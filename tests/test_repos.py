"""Tests de los repositorios (Fase C).

Sesiones: file + postgres (SQLite-aiosqlite en memoria).
Resto: postgres only (no-op cuando backend=file).
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio


# ---------- SessionRepo: backend=file ----------

async def test_session_file_roundtrip(tmp_path, monkeypatch):
    import config, repos
    monkeypatch.setattr(config, "SESSIONS_BACKEND", "file")
    monkeypatch.setattr(config, "SESSIONS_FILE", tmp_path / "s.json")
    r = repos.SessionRepo()
    assert await r.load("u1") == []
    await r.save("u1", [{"role": "user", "content": "hola"}])
    assert (await r.load("u1"))[0]["content"] == "hola"
    await r.clear("u1")
    assert await r.load("u1") == []


# ---------- SessionRepo: backend=postgres ----------

async def test_session_postgres_roundtrip(pg_db):
    import repos
    r = repos.SessionRepo()
    assert await r.load("u-pg") == []
    await r.save("u-pg", [{"role": "user", "content": "hola pg"}])
    h = await r.load("u-pg")
    assert h == [{"role": "user", "content": "hola pg"}]
    # update
    await r.save("u-pg", [{"role": "user", "content": "a"},
                          {"role": "assistant", "content": "b"}])
    h = await r.load("u-pg")
    assert len(h) == 2
    await r.clear("u-pg")
    assert await r.load("u-pg") == []


# ---------- MessageRepo ----------

async def test_message_repo_postgres(pg_db):
    import repos, models
    from sqlalchemy import select
    mid = await repos.messages.add(sid="SMx", sender="whatsapp:+5491100000000",
                                   body="hola", direction="inbound",
                                   inbox_page_id="inbox_1")
    assert mid is not None
    async with pg_db.session_scope() as s:
        rows = (await s.execute(select(models.Message))).scalars().all()
        assert len(rows) == 1
        assert rows[0].body == "hola" and rows[0].sid == "SMx"


async def test_message_repo_noop_when_file_backend(tmp_path, monkeypatch):
    import config, repos
    monkeypatch.setattr(config, "SESSIONS_BACKEND", "file")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    mid = await repos.messages.add(sid="SMx", sender="x", body="b")
    assert mid is None  # no-op


# ---------- AgentRunRepo + ToolCallRepo ----------

async def test_agent_run_with_tool_calls(pg_db):
    import repos, models
    from sqlalchemy import select
    run_id = await repos.agent_runs.create(
        sid="SM1", session_key="u", route="sonnet_agent",
        intent="plan", model="claude-sonnet-4-6")
    assert run_id
    await repos.tool_calls.add(agent_run_id=run_id, sid="SM1",
                               name="create_task",
                               args={"name": "x"}, result={"ok": True})
    await repos.tool_calls.add(agent_run_id=run_id, sid="SM1",
                               name="create_event",
                               args={"name": "y"},
                               result={"error": "boom"})
    await repos.agent_runs.finish(run_id, input_tokens=100, output_tokens=50,
                                  iterations=2, reply="✓ listo")

    async with pg_db.session_scope() as s:
        run = (await s.execute(
            select(models.AgentRun).where(models.AgentRun.id == run_id)
        )).scalar_one()
        assert run.input_tokens == 100 and run.iterations == 2
        assert run.finished_at is not None and run.reply == "✓ listo"

        tcs = (await s.execute(select(models.ToolCall))).scalars().all()
        assert len(tcs) == 2
        assert {t.name for t in tcs} == {"create_task", "create_event"}
        assert {t.ok for t in tcs} == {True, False}


# ---------- CostLogRepo ----------

async def test_cost_log_repo(pg_db):
    import repos, models
    from sqlalchemy import select
    await repos.cost_logs.add({
        "ts": datetime.now(timezone.utc).isoformat(),
        "sid": "SMx", "route": "haiku_router",
        "intent": "add_note", "model": "claude-haiku-4-5-20251001",
        "input_tokens": 200, "output_tokens": 50, "cost_usd": 0.00045,
        "iterations": 1,
    })
    async with pg_db.session_scope() as s:
        rows = (await s.execute(select(models.CostLog))).scalars().all()
        assert len(rows) == 1
        assert rows[0].route == "haiku_router"
        assert rows[0].input_tokens == 200
        assert rows[0].extra == {"iterations": 1}


# ---------- PendingConfirmationRepo: TTL ----------

async def test_pending_confirmation_lifecycle(pg_db, monkeypatch):
    import config, repos
    monkeypatch.setattr(config, "CONFIRMATION_TTL_MINUTES", 5)
    cid = await repos.confirmations.create(
        session_key="u", payload={"intent": "delete_all", "n": 12})
    assert cid is not None
    popped = await repos.confirmations.pop_latest("u")
    assert popped == {"intent": "delete_all", "n": 12}
    # ya consumida → no la encuentra
    assert await repos.confirmations.pop_latest("u") is None


async def test_pending_confirmation_expired(pg_db):
    """Una confirmacion vencida no se devuelve y se purga."""
    import repos, models
    from sqlalchemy import select
    # creamos directo en DB con expires_at pasado
    async with pg_db.session_scope() as s:
        row = models.PendingConfirmation(
            session_key="u-exp", payload={"x": 1},
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        s.add(row)
    assert await repos.confirmations.pop_latest("u-exp") is None
    purged = await repos.confirmations.purge_expired()
    assert purged >= 1


# ---------- Faltante de DATABASE_URL con backend=postgres ----------

@pytest.mark.asyncio(loop_scope="function")
async def test_config_raises_when_postgres_without_url(monkeypatch):
    """Reimportar config con SESSIONS_BACKEND=postgres y sin DATABASE_URL
    debe lanzar RuntimeError."""
    import importlib
    monkeypatch.setenv("SESSIONS_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "")
    import config
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        importlib.reload(config)
    monkeypatch.setenv("SESSIONS_BACKEND", "file")
    importlib.reload(config)
