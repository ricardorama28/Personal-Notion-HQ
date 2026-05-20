"""Ejecutor async via FastAPI BackgroundTasks (Fase H).

Politica:
- ASYNC_ENABLED=false → todo sincrono (default).
- ASYNC_ENABLED=true + plan ruta planner_agent/writer_agent/research_agent
  o intent en {plan, reorganize, write, research} o plan.async_required=True
  → encolar.

El webhook responde rapido ("✓ recibí, te respondo en un toque"); el
worker corre el plan en background y manda el resultado por Twilio
outbound. Si Twilio outbound no esta configurado, el resultado queda en
agent_runs igual y un /cost o query a la DB lo recupera.

Limitaciones de BackgroundTasks:
- vive en el mismo proceso (si crashea uvicorn, se pierde);
- sin retry automatico;
- sin cola persistente — no escalable horizontal.
Cuando crezca: portar `run_in_background` a `rq.Queue.enqueue(...)` con
el mismo signature; los repos y twilio_outbound no cambian.
"""
import logging
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from orchestrator import ActionPlan

log = logging.getLogger("wpp.async")


# Agentes que tipicamente toman >5s — los mandamos async cuando se puede.
ASYNC_AGENT_ROUTES = {"planner_agent", "writer_agent", "research_agent"}
ASYNC_INTENTS = {"plan", "reorganize", "reorganizar", "write", "redactar",
                 "research", "investigar"}


def should_run_async(plan: "ActionPlan") -> bool:
    """Decide si un ActionPlan se debe ejecutar en background.

    Regla:
      ASYNC_ENABLED=true AND (
          plan.async_required
          OR plan.route ∈ ASYNC_AGENT_ROUTES
          OR plan.intent ∈ ASYNC_INTENTS
      )
    """
    if not config.ASYNC_ENABLED:
        return False
    if plan.async_required:
        return True
    if plan.route in ASYNC_AGENT_ROUTES:
        return True
    if (plan.intent or "") in ASYNC_INTENTS:
        return True
    return False


async def run_in_background(*, plan_payload: dict, sender: str,
                            sid: str | None, run_id: str | None,
                            inbox_id: str | None) -> None:
    """Ejecuta el plan en background.

    Pasos:
      1. marca async_running en agent_runs;
      2. carga history desde sessions repo;
      3. ejecuta plan via main._execute_plan (lazy import para evitar
         circular);
      4. en finally: marca async_done o async_error, finaliza Inbox,
         persiste outbound message + session, manda WhatsApp si hay
         outbound configurado;
      5. nunca propaga excepciones — el background task no debe romper
         el server.
    """
    # Lazy imports: estos modulos crean instancias top-level (Anthropic,
    # FastAPI) que no queremos cargar al importar async_runner desde test.
    import notion_ops as ops
    import repos
    import twilio_outbound
    from main import _execute_plan, _sanitize_for_persist, client as anthropic
    from orchestrator import ActionPlan
    import cost_log

    try:
        plan = ActionPlan.from_json(plan_payload)
    except Exception as e:
        log.warning("async: payload invalido: %s", e)
        await repos.agent_runs.finish(
            run_id, error=f"invalid_payload:{type(e).__name__}")
        return

    log.info("async: arrancando run_id=%s route=%s intent=%s sid=%r",
             run_id, plan.route, plan.intent, sid)

    await _set_async_state(run_id, "async_running")

    reply = ""
    history = await repos.sessions.load(sender)
    history.append({"role": "user", "content": plan.payload.get("user_text",
                                                                "")})

    # Restablecer contexto de Inbox para que tools relacionen contra esa fila.
    if inbox_id:
        ops.set_inbox(inbox_id)

    try:
        reply, history, _run_meta, _ = await _execute_plan(
            plan, history=history, sid=sid, sender=sender,
        )
        await _set_async_state(run_id, "async_done")
    except Exception as e:
        log.exception("async: error en _execute_plan")
        reply = f"⚠ no pude terminar la accion ({type(e).__name__})"
        await _set_async_state(run_id, "async_error",
                               error=f"{type(e).__name__}: {e}")
        cost_log.log_event(route="async_error", intent=plan.intent, sid=sid,
                           extra={"error": type(e).__name__})
    finally:
        if inbox_id:
            try:
                ops.finalize_inbox(inbox_id, reply or "(sin respuesta)")
            except Exception as e:
                log.warning("async: finalize_inbox fallo: %s", e)
        ops.clear_inbox()

    # Persistir outbound (sin SID Twilio para no chocar con UNIQUE).
    try:
        await repos.messages.add(sid=None, sender=sender,
                                 body=_sanitize_for_persist(reply),
                                 direction="outbound")
        await repos.sessions.save(sender, history[-config.HISTORY_WINDOW:])
    except Exception as e:
        log.warning("async: persistencia post-run fallo: %s", e)

    # Outbound WhatsApp. Failure soft: si Twilio outbound no esta listo,
    # el resultado ya quedo en DB y queda accesible.
    try:
        twilio_outbound.send(to=sender, body=reply)
    except Exception as e:
        log.warning("async: twilio outbound fallo: %s", e)


async def _set_async_state(run_id: str | None, state: str,
                           error: str | None = None) -> None:
    """Updatea agent_runs.async_state. Si run_id es None, no-op."""
    if not run_id:
        return
    import db
    import models
    from sqlalchemy import update
    if not db.is_postgres_enabled():
        return
    async with db.session_scope() as s:
        await s.execute(
            update(models.AgentRun)
            .where(models.AgentRun.id == run_id)
            .values(async_state=state,
                    **({"error": error} if error else {}))
        )


# Reply rapida al usuario cuando se encola async.
ACK_REPLY = "✓ recibí, te respondo en un toque cuando termine."
