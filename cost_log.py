"""Logging de costo a JSONL.

Cada fila es una invocacion a un modelo (router o agente) o una decision
del router que no llamo modelo (regex). Cuando lleguen Postgres + Fase C
el reemplazo es directo: una tabla `cost_logs` con las mismas columnas.

Precios indicativos (USD por 1M tokens, snapshot 2026-01). Ajustables por
env si Anthropic los cambia.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import config

log = logging.getLogger("wpp.cost")

# precios por 1M tokens (input, output). Aprox publicados.
PRICES = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
}


def _price(model: str) -> tuple[float, float]:
    if model in PRICES:
        return PRICES[model]
    for key, v in PRICES.items():
        if model.startswith(key.split("-2")[0]):  # match por familia
            return v
    return (0.0, 0.0)


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pi, po = _price(model)
    return round((input_tokens * pi + output_tokens * po) / 1_000_000, 6)


def log_event(*, route: str, intent: Optional[str] = None,
              model: Optional[str] = None, input_tokens: int = 0,
              output_tokens: int = 0, sid: Optional[str] = None,
              extra: Optional[dict] = None) -> dict:
    """Anexa una fila al JSONL. Nunca rompe la request si falla."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "sid": sid,
        "route": route,           # rule|haiku_router|sonnet_agent|haiku_agent|admin|error
        "intent": intent,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": estimate_cost_usd(model or "", input_tokens, output_tokens),
    }
    if extra:
        record.update(extra)
    try:
        config.COST_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(config.COST_LOG_FILE,
                          config.COST_LOG_MAX_BYTES,
                          config.COST_LOG_BACKUPS)
        with config.COST_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("no se pudo escribir cost_log: %s", e)
    return record


def _rotate_if_needed(path, max_bytes: int, backups: int) -> None:
    """Rotacion tipo RotatingFileHandler: si path > max_bytes, lo movemos
    a path.1; lo anterior .1 → .2, etc.; el ultimo se descarta.

    No-op si max_bytes <= 0, si backups <= 0, o si el archivo no supera
    el limite. Tolerante a errores de filesystem (warning, no excepcion).
    """
    try:
        if max_bytes <= 0 or backups <= 0:
            return
        if not path.exists() or path.stat().st_size < max_bytes:
            return
        # Borrar el mas viejo.
        oldest = path.with_suffix(path.suffix + f".{backups}")
        if oldest.exists():
            oldest.unlink()
        # Renombrar .N → .N+1, desde el penultimo hacia abajo.
        for i in range(backups - 1, 0, -1):
            src = path.with_suffix(path.suffix + f".{i}")
            dst = path.with_suffix(path.suffix + f".{i+1}")
            if src.exists():
                src.rename(dst)
        # path → path.1
        path.rename(path.with_suffix(path.suffix + ".1"))
    except Exception as e:
        log.warning("no se pudo rotar cost_log: %s", e)


def summary(last_n_days: int = 7) -> dict:
    """Resumen agregado del JSONL para el comando /cost."""
    path = config.COST_LOG_FILE
    if not path.exists():
        return {"days": last_n_days, "events": 0,
                "input_tokens": 0, "output_tokens": 0,
                "total_usd": 0.0, "by_model": {}, "by_route": {},
                "source": "jsonl"}
    cutoff = datetime.now(timezone.utc).timestamp() - last_n_days * 86400
    total = 0.0
    events = 0
    by_model: dict = {}
    by_route: dict = {}
    in_tok = 0
    out_tok = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            ts = datetime.fromisoformat(r["ts"]).timestamp()
        except Exception:
            continue
        if ts < cutoff:
            continue
        events += 1
        c = float(r.get("cost_usd") or 0)
        total += c
        in_tok += int(r.get("input_tokens") or 0)
        out_tok += int(r.get("output_tokens") or 0)
        m = r.get("model") or "-"
        by_model[m] = round(by_model.get(m, 0.0) + c, 6)
        rt = r.get("route") or "-"
        by_route[rt] = by_route.get(rt, 0) + 1
    return {"days": last_n_days, "events": events,
            "input_tokens": in_tok, "output_tokens": out_tok,
            "total_usd": round(total, 4),
            "by_model": by_model, "by_route": by_route,
            "source": "jsonl"}


async def summary_db(last_n_days: int = 7) -> dict:
    """Como summary() pero leyendo de la tabla cost_logs. Si la DB no esta
    habilitada o falla, cae al JSONL (mantiene compat y resiliencia)."""
    try:
        import db as db_mod
        import models
        if not db_mod.is_postgres_enabled():
            return summary(last_n_days)
        from sqlalchemy import select
        cutoff = datetime.now(timezone.utc) - timedelta(days=last_n_days)
        async with db_mod.session_scope() as s:
            rows = (await s.execute(
                select(models.CostLog).where(models.CostLog.ts >= cutoff)
            )).scalars().all()
    except Exception as e:
        log.warning("summary_db fallo (%s), fallback a JSONL", e)
        return summary(last_n_days)

    total = 0.0
    in_tok = 0
    out_tok = 0
    by_model: dict = {}
    by_route: dict = {}
    for r in rows:
        total += float(r.cost_usd or 0)
        in_tok += int(r.input_tokens or 0)
        out_tok += int(r.output_tokens or 0)
        m = r.model or "-"
        by_model[m] = round(by_model.get(m, 0.0) + float(r.cost_usd or 0), 6)
        rt = r.route or "-"
        by_route[rt] = by_route.get(rt, 0) + 1
    return {"days": last_n_days, "events": len(rows),
            "input_tokens": in_tok, "output_tokens": out_tok,
            "total_usd": round(total, 4),
            "by_model": by_model, "by_route": by_route,
            "source": "postgres"}
