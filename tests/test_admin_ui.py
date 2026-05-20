"""Tests del Command Center web (Fase I).

Cubre auth, chat web reutilizando el orquestador, listados, detalle,
retry safe vs bloqueado, y no exposicion de secretos.
"""
from unittest.mock import MagicMock

import pytest


def _ant_text(text, in_t=10, out_t=10):
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    b = MagicMock()
    b.type = "text"
    b.text = text
    b.model_dump.return_value = {"type": "text", "text": text}
    resp.content = [b]
    resp.usage = MagicMock(input_tokens=in_t, output_tokens=out_t)
    return resp


def _haiku_classifies(intent, complexity="low", confidence=0.9,
                      destructive=False):
    payload = (f'{{"intent":"{intent}","complexity":"{complexity}",'
               f'"confidence":{confidence},"destructive":'
               f'{"true" if destructive else "false"},"reason":"r"}}')
    return _ant_text(payload, in_t=100, out_t=30)


# ---------- Auth ----------

def test_admin_index_without_token_returns_404(client):
    tc, _ = client
    r = tc.get("/admin/")
    assert r.status_code == 404


def test_admin_index_with_wrong_token_returns_404(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_TOKEN", "good")
    tc, _ = client
    r = tc.get("/admin/", headers={"X-Admin-Token": "bad"})
    assert r.status_code == 404


def test_admin_index_with_token_header_renders(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_TOKEN", "good")
    tc, _ = client
    r = tc.get("/admin/", headers={"X-Admin-Token": "good"})
    assert r.status_code == 200
    assert "Command Center" in r.text


def test_admin_index_with_token_query_renders(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_TOKEN", "good")
    tc, _ = client
    r = tc.get("/admin/?token=good")
    assert r.status_code == 200


def test_admin_login_sets_cookie(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_TOKEN", "good")
    tc, _ = client
    r = tc.get("/admin/login?token=good", follow_redirects=False)
    assert r.status_code in (302, 307)
    cookies = r.headers.get("set-cookie", "")
    assert "admin_token=good" in cookies
    assert "HttpOnly" in cookies


def test_admin_disabled_when_token_empty(client, monkeypatch):
    """ADMIN_TOKEN vacio = UI deshabilitada (404 incluso con query)."""
    import config
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    tc, _ = client
    assert tc.get("/admin/").status_code == 404
    assert tc.get("/admin/?token=anything").status_code == 404


def test_admin_does_not_leak_secrets_in_html(client, monkeypatch):
    """El HTML renderizado no debe contener tokens / keys reales."""
    import config
    monkeypatch.setattr(config, "ADMIN_TOKEN", "super_secret_admin_token_xyz")
    monkeypatch.setattr(config, "NOTION_TOKEN", "ntn_NEVER_LEAK_THIS_NOTION")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-NEVER_LEAK")
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "tw_NEVER_LEAK")
    tc, _ = client
    r = tc.get("/admin/config",
               headers={"X-Admin-Token": "super_secret_admin_token_xyz"})
    assert r.status_code == 200
    text = r.text
    # ningun valor de secret se filtra
    for forbidden in ("ntn_NEVER_LEAK_THIS_NOTION", "sk-ant-NEVER_LEAK",
                      "tw_NEVER_LEAK", "super_secret_admin_token_xyz"):
        assert forbidden not in text, f"se filtro: {forbidden}"


# ---------- Sesiones web ----------

@pytest.fixture
def admin(monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_TOKEN", "T")
    return {"X-Admin-Token": "T"}


@pytest.mark.asyncio(loop_scope="function")
async def test_new_session_creates_web_session(client, fake_notion, pg_db,
                                                admin):
    tc, _ = client
    r = tc.post("/admin/sessions/new", headers=admin, follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/admin/c/web:")
    # se persistio con source=web
    import repos, models, db
    from sqlalchemy import select
    async with db.session_scope() as s:
        rows = (await s.execute(
            select(models.Session).where(models.Session.source == "web")
        )).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_chat_send_runs_pipeline_and_persists(client, fake_notion,
                                                     pg_db, admin):
    tc, ant = client
    ant.messages.create.side_effect = [
        _haiku_classifies("add_note"),
        _ant_text("✓ nota guardada"),
    ]
    fake_notion.databases.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "p1"}
    sk = "web:abc123"
    import repos
    await repos.sessions.save(sk, [], source="web")
    r = tc.post(f"/admin/c/{sk}/send",
                headers=admin, data={"body": "anotá: idea X"})
    assert r.status_code == 200
    assert "nota guardada" in r.text.lower()
    # chip de route esta en el HTML
    assert "capture_agent" in r.text
    # se persistio la historia
    hist = await repos.sessions.load(sk)
    assert len(hist) >= 2  # user + assistant


@pytest.mark.asyncio(loop_scope="function")
async def test_chat_view_renders_history(client, fake_notion, pg_db, admin):
    sk = "web:xyz"
    import repos
    await repos.sessions.save(sk,
        [{"role": "user", "content": "hola"},
         {"role": "assistant", "content": "hola, que necesitas"}],
        source="web")
    tc, _ = client
    r = tc.get(f"/admin/c/{sk}", headers=admin)
    assert r.status_code == 200
    assert "hola, que necesitas" in r.text


# ---------- Listados ----------

@pytest.mark.asyncio(loop_scope="function")
async def test_list_sessions_filtered_by_source(client, pg_db, admin):
    import repos
    await repos.sessions.save("web:s1", [{"role": "user", "content": "h"}],
                              source="web")
    await repos.sessions.save("whatsapp:+5491100000000",
                              [{"role": "user", "content": "h"}],
                              source="whatsapp")
    tc, _ = client
    r = tc.get("/admin/sessions?source=web", headers=admin)
    assert r.status_code == 200
    assert "web:s1" in r.text
    assert "whatsapp:+5491100000000" not in r.text


@pytest.mark.asyncio(loop_scope="function")
async def test_list_runs(client, pg_db, admin):
    import repos
    await repos.agent_runs.create(sid=None, session_key="web:s1",
                                  route="capture_agent", intent="add_note",
                                  model="haiku", plan={}, safety_level="safe")
    tc, _ = client
    r = tc.get("/admin/runs", headers=admin)
    assert r.status_code == 200
    assert "capture_agent" in r.text
    assert "add_note" in r.text


@pytest.mark.asyncio(loop_scope="function")
async def test_run_detail_shows_plan_and_tools(client, pg_db, admin):
    import repos
    run_id = await repos.agent_runs.create(
        sid="SX1", session_key="web:s1", route="capture_agent",
        intent="add_note", model="haiku",
        plan={"intent": "add_note", "route": "capture_agent"},
        safety_level="safe")
    await repos.tool_calls.add(agent_run_id=run_id, sid="SX1",
                               name="add_note", args={"content": "x"},
                               result={"ok": True, "note_id": "abc-def-123"})
    tc, _ = client
    r = tc.get(f"/admin/runs/{run_id}", headers=admin)
    assert r.status_code == 200
    assert "add_note" in r.text
    assert "abc-def-123" in r.text
    # link Notion construido a partir del *_id
    assert "notion.so" in r.text


def test_agents_view_lists_all(client, admin):
    tc, _ = client
    r = tc.get("/admin/agents", headers=admin)
    assert r.status_code == 200
    for name in ("capture_agent", "planner_agent", "writer_agent",
                 "research_agent", "critic_agent"):
        assert name in r.text


@pytest.mark.asyncio(loop_scope="function")
async def test_costs_view_renders(client, pg_db, admin):
    import repos
    await repos.cost_logs.add({"route": "rule", "intent": "x",
                                "cost_usd": 0.0012})
    tc, _ = client
    r = tc.get("/admin/costs", headers=admin)
    assert r.status_code == 200
    assert "Costos" in r.text


def test_alerts_view_renders(client, admin):
    tc, _ = client
    r = tc.get("/admin/alerts", headers=admin)
    assert r.status_code == 200


# ---------- Confirmaciones ----------

@pytest.mark.asyncio(loop_scope="function")
async def test_approve_confirmation_executes_plan(client, fake_notion,
                                                   pg_db, admin):
    """Plan destructive crea pending; approve lo ejecuta."""
    tc, ant = client
    sk = "web:cf1"
    import repos
    await repos.sessions.save(sk, [], source="web")
    # 1) mensaje destructive
    ant.messages.create.side_effect = [
        _haiku_classifies("destructive", destructive=True),
        _ant_text('{"verdict":"ok","reason":"r"}'),  # critic OK
    ]
    fake_notion.databases.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "p1"}
    r = tc.post(f"/admin/c/{sk}/send", headers=admin,
                data={"body": "borrá tareas viejas"})
    assert r.status_code == 200
    assert "cancelar" in r.text.lower() or "confirm" in r.text.lower()
    pending = await repos.confirmations.pop_latest(sk)
    # ya fue consumida? no: pop_latest la consumio aca para inspeccion.
    # restauramos:
    if pending:
        await repos.confirmations.create(session_key=sk, payload=pending)

    # 2) approve
    ant.messages.create.side_effect = [_ant_text("✓ tareas borradas")]
    r2 = tc.post(f"/admin/c/{sk}/approve", headers=admin)
    assert r2.status_code == 200
    assert "borradas" in r2.text.lower() or "✓" in r2.text


@pytest.mark.asyncio(loop_scope="function")
async def test_cancel_confirmation_discards(client, fake_notion, pg_db,
                                             admin):
    tc, ant = client
    sk = "web:cf2"
    import repos
    await repos.sessions.save(sk, [], source="web")
    ant.messages.create.side_effect = [
        _haiku_classifies("destructive", destructive=True),
        _ant_text('{"verdict":"ok","reason":"r"}'),
    ]
    fake_notion.databases.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "p1"}
    tc.post(f"/admin/c/{sk}/send", headers=admin,
            data={"body": "borrá X"})
    r = tc.post(f"/admin/c/{sk}/cancel", headers=admin)
    assert r.status_code == 200
    assert "cancel" in r.text.lower()
    # no queda pending vigente
    assert await repos.confirmations.pop_latest(sk) is None


# ---------- Retry ----------

@pytest.mark.asyncio(loop_scope="function")
async def test_retry_safe_async_error_is_allowed(client, pg_db, admin):
    import repos
    run_id = await repos.agent_runs.create(
        sid=None, session_key="web:rt", route="research_agent",
        intent="research", model="haiku",
        plan={"intent": "research", "route": "research_agent",
              "payload": {"user_text": "X"}},
        safety_level="safe", async_state="async_error")
    tc, _ = client
    r = tc.post(f"/admin/runs/{run_id}/retry", headers=admin)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["new_run_id"]


@pytest.mark.asyncio(loop_scope="function")
async def test_retry_destructive_is_blocked(client, pg_db, admin):
    import repos
    run_id = await repos.agent_runs.create(
        sid=None, session_key="web:rt2", route="sonnet_agent",
        intent="destructive", model="sonnet",
        plan={"intent": "destructive"},
        safety_level="destructive", async_state="async_error")
    tc, _ = client
    r = tc.post(f"/admin/runs/{run_id}/retry", headers=admin)
    assert r.status_code == 400
    assert r.json()["ok"] is False


@pytest.mark.asyncio(loop_scope="function")
async def test_retry_only_for_async_error(client, pg_db, admin):
    import repos
    run_id = await repos.agent_runs.create(
        sid=None, session_key="web:rt3", route="capture_agent",
        intent="add_note", model="haiku", plan={},
        safety_level="safe", async_state="async_done")
    tc, _ = client
    r = tc.post(f"/admin/runs/{run_id}/retry", headers=admin)
    assert r.status_code == 400


# ---------- System status ----------

def test_system_endpoint_json(client, admin):
    tc, _ = client
    r = tc.get("/admin/api/system", headers=admin)
    assert r.status_code == 200
    body = r.json()
    assert "sessions_backend" in body
    assert "async_enabled" in body
    # no debe filtrar tokens
    assert "ADMIN_TOKEN" not in str(body)
