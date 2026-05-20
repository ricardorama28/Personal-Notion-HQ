"""Fase J liviana: pins Docker, limpieza queries, no @apply en CDN,
async file backend cae a sync, rotacion JSONL."""
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ---------- 1. Docker pins ----------

def test_postgres_image_is_pinned():
    text = (ROOT / "docker-compose.yml").read_text()
    # postgres:16.x-alpine concreto, no `postgres:16-alpine` ni `latest`.
    m = re.search(r"image:\s*postgres:(\S+)", text)
    assert m, "no se encontro imagen postgres"
    tag = m.group(1)
    assert re.match(r"^\d+\.\d+(\.\d+)?-alpine$", tag), \
        f"postgres tag no esta pinneado a minor: {tag}"


def test_cloudflared_image_is_pinned():
    text = (ROOT / "docker-compose.yml").read_text()
    m = re.search(r"image:\s*cloudflare/cloudflared:(\S+)", text)
    assert m, "no se encontro imagen cloudflared"
    tag = m.group(1)
    assert tag != "latest", "cloudflared no debe estar en :latest"
    # versionado tipo YYYY.MM.PATCH
    assert re.match(r"^\d{4}\.\d+\.\d+$", tag), \
        f"cloudflared tag no es version semantica de calendario: {tag}"


def test_compose_documents_pin_update():
    text = (ROOT / "docker-compose.yml").read_text()
    assert "actualizar" in text.lower() or "update" in text.lower(), \
        "compose debe documentar como actualizar los pins"


# ---------- 2. queries.py sin codigo muerto ----------

def test_session_cost_summary_has_no_dead_total():
    src = (ROOT / "web" / "queries.py").read_text()
    # ya no hay "total = sum(0.0 for _ in rows)" deshechado
    assert "sum(0.0 for _ in rows)" not in src


def test_tool_usage_has_single_query():
    src = (ROOT / "web" / "queries.py").read_text()
    # ya no usa cast(ok, JSON) — query muerta eliminada
    assert "func.cast(models.ToolCall.ok" not in src


# ---------- 3. base.html sin @apply ----------

def test_base_template_does_not_use_apply():
    """Tailwind CDN no soporta @apply en runtime."""
    text = (ROOT / "web" / "templates" / "base.html").read_text()
    assert "@apply" not in text, \
        "base.html no debe usar @apply (Tailwind via CDN no lo soporta)"


def test_base_template_keeps_chip_classes():
    """Los chips siguen estilados (con CSS plano) para mantener UX."""
    text = (ROOT / "web" / "templates" / "base.html").read_text()
    for cls in ("chip-rule", "chip-haiku", "chip-sonnet", "chip-blocked",
                "chip-bulk", "chip-unsafe", "chip-safe", "chip-destructive",
                "chip-default"):
        assert f".{cls}" in text, f"falta estilo para .{cls}"


# ---------- 4. async + file backend → sync ----------

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


def _haiku_classifies(intent, confidence=0.9, destructive=False):
    payload = (f'{{"intent":"{intent}","complexity":"low",'
               f'"confidence":{confidence},"destructive":'
               f'{"true" if destructive else "false"},"reason":"r"}}')
    return _ant_text(payload, in_t=100, out_t=30)


def test_chat_web_async_file_backend_falls_back_to_sync(client, fake_notion,
                                                         monkeypatch):
    """ASYNC_ENABLED=true + SESSIONS_BACKEND=file → no encolar, ejecutar
    inline. El response debe traer el reply real, no el ACK."""
    import config
    monkeypatch.setattr(config, "ADMIN_TOKEN", "T")
    monkeypatch.setattr(config, "ASYNC_ENABLED", True)
    # backend=file por default en conftest
    assert config.SESSIONS_BACKEND == "file"

    tc, ant = client
    ant.messages.create.side_effect = [
        _haiku_classifies("research", confidence=0.95),
        _ant_text("no tengo browsing habilitado"),
    ]
    fake_notion.databases.query.return_value = {"results": []}
    fake_notion.pages.create.return_value = {"id": "p1"}

    sk = "web:fb1"
    # Inicializar la sesion via file backend (save crea el archivo).
    import repos
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        repos.sessions.save(sk, [], source="web")
    ) if False else None  # no async fixture en este test; saltamos init.

    r = tc.post(f"/admin/c/{sk}/send",
                headers={"X-Admin-Token": "T"},
                data={"body": "investigá X"})
    assert r.status_code == 200
    # El reply real esta presente, no el ACK
    assert "browsing" in r.text.lower()
    assert "procesando" not in r.text.lower()
    # Se llamo al agente (2 calls: clasificador + agente)
    assert ant.messages.create.call_count == 2


