"""Pipeline de chat para la UI web.

Reusa: router → orchestrator → ActionPlan → agents/tools.

Diferencias con el handler de WhatsApp (`main.py`):
- No hay firma Twilio que validar.
- No hay TwiML; el reply va inline en HTTP.
- Session keys con prefijo `web:` y source="web" en sessions.
- No se manda outbound por Twilio (no aplica al canal web).
- Si el plan necesita confirmacion: se persiste el pending igual y el
  reply contiene el prompt. El proximo POST del user al mismo session_key
  con "1"/"cancelar" lo consume.

Para mantenerlo simple, el chat web hoy ejecuta TODO sincronicamente
(incluso si ASYNC_ENABLED). Si el plan es async-eligible, se ejecuta
inline igual y devuelve el reply en la respuesta. Mostrar progreso live
queda para una iteracion futura via SSE/polling.
"""
import json
import logging
import re
import uuid

import config
import cost_log
import db as db_mod
import notion_ops as ops
import orchestrator
import repos
import router as wpp_router
from orchestrator import ActionPlan

log = logging.getLogger("wpp.web.chat")


def new_session_key() -> str:
    return f"web:{uuid.uuid4().hex[:12]}"


_ERROR_LINE_RE = re.compile(r"^error: ([A-Za-z_][A-Za-z0-9_]*):.*", re.DOTALL)


def _sanitize_for_persist(text: str, limit: int = 500) -> str:
    if not text:
        return ""
    m = _ERROR_LINE_RE.match(text)
    if m:
        return f"error: {m.group(1)}"
    return text[:limit]


