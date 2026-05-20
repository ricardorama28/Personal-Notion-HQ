"""Configuracion centralizada del bot.

Lee variables de entorno una sola vez y expone constantes. Asi el resto del
codigo no consulta os.environ en cada modulo y los tests pueden inyectar
valores con monkeypatch + reload.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# ---------- Anthropic ----------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
# Router (Fase B): clasificador barato + modelo "fuerte" del orquestador.
ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "claude-haiku-4-5-20251001")
ORCHESTRATOR_MODEL = os.environ.get("ORCHESTRATOR_MODEL", CLAUDE_MODEL)
ROUTER_ENABLED = _bool("ROUTER_ENABLED", default=True)
ROUTER_CONFIDENCE_THRESHOLD = float(os.environ.get(
    "ROUTER_CONFIDENCE_THRESHOLD", "0.7"))

# ---------- Twilio ----------
MY_WHATSAPP = os.environ.get("MY_WHATSAPP", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_VALIDATE = _bool("TWILIO_VALIDATE", default=True)

# ---------- Notion ----------
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
TASKS_DB_ID = os.environ.get("TASKS_DB_ID", "")
EVENTS_DB_ID = os.environ.get("EVENTS_DB_ID", "")
PROJECTS_DB_ID = os.environ.get("PROJECTS_DB_ID", "")
NOTES_DB_ID = os.environ.get("NOTES_DB_ID", "")
EXPENSES_DB_ID = os.environ.get("EXPENSES_DB_ID", "")
MEALS_DB_ID = os.environ.get("MEALS_DB_ID", "")
HABITS_DB_ID = os.environ.get("HABITS_DB_ID", "")
HABITLOG_DB_ID = os.environ.get("HABITLOG_DB_ID", "")
INBOX_DB_ID = os.environ.get("INBOX_DB_ID", "")

# ---------- Runtime ----------
SESSIONS_FILE = Path(os.environ.get("SESSIONS_FILE", "/tmp/wpp_sessions.json"))
MAX_TOOL_ITERATIONS = int(os.environ.get("MAX_TOOL_ITERATIONS", "8"))
HISTORY_WINDOW = int(os.environ.get("HISTORY_WINDOW", "30"))
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", "4096"))
COST_LOG_FILE = Path(os.environ.get("COST_LOG_FILE", "/tmp/wpp_cost_log.jsonl"))