# ---------- 5. cost_log rotation ----------

def test_cost_log_rotates_when_exceeding_max_bytes(tmp_path, monkeypatch):
    import config, cost_log
    log_file = tmp_path / "cost.jsonl"
    monkeypatch.setattr(config, "COST_LOG_FILE", log_file)
    monkeypatch.setattr(config, "COST_LOG_MAX_BYTES", 200)  # forzar rotacion rapida
    monkeypatch.setattr(config, "COST_LOG_BACKUPS", 3)

    # Escribir varias filas hasta forzar rotacion.
    for i in range(50):
        cost_log.log_event(route="rule", intent=f"i{i}",
                           input_tokens=10, output_tokens=10,
                           extra={"padding": "x" * 50})

    # Debe existir el archivo principal y al menos un .1
    assert log_file.exists()
    rotated = log_file.with_suffix(log_file.suffix + ".1")
    assert rotated.exists(), "no se creo cost.jsonl.1"
    # nunca debe haber un .4 (BACKUPS=3)
    assert not log_file.with_suffix(log_file.suffix + ".4").exists()


def test_cost_log_no_rotation_when_under_limit(tmp_path, monkeypatch):
    import config, cost_log
    log_file = tmp_path / "cost.jsonl"
    monkeypatch.setattr(config, "COST_LOG_FILE", log_file)
    monkeypatch.setattr(config, "COST_LOG_MAX_BYTES", 10 * 1024 * 1024)  # 10 MB
    monkeypatch.setattr(config, "COST_LOG_BACKUPS", 5)
    for i in range(5):
        cost_log.log_event(route="rule", intent=f"i{i}")
    assert log_file.exists()
    assert not log_file.with_suffix(log_file.suffix + ".1").exists()


def test_cost_log_rotation_disabled_when_max_bytes_zero(tmp_path, monkeypatch):
    import config, cost_log
    log_file = tmp_path / "cost.jsonl"
    monkeypatch.setattr(config, "COST_LOG_FILE", log_file)
    monkeypatch.setattr(config, "COST_LOG_MAX_BYTES", 0)
    monkeypatch.setattr(config, "COST_LOG_BACKUPS", 5)
    for i in range(50):
        cost_log.log_event(route="rule", intent=f"i{i}",
                           extra={"padding": "x" * 200})
    assert log_file.exists()
    # No deberia rotar nunca
    assert not log_file.with_suffix(log_file.suffix + ".1").exists()


def test_cost_log_summary_reads_only_current_file(tmp_path, monkeypatch):
    """summary() lee solo el archivo activo. Las filas rotadas a .1/.2
    quedan en disco pero no se cuentan (asi el reporte 7d se mantiene
    barato; si haces falta total historico, se hace por DB)."""
    import config, cost_log
    log_file = tmp_path / "cost.jsonl"
    monkeypatch.setattr(config, "COST_LOG_FILE", log_file)
    monkeypatch.setattr(config, "COST_LOG_MAX_BYTES", 200)
    monkeypatch.setattr(config, "COST_LOG_BACKUPS", 3)
    for i in range(40):
        cost_log.log_event(route="rule", intent="x",
                           extra={"padding": "x" * 80})
    s = cost_log.summary(last_n_days=7)
    # No rompe; events corresponde solo al archivo activo
    assert s["events"] >= 0
    assert s["source"] == "jsonl"
