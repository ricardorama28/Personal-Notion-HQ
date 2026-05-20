"""
FastAPI webhook que recibe mensajes de WhatsApp via Twilio,
los procesa con Claude + tools, y responde por TwiML.
"""
import asyncio
import hmac
import json
import logging
import re

from anthropic import Anthropic
from fastapi import BackgroundTasks, FastAPI, Form, Header, Request, Response
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

import agents
import async_runner
import config
import cost_log
import db as db_mod
import notion_ops as ops
import orchestrator
import repos
import router as wpp_router
from notion_ops import diagnostics, today_context
from orchestrator import ActionPlan
from prompts import SYSTEM
from tools import TOOLS, execute_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wpp")

app = FastAPI(
    # Default: docs deshabilitados (mas seguro detras de un tunel publico).
    # Set ENABLE_DOCS=true en dev para reactivar /docs, /redoc, /openapi.json.
    docs_url="/docs" if config.ENABLE_DOCS else None,
    redoc_url="/redoc" if config.ENABLE_DOCS else None,
    openapi_url="/openapi.json" if config.ENABLE_DOCS else None,
)
client = Anthropic()

# Fase I: Command Center web. Lazy import para que el resto de tests
# (que no tienen jinja2 instalado en algunos paths) no se rompan si
# falta dep.
try:
    from web import router as _admin_router
    app.include_router(_admin_router)
except ImportError as _e:  # pragma: no cover
    log.warning("Web UI deshabilitada: %s", _e)

if not config.MY_WHATSAPP:
    log.warning("MY_WHATSAPP no esta seteada: la app va a rechazar todos los mensajes hasta que la configures")
if config.TWILIO_VALIDATE and not config.TWILIO_AUTH_TOKEN:
    log.warning("TWILIO_VALIDATE=true pero TWILIO_AUTH_TOKEN esta vacio: vas a rechazar todos los webhooks")
if not config.TWILIO_VALIDATE:
    log.warning("⚠ TWILIO_VALIDATE=false — el webhook NO valida firma. SOLO para tests locales; "
                "NUNCA dejes esto asi cuando expongas con Cloudflare Tunnel.")
if not config.ADMIN_TOKEN:
    log.warning("ADMIN_TOKEN vacio: /health/internal y /diag devuelven 404 (deshabilitados).")


def normalize_whatsapp(s: str) -> str:
    """Normaliza un numero de WhatsApp para comparar sin importar espacios ni mayusculas en el prefijo."""
    return s.strip().replace(" ", "").lower()


def mask_sender(s: str) -> str:
    """Enmascara un numero para logs (mantiene prefijo y ultimos 2)."""
    s = (s or "").strip()
    if len(s) <= 6:
        return s
    return f"{s[:6]}…{s[-2:]}"


log.info("sessions backend=%s", config.SESSIONS_BACKEND)


