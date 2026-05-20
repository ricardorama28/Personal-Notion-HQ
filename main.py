"""
FastAPI webhook que recibe mensajes de WhatsApp via Twilio,
los procesa con Claude + tools, y responde por TwiML.
"""
import json
import logging

from anthropic import Anthropic
from fastapi import FastAPI, Form, Request, Response
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

import config
import notion_ops as ops
from notion_ops import diagnostics, today_context
from prompts import SYSTEM
from tools import TOOLS, execute_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wpp")

app = FastAPI()
client = Anthropic()

if not config.MY_WHATSAPP:
    log.warning("MY_WHATSAPP no esta seteada: la app va a rechazar todos los mensajes hasta que la configures")
if config.TWILIO_VALIDATE and not config.TWILIO_AUTH_TOKEN:
    log.warning("TWILIO_VALIDATE=true pero TWILIO_AUTH_TOKEN esta vacio: vas a rechazar todos los webhooks")


def normalize_whatsapp(s: str) -> str:
    """Normaliza un numero de WhatsApp para comparar sin importar espacios ni mayusculas en el prefijo."""
    return s.strip().replace(" ", "").lower()


def mask_sender(s: str) -> str:
    """Enmascara un numero para logs (mantiene prefijo y ultimos 2)."""
    s = (s or "").strip()
    if len(s) <= 6:
        return s
    return f"{s[:6]}…{s[-2:]}"


def load_sessions() -> dict:
    if config.SESSIONS_FILE.exists():
        try:
            return json.loads(config.SESSIONS_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_sessions(sessions: dict) -> None:
    config.SESSIONS_FILE.write_text(json.dumps(sessions, ensure_ascii=False))


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


def run_agent(messages: list) -> tuple[str, list]:
    """Loop de tool use. Retorna (texto_final, mensajes_actualizados)."""
    system_prompt = f"{SYSTEM}\n\n{today_context()}"

    for _ in range(config.MAX_TOOL_ITERATIONS):
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=2048,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        messages.append({
            "role": "assistant",
            "content": [block.model_dump() for block in response.content],
        })

        if response.stop_reason != "tool_use":
            text = "\n".join(b.text for b in response.content if b.type == "text")
            return text.strip() or "✓", messages

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

        messages.append({"role": "user", "content": tool_results})

    return "Llegue al limite de iteraciones, probá reformular.", messages


def _twiml(text: str) -> Response:
    twiml = MessagingResponse()
    twiml.message(text)
    return Response(content=str(twiml), media_type="application/xml")


@app.post("/webhook")
async def whatsapp_webhook(request: Request,
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

    sessions = load_sessions()
    history = sessions.get(From, [])

    if Body.strip().lower() in {"/reset", "nuevo", "reset"}:
        sessions[From] = []
        save_sessions(sessions)
        return _twiml("✓ sesion limpia")

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

    history.append({"role": "user", "content": Body})

    reply = ""
    try:
        reply, history = run_agent(history)
    except Exception as e:
        log.exception("error en run_agent")
        reply = f"error: {type(e).__name__}: {e}"
    finally:
        if inbox_id:
            try:
                ops.finalize_inbox(inbox_id, reply or "(sin respuesta)")
            except Exception as e:
                log.warning("no se pudo cerrar la fila de Inbox: %s", e)
        ops.clear_inbox()

    sessions[From] = history[-config.HISTORY_WINDOW:]
    save_sessions(sessions)

    return _twiml(reply)


@app.get("/health")
async def health():
    return {
        "ok": True,
        "my_whatsapp_set": bool(config.MY_WHATSAPP),
        "anthropic_key_set": bool(config.ANTHROPIC_API_KEY),
        "notion_token_set": bool(config.NOTION_TOKEN),
        "twilio_validate": config.TWILIO_VALIDATE,
        "twilio_auth_token_set": bool(config.TWILIO_AUTH_TOKEN),
    }


@app.get("/diag")
async def diag():
    """Probes de lectura contra Notion para diagnosticar permisos de la integracion."""
    return diagnostics()
