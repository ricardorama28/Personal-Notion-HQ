"""
Setup global de tests.

- Inyecta envs falsas ANTES de importar la app (config.py se lee en import).
- Mockea notion_client.Client para que no haga llamadas reales.
- Mockea anthropic.Anthropic en main.py para no llamar a la API.
"""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

# 1. Envs falsas ANTES de importar nada del proyecto.
os.environ.setdefault("NOTION_TOKEN", "secret_test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("MY_WHATSAPP", "whatsapp:+5491100000000")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test_twilio_token")
os.environ["TWILIO_VALIDATE"] = "false"
os.environ.setdefault("ENABLE_DOCS", "false")
os.environ.setdefault("TASKS_DB_ID", "db_tasks")
os.environ.setdefault("EVENTS_DB_ID", "db_events")
os.environ.setdefault("PROJECTS_DB_ID", "db_projects")
os.environ.setdefault("NOTES_DB_ID", "db_notes")
os.environ.setdefault("EXPENSES_DB_ID", "db_expenses")
os.environ.setdefault("MEALS_DB_ID", "db_meals")
os.environ.setdefault("HABITS_DB_ID", "db_habits")
os.environ.setdefault("HABITLOG_DB_ID", "db_habitlog")
os.environ.setdefault("INBOX_DB_ID", "db_inbox")
os.environ["SESSIONS_FILE"] = str(Path("/tmp/wpp_sessions_test.json"))

# 2. Path del proyecto.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def fake_notion(monkeypatch):
    """Reemplaza el cliente notion en notion_ops por un MagicMock controlable."""
    import notion_ops
    mock = MagicMock(name="notion_client")
    # defaults razonables para que list_projects/list_habits no exploten
    mock.databases.query.return_value = {"results": []}
    mock.pages.create.return_value = {"id": "page_fake_id",
                                      "url": "https://notion.so/fake"}
    mock.pages.update.return_value = {"id": "page_fake_id"}
    monkeypatch.setattr(notion_ops, "notion", mock)
    # limpiar caches lru_cache
    notion_ops._projects_index.cache_clear()
    notion_ops._habits_index.cache_clear()
    notion_ops.clear_inbox()
    return mock


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient de FastAPI con Anthropic mockeado."""
    from fastapi.testclient import TestClient

    # sesiones a tmp para no chocar entre tests
    sessions_file = tmp_path / "sessions.json"
    monkeypatch.setenv("SESSIONS_FILE", str(sessions_file))
    import config
    config.SESSIONS_FILE = sessions_file

    import main
    fake_anthropic = MagicMock(name="anthropic_client")
    monkeypatch.setattr(main, "client", fake_anthropic)
    return TestClient(main.app), fake_anthropic


@pytest_asyncio.fixture
async def pg_db(monkeypatch):
    """Backend postgres con SQLite-aiosqlite en memoria para tests.

    Cambia SESSIONS_BACKEND=postgres, crea el schema via metadata y
    devuelve el modulo db para que el test pueda inspeccionar.
    """
    import config
    import db as db_mod
    monkeypatch.setattr(config, "SESSIONS_BACKEND", "postgres")
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    # forzar engine nuevo
    await db_mod.dispose()
    await db_mod.create_all_for_tests()
    yield db_mod
    await db_mod.dispose()