def _public_url(request: Request) -> str:
    """Reconstruye la URL absoluta del webhook respetando proxies (Railway, Cloudflare)."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}{request.url.path}"


async def _validate_twilio(request: Request, form: dict) -> bool:
    """Devuelve True si la firma es valida (o si la validacion esta deshabilitada)."""
    if not config.TWILIO_VALIDATE:
        return True
    if not config.TWILIO_AUTH_TOKEN:
        return False
    signature = request.headers.get("x-twilio-signature", "")
    if not signature:
        return False
    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    return validator.validate(_public_url(request), form, signature)


def run_agent(messages: list, model: str,
              sid: str = None, intent: str = None
              ) -> tuple[str, list, dict]:
    """Loop de tool use. Devuelve (texto, mensajes, run_meta).

    run_meta = {model, input_tokens, output_tokens, iterations, tool_calls:
    [{name, args, result}]}.
    """
    system_prompt = f"{SYSTEM}\n\n{today_context()}"
    total_in = 0
    total_out = 0
    iterations = 0
    tool_invocations: list[dict] = []

    try:
        for _ in range(config.MAX_TOOL_ITERATIONS):
            iterations += 1
            response = client.messages.create(
                model=model,
                max_tokens=2048,
                system=system_prompt,
                tools=TOOLS,
                messages=messages,
            )
            total_in += getattr(response.usage, "input_tokens", 0)
            total_out += getattr(response.usage, "output_tokens", 0)

            messages.append({
                "role": "assistant",
                "content": [block.model_dump() for block in response.content],
            })

            if response.stop_reason != "tool_use":
                text = "\n".join(b.text for b in response.content if b.type == "text")
                meta = {"model": model, "input_tokens": total_in,
                        "output_tokens": total_out, "iterations": iterations,
                        "tool_calls": tool_invocations}
                return text.strip() or "✓", messages, meta

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    tool_invocations.append({"name": block.name,
                                             "args": block.input,
                                             "result": result})
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })

            messages.append({"role": "user", "content": tool_results})

        return ("Llegue al limite de iteraciones, probá reformular.",
                messages,
                {"model": model, "input_tokens": total_in,
                 "output_tokens": total_out, "iterations": iterations,
                 "tool_calls": tool_invocations})
    finally:
        cost_log.log_event(
            route=("haiku_agent" if model == config.ROUTER_MODEL else "sonnet_agent"),
            intent=intent, model=model, sid=sid,
            input_tokens=total_in, output_tokens=total_out,
            extra={"iterations": iterations},
        )


_ERROR_LINE_RE = re.compile(r"^error: ([A-Za-z_][A-Za-z0-9_]*):.*", re.DOTALL)


def _sanitize_for_persist(text: str, *, limit: int = 500) -> str:
    """Si el texto es un error de excepcion, persistimos solo el tipo (no el
    detalle, que puede incluir paths, queries, valores). Caso comun:
        'error: RuntimeError: secret leaked in message' → 'error: RuntimeError'
    El reply hacia el usuario por TwiML mantiene el detalle (lo necesita
    para debug); solo se sanitiza al guardar en `messages`.
    """
    if not text:
        return ""
    m = _ERROR_LINE_RE.match(text)
    if m:
        return f"error: {m.group(1)}"
    return text[:limit]


def _render_rule_result(intent: str, result: dict) -> str:
    """Texto corto para responder cuando el router ejecuta tools sin LLM."""
    if "error" in result:
        return f"⚠️ {result['error']}"
    if intent == "add_expense":
        amt = result.get("amount")
        name = result.get("name", "")
        return f"✓ gasto de ${amt} en {name} anotado" if amt else "✓ gasto anotado"
    if intent == "query_tasks":
        tasks = result.get("tasks", [])
        if not tasks:
            return "✓ no hay tareas en ese rango"
        lines = []
        for t in tasks[:10]:
            due = t.get("due") or "—"
            lines.append(f"• {t['name']} ({due})")
        if len(tasks) > 10:
            lines.append(f"…y {len(tasks) - 10} mas")
        return "\n".join(lines)
    return f"✓ {intent} ejecutado"


def _twiml(text: str) -> Response:
    twiml = MessagingResponse()
    twiml.message(text)
    return Response(content=str(twiml), media_type="application/xml")


async def _peek_and_pop_confirmation(session_key: str
                                     ) -> tuple[str | None, dict | None]:
    """Devuelve (confirmation_id, payload) si hay una pending vigente y la
    consume. Si no hay nada, (None, None).

    Usamos pop_latest del repo (que ya marca consumed=True y filtra
    expires_at>now). El id lo recuperamos antes del pop con una query
    paralela; si falla, devolvemos None — la idempotencia de pop_latest
    nos protege de doble consumo.
    """
    from sqlalchemy import select
    import models as M
    async with db_mod.session_scope() as s:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        stmt = (select(M.PendingConfirmation)
                .where(M.PendingConfirmation.session_key == session_key,
                       M.PendingConfirmation.consumed.is_(False),
                       M.PendingConfirmation.expires_at > now)
                .order_by(M.PendingConfirmation.created_at.desc())
                .limit(1))
        row = (await s.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None, None
        row.consumed = True
        return row.id, dict(row.payload)


async def _execute_plan(plan: ActionPlan, *, history: list, sid: str | None,
                        sender: str, confirmed_from: str | None = None
                        ) -> tuple[str, list, dict, str | None]:
    """Ejecuta un ActionPlan ya autorizado (no pide confirmacion).

    Retorna (reply, history, run_meta, agent_run_id). Persiste agent_run +
    tool_calls + cost_logs cuando backend=postgres.
    """
    run_id = await repos.agent_runs.create(
        sid=sid, session_key=sender, route=plan.route, intent=plan.intent,
        model=plan.model, plan=plan.to_json(),
        safety_level=plan.safety_level, confirmed_from=confirmed_from,
    )

    if plan.route == "rule":
        tool = plan.payload.get("tool")
        args = plan.payload.get("args") or {}
        result = execute_tool(tool, args)
        await repos.tool_calls.add(agent_run_id=run_id, sid=sid,
                                   name=tool, args=args, result=result)
        reply = _render_rule_result(plan.intent, result)
        history.append({"role": "assistant", "content": reply})
        rec = cost_log.log_event(
            route="rule", intent=plan.intent, sid=sid,
            extra={"tool": tool, "ok": "error" not in result,
                   "confirmed_from": confirmed_from},
        )
        await repos.cost_logs.add(rec)
        return reply, history, {"tool_calls": [
            {"name": tool, "args": args, "result": result}
        ]}, run_id

    # agente: especializado si plan.route lo identifica, fallback legacy
    # si es haiku_agent/sonnet_agent.
    agent = agents.get_agent(plan.route)
    if agent is not None:
        reply, history, run_meta = agent.run(
            history, anthropic_client=client, sid=sid, intent=plan.intent,
            model_override=plan.model,
        )
        cost_route = agent.name
    else:
        reply, history, run_meta = run_agent(
            history, model=plan.model or config.ORCHESTRATOR_MODEL,
            sid=sid, intent=plan.intent,
        )
        cost_route = ("haiku_agent" if run_meta.get("model") == config.ROUTER_MODEL
                      else "sonnet_agent")

    for tc in run_meta.get("tool_calls", []):
        await repos.tool_calls.add(agent_run_id=run_id, sid=sid,
                                   name=tc["name"], args=tc["args"],
                                   result=tc["result"])
    await repos.cost_logs.add({
        "ts": None, "sid": sid,
        "route": cost_route,
        "intent": plan.intent, "model": run_meta.get("model"),
        "input_tokens": run_meta.get("input_tokens", 0),
        "output_tokens": run_meta.get("output_tokens", 0),
        "iterations": run_meta.get("iterations", 0),
        "confirmed_from": confirmed_from,
        "agent": run_meta.get("agent"),
    })
    return reply, history, run_meta, run_id


@app.post("/webhook")
async def whatsapp_webhook(request: Request,
                           background_tasks: BackgroundTasks,
                           From: str = Form(...),
                           Body: str = Form(...),
                           MessageSid: str = Form(None)):
    # leer el form crudo para validar firma con TODOS los params (Twilio firma sobre todo el body)
    form_data = await request.form()
    form_dict = {k: v for k, v in form_data.items()}

    if not await _validate_twilio(request, form_dict):
        log.warning("firma Twilio invalida o ausente (sid=%r)", MessageSid)
        return Response(status_code=403, content="invalid signature")

    log.info("inbound webhook From=%s sid=%r len=%d",
             mask_sender(From), MessageSid, len(Body or ""))

    # tope de tamano
    if len(Body or "") > config.MAX_BODY_BYTES:
        log.warning("mensaje rechazado por tamano (%d bytes)", len(Body))
        return _twiml("⚠️ Mensaje demasiado largo. Mandá algo mas corto.")

    # solo respondemos a tu numero (comparacion normalizada)
    if normalize_whatsapp(From) != normalize_whatsapp(config.MY_WHATSAPP):
        log.warning("numero no autorizado: recibido=%s", mask_sender(From))
        return _twiml(
            f"⚠️ No autorizado. Recibí From={From}\n"
            f"Poné exactamente ese valor en la env var MY_WHATSAPP y redeployá."
        )

    history = await repos.sessions.load(From)

    body_norm = Body.strip().lower()
    if body_norm in {"/reset", "nuevo", "reset"}:
        await repos.sessions.clear(From)
        cost_log.log_event(route="admin", intent="reset", sid=MessageSid)
        return _twiml("✓ sesion limpia")

    if body_norm in {"/cost", "/status"}:
        if db_mod.is_postgres_enabled():
            s = await cost_log.summary_db(last_n_days=7)
        else:
            s = cost_log.summary(last_n_days=7)
        cost_log.log_event(route="admin", intent="cost_query", sid=MessageSid)
        lines = [
            f"Costo 7d: USD {s['total_usd']:.4f} ({s['events']} eventos)",
            f"Tokens in/out: {s['input_tokens']}/{s['output_tokens']}",
            f"Fuente: {s.get('source', 'jsonl')}",
        ]
        if s["by_route"]:
            lines.append("Rutas: " + ", ".join(
                f"{k}={v}" for k, v in s["by_route"].items()))
        return _twiml("\n".join(lines))

    # ----- Fase F: respuestas a confirmaciones pendientes -----
    # Solo si backend=postgres. Sin DB no hay forma de recordar el plan.
    if db_mod.is_postgres_enabled():
        conf_kind = orchestrator.is_confirmation_reply(Body)
        if conf_kind is not None:
            pending_id, pending_payload = await _peek_and_pop_confirmation(From)
            if pending_payload is not None:
                if conf_kind is False:
                    cost_log.log_event(route="admin", intent="confirm_cancel",
                                       sid=MessageSid,
                                       extra={"confirmed_from": pending_id})
                    return _twiml("✓ cancelado")
                # confirmar: rehidratar plan y ejecutar
                try:
                    plan = ActionPlan.from_json(pending_payload)
                except Exception:
                    log.exception("payload de confirmacion invalido")
                    return _twiml("⚠️ confirmacion invalida, mandá de nuevo el mensaje original")
                # defense-in-depth: unsafe nunca debe haber llegado al pop,
                # pero por las dudas no ejecutamos si lo es.
                if plan.safety_level == "unsafe":
                    log.error("plan unsafe encontrado en pop_latest "
                              "(no deberia pasar): id=%s", pending_id)
                    return _twiml(orchestrator.BLOCKED_UNSAFE_REPLY)
                history = await repos.sessions.load(From)
                history.append({"role": "user", "content": Body})
                inbox_id = None
                try:
                    inbox = ops.create_inbox_entry(Body, From, MessageSid)
                    inbox_id = inbox.get("page_id") if not inbox.get("existing") else None
                    if inbox_id:
                        ops.set_inbox(inbox_id)
                except Exception as e:
                    log.warning("inbox en confirmacion: %s", e)

                # Fase H: si el plan confirmado es async-elegible, encolar.
                if async_runner.should_run_async(plan):
                    run_id = await repos.agent_runs.create(
                        sid=MessageSid, session_key=From, route=plan.route,
                        intent=plan.intent, model=plan.model,
                        plan=plan.to_json(), safety_level=plan.safety_level,
                        confirmed_from=pending_id,
                        async_state="async_pending",
                    )
                    background_tasks.add_task(
                        async_runner.run_in_background,
                        plan_payload=plan.to_json(), sender=From,
                        sid=MessageSid, run_id=run_id, inbox_id=inbox_id,
                    )
                    ops.clear_inbox()  # el worker lo va a re-setear
                    reply = async_runner.ACK_REPLY
                    await repos.messages.add(sid=None, sender=From,
                                             body=_sanitize_for_persist(reply),
                                             direction="outbound")
                    history.append({"role": "assistant", "content": reply})
                    await repos.sessions.save(
                        From, history[-config.HISTORY_WINDOW:])
                    return _twiml(reply)

                try:
                    reply, history, run_meta, _ = await _execute_plan(
                        plan, history=history, sid=MessageSid,
                        sender=From, confirmed_from=pending_id,
                    )
                except Exception as e:
                    log.exception("error ejecutando plan confirmado")
                    reply = f"error: {type(e).__name__}: {e}"
                finally:
                    if inbox_id:
                        try:
                            ops.finalize_inbox(inbox_id, reply or "(sin respuesta)")
                        except Exception as e:
                            log.warning("finalize_inbox: %s", e)
                    ops.clear_inbox()
                await repos.messages.add(sid=None, sender=From,
                                         body=_sanitize_for_persist(reply),
                                         direction="outbound")
                await repos.sessions.save(From, history[-config.HISTORY_WINDOW:])
                return _twiml(reply)
            # Si no habia confirmacion pendiente, "1"/"si" se procesa como
            # mensaje normal (puede ser intencional).

    inbox_id = None
    try:
        inbox = ops.create_inbox_entry(Body, From, MessageSid)
        if inbox.get("existing"):
            log.info("mensaje duplicado (Twilio retry) sid=%r, se ignora", MessageSid)
            return _twiml("✓ ya recibido (no se duplicó)")
        inbox_id = inbox.get("page_id")
        ops.set_inbox(inbox_id)
    except Exception as e:
        log.warning("no se pudo registrar en Inbox: %s", e)

    # Mensaje inbound persistido (solo escribe si backend=postgres).
    await repos.messages.add(sid=MessageSid, sender=From, body=Body,
                             direction="inbound", inbox_page_id=inbox_id)

    history.append({"role": "user", "content": Body})

    reply = ""
    run_id: str | None = None
    run_meta: dict = {}
    try:
        decision = wpp_router.route(Body, client)
        log.info("router decision: route=%s intent=%s model=%s reason=%s",
                 decision.route, decision.intent, decision.model, decision.reason)

        # Cost del router (clasificador Haiku) si consumio tokens.
        if decision.router_input_tokens or decision.router_output_tokens:
            rec = cost_log.log_event(
                route="haiku_router", intent=decision.intent,
                model=config.ROUTER_MODEL, sid=MessageSid,
                input_tokens=decision.router_input_tokens,
                output_tokens=decision.router_output_tokens,
                extra={"confidence": decision.confidence,
                       "destructive": decision.destructive,
                       "reason": decision.reason},
            )
            await repos.cost_logs.add(rec)

        plan = orchestrator.plan_from_decision(decision, Body)
        log.info("plan: intent=%s route=%s safety=%s confirm=%s",
                 plan.intent, plan.route, plan.safety_level,
                 plan.needs_confirmation)

        # Politica de seguridad (post-Fase F):
        # - unsafe → BLOQUEO seco, sin confirmacion, sin tools.
        # - safe → ejecuta siempre (cualquier backend).
        # - bulk / destructive:
        #     postgres → pending_confirmation;
        #     file     → rechazo (no es seguro confirmar sin persistencia).
        if plan.safety_level == "unsafe":
            log.warning("plan UNSAFE bloqueado: intent=%s reason=%s",
                        plan.intent, plan.confirmation_reason)
            run_id = await repos.agent_runs.create(
                sid=MessageSid, session_key=From, route="blocked",
                intent=plan.intent, model=plan.model, plan=plan.to_json(),
                safety_level="unsafe",
            )
            await repos.agent_runs.finish(run_id, reply="blocked_unsafe")
            cost_log.log_event(route="blocked", intent=plan.intent,
                               sid=MessageSid,
                               extra={"safety_level": "unsafe",
                                      "reason": plan.confirmation_reason})
            reply = orchestrator.BLOCKED_UNSAFE_REPLY
            history.append({"role": "assistant", "content": reply})

        elif plan.needs_confirmation:
            # Critic-Safety: para destructive consultamos antes de pedir
            # confirmacion, SOLO si tenemos backend postgres (sino igual
            # rechazamos y no gastamos tokens del critic).
            critic_blocked = False
            if (plan.safety_level == "destructive"
                    and db_mod.is_postgres_enabled()):
                verdict = agents.review_plan(plan, Body,
                                             anthropic_client=client)
                log.info("critic verdict=%s reason=%s",
                         verdict.get("verdict"), verdict.get("reason"))
                if verdict.get("verdict") == "block":
                    critic_blocked = True
                    run_id = await repos.agent_runs.create(
                        sid=MessageSid, session_key=From, route="blocked",
                        intent=plan.intent, model=plan.model,
                        plan={**plan.to_json(), "critic": verdict},
                        safety_level="unsafe",
                    )
                    await repos.agent_runs.finish(run_id,
                                                  reply="blocked_by_critic")
                    cost_log.log_event(route="blocked", intent=plan.intent,
                                       sid=MessageSid,
                                       extra={"safety_level": "unsafe",
                                              "critic_reason":
                                                  verdict.get("reason")})
                    reply = orchestrator.BLOCKED_UNSAFE_REPLY
                    history.append({"role": "assistant", "content": reply})

            if not critic_blocked:
                if db_mod.is_postgres_enabled():
                    cid = await repos.confirmations.create(
                        session_key=From, payload=plan.to_json())
                    run_id = await repos.agent_runs.create(
                        sid=MessageSid, session_key=From,
                        route="confirm_required",
                        intent=plan.intent, model=plan.model,
                        plan=plan.to_json(),
                        safety_level=plan.safety_level,
                    )
                    await repos.agent_runs.finish(run_id,
                                                  reply="awaiting_confirmation")
                    cost_log.log_event(route="confirm_required",
                                       intent=plan.intent, sid=MessageSid,
                                       extra={"safety_level": plan.safety_level,
                                              "confirmation_id": cid})
                    reply = orchestrator.confirmation_prompt(plan)
                    history.append({"role": "assistant", "content": reply})
                else:
                    # backend=file: no podemos guardar confirmaciones; NO
                    # ejecutamos. Rechazo claro al usuario.
                    log.warning("plan needs_confirmation=true con "
                                "backend=file: rechazado (intent=%s safety=%s)",
                                plan.intent, plan.safety_level)
                    run_id = await repos.agent_runs.create(
                        sid=MessageSid, session_key=From, route="blocked",
                        intent=plan.intent, model=plan.model,
                        plan=plan.to_json(),
                        safety_level=plan.safety_level,
                    )
                    await repos.agent_runs.finish(
                        run_id, reply="blocked_no_persistence")
                    cost_log.log_event(route="blocked", intent=plan.intent,
                                       sid=MessageSid,
                                       extra={"safety_level": plan.safety_level,
                                              "reason": "needs_postgres"})
                    reply = orchestrator.NEEDS_POSTGRES_REPLY
                    history.append({"role": "assistant", "content": reply})

        else:
            # Plan safe (o post-critic OK que no requiere confirmacion).
            # Fase H: si el plan califica como async y ASYNC_ENABLED=true,
            # encolar y responder rapido. El worker cierra el Inbox y
            # manda el WhatsApp outbound cuando termina.
            if async_runner.should_run_async(plan):
                run_id = await repos.agent_runs.create(
                    sid=MessageSid, session_key=From, route=plan.route,
                    intent=plan.intent, model=plan.model,
                    plan=plan.to_json(), safety_level=plan.safety_level,
                    async_state="async_pending",
                )
                background_tasks.add_task(
                    async_runner.run_in_background,
                    plan_payload=plan.to_json(), sender=From,
                    sid=MessageSid, run_id=run_id, inbox_id=inbox_id,
                )
                reply = async_runner.ACK_REPLY
                history.append({"role": "assistant", "content": reply})
                # El worker se encarga del Inbox: el finally NO debe tocarlo.
                inbox_id = None
                # Tampoco actualizamos agent_runs al final del handler:
                # el worker hace ese update con el resultado real.
                run_id = None
            else:
                reply, history, run_meta, run_id = await _execute_plan(
                    plan, history=history, sid=MessageSid, sender=From,
                )
    except Exception as e:
        log.exception("error en orquestador/agente")
        reply = f"error: {type(e).__name__}: {e}"
        rec = cost_log.log_event(route="error", intent="exception",
                                 sid=MessageSid,
                                 extra={"error": f"{type(e).__name__}: {e}"})
        await repos.cost_logs.add(rec)
        await repos.agent_runs.finish(run_id, error=str(e))
    finally:
        if inbox_id:
            try:
                ops.finalize_inbox(inbox_id, reply or "(sin respuesta)")
            except Exception as e:
                log.warning("no se pudo cerrar la fila de Inbox: %s", e)
        ops.clear_inbox()

    await repos.agent_runs.finish(
        run_id,
        input_tokens=run_meta.get("input_tokens", 0),
        output_tokens=run_meta.get("output_tokens", 0),
        iterations=run_meta.get("iterations", 0),
        reply=reply,
    )

    # outbound persistido (solo si backend=postgres) — sanitizado para no
    # guardar detalles de excepcion en `messages.body`. El SID es del
    # inbound de Twilio; outbound no lleva sid (es respuesta nuestra),
    # asi no chocamos con UNIQUE(messages.sid).
    await repos.messages.add(sid=None, sender=From,
                             body=_sanitize_for_persist(reply),
                             direction="outbound")

    await repos.sessions.save(From, history[-config.HISTORY_WINDOW:])

    return _twiml(reply)


def _check_admin_token(provided: str) -> bool:
    """True si ADMIN_TOKEN esta seteado y matchea (constant-time)."""
    expected = config.ADMIN_TOKEN
    if not expected:
        return False
    return hmac.compare_digest(provided or "", expected)


async def _detailed_health() -> dict:
    db_ok: bool | None = None
    db_error: str | None = None
    if db_mod.is_postgres_enabled():
        try:
            await asyncio.wait_for(db_mod.ping(), timeout=1.5)
            db_ok = True
        except Exception as e:
            db_ok = False
            db_error = type(e).__name__
            log.warning("DB ping fallo: %s", e)
    return {
        "ok": True if db_ok is not False else False,
        "my_whatsapp_set": bool(config.MY_WHATSAPP),
        "anthropic_key_set": bool(config.ANTHROPIC_API_KEY),
        "notion_token_set": bool(config.NOTION_TOKEN),
        "twilio_validate": config.TWILIO_VALIDATE,
        "twilio_auth_token_set": bool(config.TWILIO_AUTH_TOKEN),
        "sessions_backend": config.SESSIONS_BACKEND,
        "database_url_set": bool(config.DATABASE_URL),
        "database_ok": db_ok,
        "database_error": db_error,
    }


@app.get("/health")
async def health():
    """Endpoint publico minimo. Confirma que el proceso responde.

    NO expone backend, tokens ni estado de DB — eso vive en
    /health/internal protegido por ADMIN_TOKEN. Asi un atacante que
    encuentre la URL del tunel no obtiene mapa de la app.
    """
    return {"ok": True}


@app.get("/health/internal")
async def health_internal(
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    """Detalle completo de health. Protegido por ADMIN_TOKEN.

    Devuelve 404 si el token no matchea, para no revelar la existencia
    del endpoint a scanners.
    """
    if not _check_admin_token(x_admin_token):
        return Response(status_code=404, content="not found")
    return await _detailed_health()


@app.get("/diag")
async def diag(
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
):
    """Probes de lectura contra Notion. Protegido por ADMIN_TOKEN."""
    if not _check_admin_token(x_admin_token):
        return Response(status_code=404, content="not found")
    return diagnostics()
