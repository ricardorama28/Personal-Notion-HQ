"""
FastAPI webhook que recibe mensajes de WhatsApp via Twilio,
los procesa con Claude + tools, y responde por TwiML.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from anthropic import Anthropic
from fastapi import FastAPI, Form, HTTPException, Response
from twilio.twiml.messaging_response import MessagingResponse

from prompts import SYSTEM
from tools import TOOLS, execute_tool
from notion_ops import today_context

app = FastAPI()
client = Anthropic()

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
MY_NUMBER = os.environ["MY_WHATSAPP"]
SESSIONS_FILE = Path(os.environ.get("SESSIONS_FILE", "/tmp/wpp_sessions.json"))
MAX_TOOL_ITERATIONS = 8
HISTORY_WINDOW = 30


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

        # agregar respuesta del modelo al historial
        messages.append({"role": "assistant", "content": response.content})

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
    # solo respondemos a tu numero
    if From != MY_NUMBER:
        raise HTTPException(status_code=403, detail="forbidden")

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
    return {"ok": True}
