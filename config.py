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
# Fase H: outbound async. ACCOUNT_SID + FROM_WHATSAPP son necesarios SOLO
# si el worker async va a mandar mensajes posteriores. Si faltan, el
# worker registra el resultado en DB pero no manda WhatsApp.
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_FROM_WHATSAPP = os.environ.get("TWILIO_FROM_WHATSAPP", "")

# ---------- Async workers (Fase H) ----------
# Default false: el bot sigue 100% sincrono. Si lo prendes, ciertos
# planes (PlannerAgent, WriterAgent, ResearchAgent) se ejecutan en
# BackgroundTasks; el webhook responde rapido y el resultado va por
# WhatsApp outbound cuando termina.
ASYNC_ENABLED = _bool("ASYNC_ENABLED", default=False)

# ---------- Admin ----------
# Token compartido para endpoints sensibles (/health/internal, /diag).
# Vacio = endpoints deshabilitados (404). Asi el default es el mas seguro.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

# /docs, /redoc y /openapi.json de FastAPI. Default: deshabilitados (mas
# seguro en self-hosted con tunel publico). En dev local, ENABLE_DOCS=true.
ENABLE_DOCS = _bool("ENABLE_DOCS", default=False)

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

# ---------- Persistencia (Fase C) ----------
# Backend de sesiones: "file" (default, /tmp JSON) o "postgres".
SESSIONS_BACKEND = os.environ.get("SESSIONS_BACKEND", "file").strip().lower()
# URL SQLAlchemy async. Ej: postgresql+asyncpg://user:pass@host:5432/db
# Para tests: sqlite+aiosqlite:///:memory:
DATABASE_URL = os.environ.get("DATABASE_URL", "")
# TTL default para pending_confirmations (minutos).
CONFIRMATION_TTL_MINUTES = int(os.environ.get("CONFIRMATION_TTL_MINUTES", "10"))

if SESSIONS_BACKEND == "postgres" and not DATABASE_URL:
    raise RuntimeError(
        "SESSIONS_BACKEND=postgres pero DATABASE_URL no esta seteada. "
        "Configurala (postgresql+asyncpg://...) o usá SESSIONS_BACKEND=file."
    )

# ---------- Runtime ----------
MAX_TOOL_ITERATIONS = int(os.environ.get("MAX_TOOL_ITERATIONS", "8"))
HISTORY_WINDOW = int(os.environ.get("HISTORY_WINDOW", "30"))
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", "4096"))


def _default_path(env_name: str, filename: str) -> Path:
    """Si la env esta seteada, usarla. Si no, preferir /data (volumen
    persistente en Docker); cae a /tmp si /data no existe / no es escribible.
    Asi Railway y dev local siguen igual; Compose monta /data y funciona sin
    config extra."""
    raw = os.environ.get(env_name)
    if raw:
        return Path(raw)
    data_dir = Path("/data")
    if data_dir.is_dir() and os.access(data_dir, os.W_OK):
        return data_dir / filename
    return Path("/tmp") / filename


SESSIONS_FILE = _default_path("SESSIONS_FILE", "wpp_sessions.json")
COST_LOG_FILE = _default_path("COST_LOG_FILE", "wpp_cost_log.jsonl")
