"""Tests del endpoint /webhook: firma, autorizacion, idempotencia, tamano."""
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
    client_mock.messages.create.return_value = resp


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
    """Con TWILIO_VALIDATE=true y sin header, devuelve 403."""
    import config
    monkeypatch.setattr(config, "TWILIO_VALIDATE", True)
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "test_twilio_token")
    # importar tarde para evitar cache
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


def test_idempotent_same_sid(client, fake_notion):
    tc, ant = client
    _fake_anthropic_text(ant, "✓ tarea creada")

    # primera vez: no existe la fila → crea
    fake_notion.databases.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "inbox_1"}
    r1 = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                    "Body": "tarea: comprar leche",
                                    "MessageSid": "SMsame"})
    assert r1.status_code == 200 and "tarea creada" in r1.text

    # segunda vez con mismo SID: existing=True → respuesta de duplicado
    fake_notion.databases.query.return_value = {
        "results": [{"id": "inbox_1"}]
    }
    r2 = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                    "Body": "tarea: comprar leche",
                                    "MessageSid": "SMsame"})
    assert r2.status_code == 200 and "ya recibido" in r2.text


def test_unknown_message_closes_inbox_as_needs_review(client, fake_notion,
                                                      monkeypatch):
    """Mensaje raro: Claude responde texto, no se ejecuta ninguna tool,
    el Inbox cierra Needs review / Unknown."""
    tc, ant = client
    _fake_anthropic_text(ant, "no entendi, podés repetir?")
    fake_notion.databases.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "inbox_unk"}

    r = tc.post("/webhook", data={"From": "whatsapp:+5491100000000",
                                   "Body": "xyzzy lorem ipsum",
                                   "MessageSid": "SMunk"})
    assert r.status_code == 200
    # se llamo pages.update sobre el inbox cerrando como Needs review/Unknown
    update_calls = [c for c in fake_notion.pages.update.call_args_list
                    if c.kwargs.get("page_id") == "inbox_unk"]
    assert update_calls
    props = update_calls[-1].kwargs["properties"]
    assert props["Processing Status"]["select"]["name"] == "Needs review"
    assert props["Detected Type"]["select"]["name"] == "Unknown"


def test_run_agent_exception_still_closes_inbox(client, fake_notion):
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
    assert update_calls  # se cerro la fila pese a la excepcion
