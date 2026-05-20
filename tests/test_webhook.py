"""Tests del endpoint /webhook: firma, autorizacion, idempotencia, tamano, router."""
from unittest.mock import MagicMock

import pytest


def _fake_anthropic_text(client_mock, text="✓ ok"):
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    block = MagicMock()
    block.type = "text"
    block.text = text
    block.model_dump.return_value = {"type": "text", "text": text}
    resp.content = [block]
    resp.usage = MagicMock(input_tokens=10, output_tokens=10)
    client_mock.messages.create.return_value = resp


def _router_then_agent(client_mock, classifier_json, agent_text):
    """Configura el mock para devolver el JSON del clasificador en la primera
    llamada (modelo Haiku) y la respuesta de agente en la segunda."""
    def make_resp(text, in_t=10, out_t=10):
        r = MagicMock()
        r.stop_reason = "end_turn"
        b = MagicMock()
        b.type = "text"
        b.text = text
        b.model_dump.return_value = {"type": "text", "text": text}
        r.content = [b]
        r.usage = MagicMock(input_tokens=in_t, output_tokens=out_t)
        return r
    seq = [make_resp(classifier_json, 100, 30), make_resp(agent_text, 200, 50)]
    client_mock.messages.create.side_effect = seq


@pytest.fixture
def no_router(monkeypatch):
    """Deshabilita el router para tests que prueban el agente directo."""
    import config
    monkeypatch.setattr(config, "ROUTER_ENABLED", False)


def test_health(client):
    tc, _ = client
    r = tc.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_unauthorized_number(client, fake_notion):
    tc, _ = client
    r = tc.post("/webhook", data={"From": "whatsapp:+99", "Body": "hola",
                                  "MessageSid": "SM1"})
    assert r.status_code == 200
    assert "No autorizado" in r.text


def test_invalid_signature_rejected(monkeypatch, fake_notion):
    import config
    monkeypatch.setattr(config, "TWILIO_VALIDATE", True)
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "test_twilio_token")
    from fastapi.testclient import TestClient
    import main
    tc = TestClient(main.app)
    r = tc.post("/webhook", data={"From": "whatsapp:+54900",
                                   "Body": "hola", "MessageSid": "SMx"})
    assert r.status_code == 403


def test_message_too_long(client, fake_notion):
    tc, _ = client
    big = "x" * 5000
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": big, "MessageSid": "SMbig"})
    assert r.status_code == 200
    assert "demasiado largo" in r.text


def test_reset_command(client, fake_notion):
    tc, _ = client
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "/reset", "MessageSid": "SMr"})
    assert r.status_code == 200
    assert "sesion limpia" in r.text


def test_cost_command(client, fake_notion):
    tc, _ = client
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "/cost", "MessageSid": "SMc"})
    assert r.status_code == 200
    assert "Costo 7d" in r.text


def test_idempotent_same_sid(client, fake_notion, no_router):
    tc, ant = client
    _fake_anthropic_text(ant, "✓ tarea creada")

    fake_notion.databases.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "inbox_1"}
    r1 = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                    "Body": "tarea: comprar leche",
                                    "MessageSid": "SMsame"})
    assert r1.status_code == 200 and "tarea creada" in r1.text

    fake_notion.databases.query.return_value = {"results": [{"id": "inbox_1"}]}
    r2 = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                    "Body": "tarea: comprar leche",
                                    "MessageSid": "SMsame"})
    assert r2.status_code == 200 and "ya recibido" in r2.text


def test_unknown_message_closes_inbox_as_needs_review(client, fake_notion,
                                                      no_router):
    tc, ant = client
    _fake_anthropic_text(ant, "no entendi, podés repetir?")
    fake_notion.databases.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "inbox_unk"}

    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "xyzzy lorem ipsum",
                                   "MessageSid": "SMunk"})
    assert r.status_code == 200
    update_calls = [c for c in fake_notion.pages.update.call_args_list
                    if c.kwargs.get("page_id") == "inbox_unk"]
    assert update_calls
    props = update_calls[-1].kwargs["properties"]
    assert props["Processing Status"]["select"]["name"] == "Needs review"
    assert props["Detected Type"]["select"]["name"] == "Unknown"


