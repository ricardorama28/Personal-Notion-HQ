"""Tests del worker async (Fase H).

Cubre:
- should_run_async politica;
- webhook con ASYNC_ENABLED=true encola y responde rapido;
- worker ejecuta el plan y persiste async_done/async_error;
- worker manda outbound mockeado (twilio_outbound.send);
- twilio_outbound fail-soft cuando falta config;
- confirmacion aceptada con plan async se encola;
- acciones simples siguen sincronas;
- ASYNC_ENABLED=false: comportamiento previo intacto.
"""
from unittest.mock import MagicMock, patch

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


# ---------- Politica ----------

def test_should_run_async_disabled_returns_false(monkeypatch):
    import async_runner, config
    from orchestrator import ActionPlan
    monkeypatch.setattr(config, "ASYNC_ENABLED", False)
    p = ActionPlan(intent="plan", route="planner_agent",
                   needs_confirmation=False, safety_level="bulk")
    assert async_runner.should_run_async(p) is False


def test_should_run_async_planner_route(monkeypatch):
    import async_runner, config
    from orchestrator import ActionPlan
    monkeypatch.setattr(config, "ASYNC_ENABLED", True)
    for route in ("planner_agent", "writer_agent", "research_agent"):
        p = ActionPlan(intent="x", route=route)
        assert async_runner.should_run_async(p) is True, route


def test_should_run_async_capture_stays_sync(monkeypatch):
    import async_runner, config
    from orchestrator import ActionPlan
    monkeypatch.setattr(config, "ASYNC_ENABLED", True)
    # Capture y reglas son sync siempre, aunque ASYNC_ENABLED=true.
    p1 = ActionPlan(intent="add_note", route="capture_agent")
    p2 = ActionPlan(intent="add_expense", route="rule")
    assert async_runner.should_run_async(p1) is False
    assert async_runner.should_run_async(p2) is False


def test_should_run_async_required_flag_overrides(monkeypatch):
    import async_runner, config
    from orchestrator import ActionPlan
    monkeypatch.setattr(config, "ASYNC_ENABLED", True)
    p = ActionPlan(intent="add_note", route="capture_agent",
                   async_required=True)
    assert async_runner.should_run_async(p) is True


# ---------- twilio_outbound: fail soft ----------

def test_outbound_skips_when_no_config(monkeypatch):
    import twilio_outbound, config
    monkeypatch.setattr(config, "TWILIO_ACCOUNT_SID", "")
    monkeypatch.setattr(config, "TWILIO_FROM_WHATSAPP", "")
    monkeypatch.setattr(config, "MY_WHATSAPP", "whatsapp:+5491100000000")
    assert twilio_outbound.send("whatsapp:+5491100000000", "hola") is False


def test_outbound_refuses_other_destination(monkeypatch):
    """Anti-fuga: solo manda al MY_WHATSAPP."""
    import twilio_outbound, config
    monkeypatch.setattr(config, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(config, "TWILIO_FROM_WHATSAPP", "whatsapp:+1")
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "x")
    monkeypatch.setattr(config, "MY_WHATSAPP", "whatsapp:+5491100000000")
    fake_client = MagicMock()
    assert twilio_outbound.send("whatsapp:+99", "hola",
                                client=fake_client) is False
    fake_client.messages.create.assert_not_called()


def test_outbound_sends_when_configured(monkeypatch):
    import twilio_outbound, config
    monkeypatch.setattr(config, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(config, "TWILIO_FROM_WHATSAPP", "whatsapp:+1")
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "x")
    monkeypatch.setattr(config, "MY_WHATSAPP", "whatsapp:+5491100000000")
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(sid="SMout1")
    ok = twilio_outbound.send("whatsapp:+5491100000000",
                              "hola desde el worker", client=fake_client)
    assert ok is True
    call = fake_client.messages.create.call_args.kwargs
    assert call["to"] == "whatsapp:+5491100000000"
    assert call["from_"] == "whatsapp:+1"
    assert "hola" in call["body"]


