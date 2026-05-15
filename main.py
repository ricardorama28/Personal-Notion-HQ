"""
FastAPI webhook que recibe mensajes de WhatsApp via Twilio,
los procesa con Claude + tools, y responde por TwiML.
"""
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from anthropic import Anthropic
from fastapi import FastAPI, Form, Response
from twilio.twiml.messaging_response import MessagingResponse

from prompts import SYSTEM
from tools import TOOLS, execute_tool
from notion_ops import today_context, diagnostics

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wpp")

app = FastAPI()
client = Anthropic()

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
MY_NUMBER = os.environ.get("MY_WHATSAPP", "")
SESSIONS_FILE = Path(os.environ.get("SESSIONS_FILE", "/tmp/wpp_sessions.json"))
MAX_TOOL_ITERATIONS = 8
HISTORY_WINDOW = 30

if not MY_NUMBER:
    log.warning("MY_WHATSAPP no esta seteada: la app va a rechazar todos los mensajes hasta que la configures")


def normalize_whatsapp(s: str) -> str:
    """Normaliza un numero de WhatsApp para comparar sin importar espacios ni mayusculas en el prefijo."""
    return s.strip().replace(" ", "").lower()


def load_sessions() -> dict:
    if SESSIONS_FILE.exists():
        try:
            return json.loads(SESSIONS_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_sessions(sessions: dict) -> None:
    SESSIONS_FILE.write_text(json.dumps(sessions, ensure_ascii=False))


def run_agent(messages: list) -> tuple[str, list]:
    """
    Loop de tool use. Retorna (texto_final, mensajes_actualizados).
    """
    system_prompt = f"{SYSTEM}\n\n{today_context()}"

    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        # agregar respuesta del modelo al historial (serializar los content
        # blocks del SDK a dicts para que sean JSON-serializables al guardar)
        messages.append({
            "role": "assistant",
            "content": [block.model_dump() for block in response.content],
        })

        if response.stop_reason != "tool_use":
            # respuesta final
            text = "\n".join(b.text for b in response.content if b.type == "text")
            return text.strip() or "✓", messages

        # ejecutar todas las tools que pidio
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


@app.post("/webhook")
async def whatsapp_webhook(From: str = Form(...), Body: str = Form(...)):
    log.info("inbound webhook From=%r Body=%r", From, Body[:80])

    # solo respondemos a tu numero (comparacion normalizada)
    if normalize_whatsapp(From) != normalize_whatsapp(MY_NUMBER):
        log.warning(
            "numero no autorizado: recibido=%r esperado(MY_WHATSAPP)=%r",
            From, MY_NUMBER,
        )
        twiml = MessagingResponse()
        twiml.message(
            f"⚠️ No autorizado. Recibí From={From}\n"
            f"Poné exactamente ese valor en la env var MY_WHATSAPP y redeployá."
        )
        return Response(content=str(twiml), media_type="application/xml")

    sessions = load_sessions()
    history = sessions.get(From, [])

    # comando para resetear
    if Body.strip().lower() in {"/reset", "nuevo", "reset"}:
        sessions[From] = []
        save_sessions(sessions)
        twiml = MessagingResponse()
        twiml.message("✓ sesion limpia")
        return Response(content=str(twiml), media_type="application/xml")

    history.append({"role": "user", "content": Body})

    try:
        reply, history = run_agent(history)
    except Exception as e:
        reply = f"error: {type(e).__name__}: {e}"

    # truncar historial (mantener pares completos)
    sessions[From] = history[-HISTORY_WINDOW:]
    save_sessions(sessions)

    twiml = MessagingResponse()
    twiml.message(reply)
    return Response(content=str(twiml), media_type="application/xml")


@app.get("/health")
async def health():
    return {
        "ok": True,
        "my_whatsapp_set": bool(MY_NUMBER),
        "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "notion_token_set": bool(os.environ.get("NOTION_TOKEN")),
    }


@app.get("/diag")
async def diag():
    """Probes de lectura contra Notion para diagnosticar permisos de la integracion."""
    return diagnostics()