def test_run_agent_exception_still_closes_inbox(client, fake_notion, no_router):
    tc, ant = client
    ant.messages.create.side_effect = RuntimeError("boom")
    fake_notion.databases.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "inbox_err"}

    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "algo",
                                   "MessageSid": "SMerr"})
    assert r.status_code == 200 and "error" in r.text.lower()
    update_calls = [c for c in fake_notion.pages.update.call_args_list
                    if c.kwargs.get("page_id") == "inbox_err"]
    assert update_calls


# ---------- Router habilitado: integracion ----------

def test_router_rule_expense_skips_llm(client, fake_notion):
    """Mensaje 'gasto X' debe matchear la regla y NO llamar a Anthropic."""
    tc, ant = client
    fake_notion.databases.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "inbox_r1"}
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "gasto 450 super con debito",
                                   "MessageSid": "SMrule"})
    assert r.status_code == 200
    assert "gasto" in r.text.lower()
    # Ningun create de Anthropic.
    ant.messages.create.assert_not_called()
    # Pero si una creacion de Expense en Notion.
    create_calls = fake_notion.pages.create.call_args_list
    expense_calls = [c for c in create_calls
                     if c.kwargs.get("parent", {}).get("database_id") == "db_expenses"]
    assert expense_calls


def test_router_haiku_then_sonnet(client, fake_notion):
    """Mensaje complejo no-bulk con backend=file: router clasifica con
    Haiku y ejecuta el agente Sonnet directo (sin confirmacion).

    Nota: intent="plan" o "destructive" estan gateados por el
    orquestador (requieren postgres). Usamos un intent libre tipo
    "research" para ejercitar la ruta Sonnet sin disparar el gate.
    """
    tc, ant = client
    fake_notion.databases.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "inbox_r2"}
    _router_then_agent(
        ant,
        classifier_json='{"intent":"research","complexity":"high","confidence":0.95,"destructive":false,"reason":"research"}',
        agent_text="✓ revisado",
    )
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "investigame las opciones",
                                   "MessageSid": "SMplan"})
    assert r.status_code == 200 and "revisado" in r.text.lower()
    # clasificador + agente Sonnet
    assert ant.messages.create.call_count == 2


def test_router_bulk_intent_blocked_in_file_backend(client, fake_notion):
    """Con backend=file, intent='plan' (bulk) se rechaza explicitamente."""
    tc, ant = client
    fake_notion.databases.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "inbox_pl"}
    _router_then_agent(
        ant,
        classifier_json='{"intent":"plan","complexity":"high","confidence":0.95,"destructive":false,"reason":"plan"}',
        agent_text="(nunca se llama)",
    )
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "organizame la semana",
                                   "MessageSid": "SMplan2"})
    assert r.status_code == 200
    assert "postgres" in r.text.lower() or "no la puedo" in r.text.lower()
    # solo se llamo al clasificador, NO al agente Sonnet
    assert ant.messages.create.call_count == 1


def test_router_haiku_low_complexity_uses_haiku_agent(client, fake_notion):
    """Clasificador dice low+alta confianza → loop de tool use con Haiku."""
    tc, ant = client
    import config
    fake_notion.databases.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "inbox_r3"}
    _router_then_agent(
        ant,
        classifier_json='{"intent":"add_note","complexity":"low","confidence":0.9,"destructive":false,"reason":"nota"}',
        agent_text="✓ nota guardada",
    )
    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "anotá: cumple de juan el sabado",
                                   "MessageSid": "SMhaiku"})
    assert r.status_code == 200
    assert ant.messages.create.call_count == 2
    # La segunda llamada (agente) usa ROUTER_MODEL (Haiku), no Sonnet.
    second_call = ant.messages.create.call_args_list[1]
    assert second_call.kwargs["model"] == config.ROUTER_MODEL