def test_outbound_swallows_twilio_exception(monkeypatch):
    import twilio_outbound, config
    monkeypatch.setattr(config, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(config, "TWILIO_FROM_WHATSAPP", "whatsapp:+1")
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "x")
    monkeypatch.setattr(config, "MY_WHATSAPP", "whatsapp:+5491100000000")
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("twilio down")
    assert twilio_outbound.send("whatsapp:+5491100000000", "hola",
                                client=fake_client) is False


# ---------- Webhook: encola y responde rapido ----------

@pytest.fixture
def async_on(monkeypatch):
    import config
    monkeypatch.setattr(config, "ASYNC_ENABLED", True)


def test_webhook_plan_intent_with_async_disabled_stays_sync(client,
                                                            fake_notion):
    """ASYNC_ENABLED=false (default): comportamiento previo, sin encolar."""
    tc, ant = client
    ant.messages.create.side_effect = [
        _haiku_classifies("research", confidence=0.9),
        _ant_text("respuesta sync"),
    ]
    fake_notion.data_sources.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "p1"}
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "investigá X",
                                   "MessageSid": "SMsync"})
    assert r.status_code == 200
    assert "te respondo en un toque" not in r.text.lower()
    # se llamo el agente sincronicamente
    assert ant.messages.create.call_count == 2


def test_webhook_research_with_async_on_enqueues(client, fake_notion,
                                                  async_on):
    """ASYNC_ENABLED=true + intent research → encola + ACK rapido."""
    tc, ant = client
    # Solo el clasificador se llama en el handler. El worker ejecuta
    # despues (lo verifica el siguiente test).
    ant.messages.create.side_effect = [
        _haiku_classifies("research", confidence=0.9),
        # Si el worker corre durante el TestClient, va a llamar de nuevo;
        # le damos una respuesta razonable por las dudas.
        _ant_text("✓ research stub"),
    ]
    fake_notion.data_sources.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "p1"}
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "investigá las opciones",
                                   "MessageSid": "SMasync1"})
    assert r.status_code == 200
    # ACK contiene "te respondo en un toque"
    assert "toque" in r.text.lower() or "recibí" in r.text.lower() or "recibi" in r.text.lower()


@pytest.mark.asyncio(loop_scope="function")
async def test_webhook_async_worker_marks_done(client, fake_notion, pg_db,
                                                async_on, monkeypatch):
    """Con postgres: tras el handler, agent_runs.async_state == async_done
    y el outbound se intento mandar (twilio_outbound.send mockeado)."""
    import twilio_outbound
    sent = []
    def fake_send(to, body, client=None):
        sent.append((to, body))
        return True
    monkeypatch.setattr(twilio_outbound, "send", fake_send)

    tc, ant = client
    ant.messages.create.side_effect = [
        _haiku_classifies("research", confidence=0.9),
        _ant_text("no tengo browsing habilitado"),
    ]
    fake_notion.data_sources.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "p1"}
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "investigá precios",
                                   "MessageSid": "SMasync2"})
    assert r.status_code == 200

    # TestClient corre los BackgroundTasks sincronicamente al cerrar la
    # response, asi que para este punto el worker ya termino.
    import models, db
    from sqlalchemy import select
    async with db.session_scope() as s:
        runs = (await s.execute(
            select(models.AgentRun).where(
                models.AgentRun.async_state.is_not(None))
        )).scalars().all()
        assert len(runs) == 1
        assert runs[0].async_state == "async_done"
        assert runs[0].route == "research_agent"

    # Outbound mockeado fue invocado con la respuesta del worker.
    assert len(sent) == 1
    to, body = sent[0]
    assert to == "whatsapp:+5491100000000"
    assert "browsing" in body.lower() or len(body) > 0