async def send_message(*, session_key: str, body: str, anthropic_client,
                       background_tasks=None) -> dict:
    """Procesa un mensaje del usuario en el chat web.

    Retorna dict con campos para renderizar la UI:
      {reply, plan, run_id, route, intent, safety_level,
       tool_calls, needs_confirmation, confirmation_id, async_dispatched,
       error}

    Si `ASYNC_ENABLED=true`, el plan es async-eligible y se paso
    `background_tasks`, encolamos en lugar de ejecutar inline. El reply
    devuelto es el ACK; el resultado real va al `agent_run` y se puede
    refrescar desde la UI.
    """
    body = (body or "").strip()
    if not body:
        return {"reply": "", "error": "mensaje vacio"}

    # Carga history.
    history = await repos.sessions.load(session_key)

    # 1) Confirmacion pendiente?
    if db_mod.is_postgres_enabled():
        kind = orchestrator.is_confirmation_reply(body)
        if kind is not None:
            from main import _peek_and_pop_confirmation, _execute_plan
            pending_id, payload = await _peek_and_pop_confirmation(session_key)
            if payload is not None:
                if kind is False:
                    history.append({"role": "user", "content": body})
                    history.append({"role": "assistant",
                                    "content": "✓ cancelado"})
                    await repos.sessions.save(session_key, history, source="web")
                    return {"reply": "✓ cancelado",
                            "plan": payload, "route": "confirm_cancel",
                            "intent": payload.get("intent")}
                try:
                    plan = ActionPlan.from_json(payload)
                except Exception as e:
                    return {"reply": f"⚠️ confirmacion invalida ({type(e).__name__})",
                            "error": "invalid_payload"}
                history.append({"role": "user", "content": body})
                reply, history, run_meta, run_id = await _execute_plan(
                    plan, history=history, sid=None, sender=session_key,
                    confirmed_from=pending_id,
                )
                await repos.messages.add(sid=None, sender=session_key,
                                         body=_sanitize_for_persist(reply),
                                         direction="outbound")
                await repos.sessions.save(session_key,
                                          history[-config.HISTORY_WINDOW:],
                                          source="web")
                return {"reply": reply, "plan": plan.to_json(),
                        "route": plan.route, "intent": plan.intent,
                        "safety_level": plan.safety_level,
                        "run_id": run_id,
                        "tool_calls": run_meta.get("tool_calls", []),
                        "confirmed_from": pending_id}
            # sin pending, sigue al flujo normal con "1" como mensaje normal

    # 2) Inbound message persistido.
    await repos.messages.add(sid=None, sender=session_key, body=body,
                             direction="inbound")

    history.append({"role": "user", "content": body})

    try:
        decision = wpp_router.route(body, anthropic_client)
        if decision.router_input_tokens or decision.router_output_tokens:
            rec = cost_log.log_event(
                route="haiku_router", intent=decision.intent,
                model=config.ROUTER_MODEL,
                input_tokens=decision.router_input_tokens,
                output_tokens=decision.router_output_tokens,
                extra={"confidence": decision.confidence,
                       "destructive": decision.destructive,
                       "reason": decision.reason, "source": "web"},
            )
            await repos.cost_logs.add(rec)
        plan = orchestrator.plan_from_decision(decision, body)

        # Politica de seguridad (mismo que main):
        if plan.safety_level == "unsafe":
            run_id = await repos.agent_runs.create(
                sid=None, session_key=session_key, route="blocked",
                intent=plan.intent, model=plan.model, plan=plan.to_json(),
                safety_level="unsafe",
            )
            await repos.agent_runs.finish(run_id, reply="blocked_unsafe")
            reply = orchestrator.BLOCKED_UNSAFE_REPLY
            history.append({"role": "assistant", "content": reply})
            await repos.sessions.save(session_key,
                                      history[-config.HISTORY_WINDOW:],
                                      source="web")
            return {"reply": reply, "plan": plan.to_json(),
                    "route": "blocked",
                    "intent": plan.intent, "safety_level": "unsafe",
                    "run_id": run_id}

        if plan.needs_confirmation:
            if not db_mod.is_postgres_enabled():
                reply = orchestrator.NEEDS_POSTGRES_REPLY
                history.append({"role": "assistant", "content": reply})
                await repos.sessions.save(session_key, history, source="web")
                return {"reply": reply, "plan": plan.to_json(),
                        "route": "blocked",
                        "intent": plan.intent, "needs_confirmation": True}
            cid = await repos.confirmations.create(
                session_key=session_key, payload=plan.to_json())
            run_id = await repos.agent_runs.create(
                sid=None, session_key=session_key, route="confirm_required",
                intent=plan.intent, model=plan.model, plan=plan.to_json(),
                safety_level=plan.safety_level,
            )
            await repos.agent_runs.finish(run_id, reply="awaiting_confirmation")
            reply = orchestrator.confirmation_prompt(plan)
            history.append({"role": "assistant", "content": reply})
            await repos.messages.add(sid=None, sender=session_key,
                                     body=reply, direction="outbound")
            await repos.sessions.save(session_key,
                                      history[-config.HISTORY_WINDOW:],
                                      source="web")
            return {"reply": reply, "plan": plan.to_json(),
                    "route": plan.route, "intent": plan.intent,
                    "safety_level": plan.safety_level,
                    "needs_confirmation": True, "confirmation_id": cid,
                    "run_id": run_id}

        # Async branch (Fase H + I hardening): si el plan es async-eligible
        # y la UI nos paso un BackgroundTasks, encolamos y devolvemos ACK.
        import async_runner
        if (background_tasks is not None
                and async_runner.should_run_async(plan)):
            run_id = await repos.agent_runs.create(
                sid=None, session_key=session_key, route=plan.route,
                intent=plan.intent, model=plan.model,
                plan=plan.to_json(), safety_level=plan.safety_level,
                async_state="async_pending",
            )
            background_tasks.add_task(
                async_runner.run_in_background,
                plan_payload=plan.to_json(), sender=session_key,
                sid=None, run_id=run_id, inbox_id=None,
            )
            reply = async_runner.ACK_REPLY
            history.append({"role": "assistant", "content": reply})
            await repos.messages.add(sid=None, sender=session_key, body=reply,
                                     direction="outbound")
            await repos.sessions.save(session_key,
                                      history[-config.HISTORY_WINDOW:],
                                      source="web")
            return {"reply": reply, "plan": plan.to_json(),
                    "route": plan.route, "intent": plan.intent,
                    "safety_level": plan.safety_level,
                    "model": plan.model,
                    "run_id": run_id,
                    "async_dispatched": True,
                    "async_state": "async_pending"}

        from main import _execute_plan
        reply, history, run_meta, run_id = await _execute_plan(
            plan, history=history, sid=None, sender=session_key,
        )
        await repos.messages.add(sid=None, sender=session_key,
                                 body=_sanitize_for_persist(reply),
                                 direction="outbound")
        await repos.sessions.save(session_key,
                                  history[-config.HISTORY_WINDOW:],
                                  source="web")
        return {"reply": reply, "plan": plan.to_json(),
                "route": plan.route, "intent": plan.intent,
                "safety_level": plan.safety_level,
                "model": run_meta.get("model"),
                "run_id": run_id,
                "tool_calls": run_meta.get("tool_calls", [])}
    except Exception as e:
        log.exception("error pipeline web")
        return {"reply": f"⚠️ error ({type(e).__name__})",
                "error": type(e).__name__}


async def approve_confirmation(*, session_key: str, confirmation_id: str,
                               anthropic_client) -> dict:
    """Aprueba la confirmacion latente del usuario. Devuelve mismo shape
    que send_message."""
    return await send_message(session_key=session_key, body="1",
                              anthropic_client=anthropic_client)


async def cancel_confirmation(session_key: str) -> dict:
    return await send_message(session_key=session_key, body="cancelar",
                              anthropic_client=None)
