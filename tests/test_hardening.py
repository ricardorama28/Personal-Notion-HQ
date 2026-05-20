"""Tests de los endurecimientos previos a Fase D:
- /cost desde SQL cuando backend=postgres.
- /health con ping a DB.
- Defaults de archivos persistentes (/data fallback /tmp).
- Sanitizacion de outbound message.
"""
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------- Defaults de paths ----------

def test_default_path_uses_env_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSIONS_FILE", str(tmp_path / "x.json"))
    import config, importlib
    importlib.reload(config)
    assert config.SESSIONS_FILE == tmp_path / "x.json"
    monkeypatch.delenv("SESSIONS_FILE", raising=False)
    importlib.reload(config)


def test_default_path_falls_back_to_tmp_when_no_data(monkeypatch):
    """Sin /data writable y sin env, el default es /tmp."""
    monkeypatch.delenv("SESSIONS_FILE", raising=False)
    monkeypatch.delenv("COST_LOG_FILE", raising=False)
    import config, importlib
    importlib.reload(config)
    # En CI/test no hay /data accesible al usuario test
    if not Path("/data").exists() or not os.access("/data", os.W_OK):
        assert config.SESSIONS_FILE == Path("/tmp/wpp_sessions.json")
        assert config.COST_LOG_FILE == Path("/tmp/wpp_cost_log.jsonl")


# ---------- Sanitizacion de outbound ----------

def test_sanitize_strips_exception_detail():
    import main
    s = main._sanitize_for_persist(
        "error: RuntimeError: secret_token=abc123 leaked in body")
    assert s == "error: RuntimeError"


def test_sanitize_keeps_normal_reply():
    import main
    s = main._sanitize_for_persist("✓ gasto de $450 en super anotado")
    assert s.startswith("✓ gasto")


def test_sanitize_truncates_long_reply():
    import main
    long = "x" * 1000
    assert len(main._sanitize_for_persist(long)) == 500


# ---------- /cost desde SQL ----------

@pytest.mark.asyncio(loop_scope="function")
async def test_cost_summary_db_reads_from_postgres(pg_db, monkeypatch):
    import cost_log, repos
    await repos.cost_logs.add({
        "ts": None, "sid": "S1", "route": "rule",
        "intent": "add_expense", "model": None,
        "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
    })
    await repos.cost_logs.add({
        "ts": None, "sid": "S2", "route": "sonnet_agent",
        "intent": "plan", "model": "claude-sonnet-4-6",
        "input_tokens": 1000, "output_tokens": 200, "cost_usd": 0.006,
    })
    s = await cost_log.summary_db(last_n_days=7)
    assert s["source"] == "postgres"
    assert s["events"] == 2
    assert s["input_tokens"] == 1000
    assert s["by_route"] == {"rule": 1, "sonnet_agent": 1}


@pytest.mark.asyncio(loop_scope="function")
async def test_cost_summary_db_falls_back_to_jsonl_without_pg(tmp_path,
                                                              monkeypatch):
    import config, cost_log
    monkeypatch.setattr(config, "SESSIONS_BACKEND", "file")
    monkeypatch.setattr(config, "COST_LOG_FILE", tmp_path / "c.jsonl")
    cost_log.log_event(route="rule", intent="x")
    s = await cost_log.summary_db(last_n_days=7)
    assert s["source"] == "jsonl"
    assert s["events"] == 1


def test_cost_endpoint_shows_source_jsonl(client, fake_notion):
    tc, _ = client
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "/cost", "MessageSid": "SMc1"})
    assert r.status_code == 200
    assert "Fuente:" in r.text


@pytest.mark.asyncio(loop_scope="function")
async def test_cost_endpoint_uses_db_when_postgres(client, fake_notion,
                                                   pg_db):
    """Con backend postgres activo, /cost lee de la tabla."""
    tc, _ = client
    # cargar una fila para que /cost devuelva source=postgres con datos
    import repos
    await repos.cost_logs.add({
        "ts": None, "sid": "S0", "route": "rule",
        "intent": "add_expense", "model": None,
        "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
    })
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "/cost", "MessageSid": "SMc2"})
    assert r.status_code == 200
    assert "Fuente: postgres" in r.text


# ---------- /health con ping a DB ----------

def test_health_file_backend(client):
    tc, _ = client
    r = tc.get("/health")
    body = r.json()
    assert body["sessions_backend"] == "file"
    assert body["database_ok"] is None  # no se intenta ping


@pytest.mark.asyncio(loop_scope="function")
async def test_health_postgres_ok(client, pg_db):
    tc, _ = client
    r = tc.get("/health")
    body = r.json()
    assert body["sessions_backend"] == "postgres"
    assert body["database_ok"] is True
    assert body["ok"] is True


@pytest.mark.asyncio(loop_scope="function")
async def test_health_postgres_down(client, monkeypatch):
    """Si el ping levanta, /health reporta database_ok=False y ok=False."""
    import db as db_mod, config
    monkeypatch.setattr(config, "SESSIONS_BACKEND", "postgres")
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql+asyncpg://x:x@nowhere/x")
    async def boom():
        raise RuntimeError("connection refused")
    monkeypatch.setattr(db_mod, "ping", boom)
    tc, _ = client
    r = tc.get("/health")
    body = r.json()
    assert body["database_ok"] is False
    assert body["database_error"] == "RuntimeError"
    assert body["ok"] is False