@pytest.mark.asyncio(loop_scope="function")
async def test_webhook_async_worker_marks_error(client, fake_notion, pg_db,
                                                 async_on, monkeypatch):
    """Si el agente revienta, async_state queda async_error y el outbound
    avisa al usuario sin filtrar detalle."""
    import twilio_outbound
    sent = []
    monkeypatch.setattr(twilio_outbound, "send",
                        lambda to, body, client=None: sent.append((to, body)) or True)

    tc, ant = client
    # 1) clasificador OK. 2) agente revienta.
    def side(*a, **kw):
        if side.call == 0:
            side.call += 1
            return _haiku_classifies("research", confidence=0.9)
        raise RuntimeError("boom secret_token=abc")
    side.call = 0
    ant.messages.create.side_effect = side

    fake_notion.data_sources.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "p1"}
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "investigá X",
                                   "MessageSid": "SMasyncerr"})
    assert r.status_code == 200

    import models, db
    from sqlalchemy import select
    async with db.session_scope() as s:
        runs = (await s.execute(
            select(models.AgentRun).where(
                models.AgentRun.async_state == "async_error")
        )).scalars().all()
        assert len(runs) == 1
        assert "RuntimeError" in (runs[0].error or "")

    # outbound avisa al usuario SIN filtrar el secret_token.
    assert len(sent) == 1
    _, body = sent[0]
    assert "secret_token" not in body
    assert "no pude" in body.lower() or "⚠" in body


@pytest.mark.asyncio(loop_scope="function")
async def test_confirmation_accepted_with_async_enqueues(client, fake_notion,
                                                          pg_db, async_on,
                                                          monkeypatch):
    """Plan bulk (planner) confirmado → encolado en background."""
    import twilio_outbound
    sent = []
    monkeypatch.setattr(twilio_outbound, "send",
                        lambda to, body, client=None: sent.append((to, body)) or True)
    tc, ant = client
    # 1) clasificador → plan high
    # 2) (post-confirm) worker llama al PlannerAgent → respuesta texto
    ant.messages.create.side_effect = [
        _haiku_classifies("plan", complexity="high", confidence=0.95),
        _ant_text("✓ semana organizada"),
    ]
    fake_notion.data_sources.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "p1"}
    # paso 1: pedido → crea pending
    r1 = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                    "Body": "organizame la semana",
                                    "MessageSid": "SMcf1"})
    assert r1.status_code == 200 and "cancelar" in r1.text.lower()

    # paso 2: confirmar → encolado, ACK al user, worker corre
    r2 = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                    "Body": "1",
                                    "MessageSid": "SMcf2"})
    assert r2.status_code == 200
    assert "toque" in r2.text.lower() or "recibí" in r2.text.lower() or "recibi" in r2.text.lower()

    # worker corrio y la salida fue al outbound mockeado
    assert len(sent) == 1
    _, body = sent[0]
    assert "organiz" in body.lower() or "✓" in body

    # agent_run con confirmed_from y async_done queda en DB
    import models, db
    from sqlalchemy import select
    async with db.session_scope() as s:
        runs = (await s.execute(
            select(models.AgentRun).where(
                models.AgentRun.async_state == "async_done",
                models.AgentRun.confirmed_from.is_not(None))
        )).scalars().all()
        assert len(runs) == 1


# ---------- Acciones simples siguen sincronas con ASYNC_ENABLED=true ----------

def test_capture_intent_still_sync_with_async_on(client, fake_notion,
                                                  async_on):
    """add_note no es async — el reply es la respuesta real del agente,
    no el ACK."""
    tc, ant = client
    ant.messages.create.side_effect = [
        _haiku_classifies("add_note", confidence=0.95),
        _ant_text("✓ nota guardada"),
    ]
    fake_notion.data_sources.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "p1"}
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "anotá: idea",
                                   "MessageSid": "SMsync2"})
    assert r.status_code == 200
    assert "nota guardada" in r.text.lower()
    assert "toque" not in r.text.lower()
