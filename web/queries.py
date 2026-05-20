"""Queries de lectura para la UI.

Nada aca debe filtrar secrets, system prompts completos ni env vars.
Las funciones devuelven dicts listos para los templates.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import desc, func, select

import config
import db as db_mod
import models

log = logging.getLogger("wpp.web.q")


def secrets_redacted_config() -> dict:
    """Snapshot de flags publicos. Sin tokens ni API keys."""
    return {
        "ASYNC_ENABLED": config.ASYNC_ENABLED,
        "SESSIONS_BACKEND": config.SESSIONS_BACKEND,
        "ENABLE_DOCS": config.ENABLE_DOCS,
        "TWILIO_VALIDATE": config.TWILIO_VALIDATE,
        "ROUTER_MODEL": config.ROUTER_MODEL,
        "ORCHESTRATOR_MODEL": config.ORCHESTRATOR_MODEL,
        "ROUTER_ENABLED": config.ROUTER_ENABLED,
        "MAX_TOOL_ITERATIONS": config.MAX_TOOL_ITERATIONS,
        "CONFIRMATION_TTL_MINUTES": config.CONFIRMATION_TTL_MINUTES,
        # solo true/false: no exponemos valores
        "NOTION_TOKEN_set": bool(config.NOTION_TOKEN),
        "ANTHROPIC_API_KEY_set": bool(config.ANTHROPIC_API_KEY),
        "TWILIO_AUTH_TOKEN_set": bool(config.TWILIO_AUTH_TOKEN),
        "TWILIO_ACCOUNT_SID_set": bool(config.TWILIO_ACCOUNT_SID),
        "ADMIN_TOKEN_set": bool(config.ADMIN_TOKEN),
    }


async def list_sessions(source: Optional[str] = None, limit: int = 50) -> list:
    """Lista de sesiones (file backend → vacio, no es queryable con SQL)."""
    if not db_mod.is_postgres_enabled():
        return []
    async with db_mod.session_scope() as s:
        stmt = select(models.Session)
        if source:
            stmt = stmt.where(models.Session.source == source)
        # Evitamos nulls_last() (no portable a SQLite). Ordenamos por
        # updated_at desc, que siempre esta seteado.
        stmt = stmt.order_by(desc(models.Session.updated_at)).limit(limit)
        rows = (await s.execute(stmt)).scalars().all()
        out = []
        for r in rows:
            hist = r.history or []
            last = hist[-1] if hist else None
            preview = ""
            if last:
                content = last.get("content")
                if isinstance(content, str):
                    preview = content[:80]
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            preview = (block.get("text") or "")[:80]
                            break
            out.append({
                "key": r.key, "source": r.source or "?",
                "n_messages": len(hist),
                "updated_at": r.updated_at, "last_message_at": r.last_message_at,
                "preview": preview,
            })
        return out


async def session_runs(session_key: str, limit: int = 20) -> list:
    if not db_mod.is_postgres_enabled():
        return []
    async with db_mod.session_scope() as s:
        rows = (await s.execute(
            select(models.AgentRun)
            .where(models.AgentRun.session_key == session_key)
            .order_by(desc(models.AgentRun.started_at)).limit(limit)
        )).scalars().all()
        return [_run_to_dict(r) for r in rows]


async def session_cost_summary(session_key: str) -> dict:
    if not db_mod.is_postgres_enabled():
        return {"total_usd": 0.0, "messages": 0,
                "input_tokens": 0, "output_tokens": 0}
    async with db_mod.session_scope() as s:
        rows = (await s.execute(
            select(models.AgentRun)
            .where(models.AgentRun.session_key == session_key)
        )).scalars().all()
        total = sum(0.0 for _ in rows)  # ver cost_logs vinculados via sid
        in_tok = sum((r.input_tokens or 0) for r in rows)
        out_tok = sum((r.output_tokens or 0) for r in rows)
        # cost_usd lo agrega cost_logs; agregamos lo de runs
        cost_rows = (await s.execute(
            select(func.coalesce(func.sum(models.CostLog.cost_usd), 0.0))
            .where(models.CostLog.sid.in_(
                select(models.AgentRun.sid)
                .where(models.AgentRun.session_key == session_key,
                       models.AgentRun.sid.is_not(None))
            ))
        )).scalar() or 0.0
        return {"total_usd": round(float(cost_rows), 4),
                "messages": len(rows),
                "input_tokens": in_tok, "output_tokens": out_tok}


async def get_run(run_id: str) -> Optional[dict]:
    if not db_mod.is_postgres_enabled() or not run_id:
        return None
    async with db_mod.session_scope() as s:
        row = await s.get(models.AgentRun, run_id)
        if row is None:
            return None
        d = _run_to_dict(row)
        tcs = (await s.execute(
            select(models.ToolCall)
            .where(models.ToolCall.agent_run_id == run_id)
            .order_by(models.ToolCall.ts)
        )).scalars().all()
        d["tool_calls"] = [{
            "id": t.id, "name": t.name, "args": t.args, "result": t.result,
            "ok": t.ok, "ts": t.ts,
        } for t in tcs]
        return d


def _run_to_dict(r: "models.AgentRun") -> dict:
    return {
        "id": r.id, "sid": r.sid, "session_key": r.session_key,
        "route": r.route, "intent": r.intent, "model": r.model,
        "safety_level": r.safety_level, "async_state": r.async_state,
        "confirmed_from": r.confirmed_from,
        "input_tokens": r.input_tokens or 0,
        "output_tokens": r.output_tokens or 0,
        "iterations": r.iterations or 0,
        "started_at": r.started_at, "finished_at": r.finished_at,
        "reply": r.reply or "", "error": r.error or "",
        "plan": r.plan or {},
    }


async def recent_runs(limit: int = 30, async_state: Optional[str] = None
                      ) -> list:
    if not db_mod.is_postgres_enabled():
        return []
    async with db_mod.session_scope() as s:
        stmt = select(models.AgentRun)
        if async_state:
            stmt = stmt.where(models.AgentRun.async_state == async_state)
        stmt = stmt.order_by(desc(models.AgentRun.started_at)).limit(limit)
        rows = (await s.execute(stmt)).scalars().all()
        return [_run_to_dict(r) for r in rows]


async def pending_confirmations() -> list:
    if not db_mod.is_postgres_enabled():
        return []
    now = datetime.now(timezone.utc)
    async with db_mod.session_scope() as s:
        rows = (await s.execute(
            select(models.PendingConfirmation)
            .where(models.PendingConfirmation.consumed.is_(False),
                   models.PendingConfirmation.expires_at > now)
            .order_by(desc(models.PendingConfirmation.created_at))
        )).scalars().all()
        return [{
            "id": r.id, "session_key": r.session_key,
            "payload": r.payload, "expires_at": r.expires_at,
            "created_at": r.created_at,
        } for r in rows]


async def alerts() -> list:
    """Computa alertas en tiempo real."""
    out = []
    if not db_mod.is_postgres_enabled():
        out.append({"level": "info",
                    "msg": "Backend=file: la mayoria de las vistas "
                           "necesita Postgres."})
        return out
    # twilio outbound mal configurado pero async on
    if config.ASYNC_ENABLED and not (config.TWILIO_ACCOUNT_SID
                                     and config.TWILIO_FROM_WHATSAPP):
        out.append({"level": "warn",
                    "msg": "ASYNC_ENABLED=true pero Twilio outbound no "
                           "esta configurado: workers no van a mandar "
                           "WhatsApp final."})
    async with db_mod.session_scope() as s:
        # async_error en las ultimas 24h
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        n_err = (await s.execute(
            select(func.count()).select_from(models.AgentRun)
            .where(models.AgentRun.async_state == "async_error",
                   models.AgentRun.started_at >= cutoff)
        )).scalar() or 0
        if n_err:
            out.append({"level": "warn",
                        "msg": f"{n_err} async_error en las ultimas 24h"})
        # blocked unsafe
        n_blocked = (await s.execute(
            select(func.count()).select_from(models.AgentRun)
            .where(models.AgentRun.route == "blocked",
                   models.AgentRun.started_at >= cutoff)
        )).scalar() or 0
        if n_blocked:
            out.append({"level": "info",
                        "msg": f"{n_blocked} planes bloqueados (unsafe) en 24h"})
        # confirmaciones vencidas sin consumir
        n_exp = (await s.execute(
            select(func.count()).select_from(models.PendingConfirmation)
            .where(models.PendingConfirmation.consumed.is_(False),
                   models.PendingConfirmation.expires_at < datetime.now(timezone.utc))
        )).scalar() or 0
        if n_exp:
            out.append({"level": "info",
                        "msg": f"{n_exp} confirmaciones vencidas sin "
                               f"consumir (purgar con repos.confirmations.purge_expired)"})
    return out


async def cost_breakdown(days: int = 7) -> dict:
    if not db_mod.is_postgres_enabled():
        return {"days": days, "total_usd": 0.0, "by_day": [],
                "by_model": {}, "by_route": {}, "messages": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with db_mod.session_scope() as s:
        rows = (await s.execute(
            select(models.CostLog).where(models.CostLog.ts >= cutoff)
        )).scalars().all()
    by_model: dict = {}
    by_route: dict = {}
    by_day: dict = {}
    total = 0.0
    in_tok = 0
    out_tok = 0
    for r in rows:
        c = float(r.cost_usd or 0)
        total += c
        in_tok += int(r.input_tokens or 0)
        out_tok += int(r.output_tokens or 0)
        m = r.model or "-"
        by_model[m] = round(by_model.get(m, 0.0) + c, 6)
        rt = r.route or "-"
        by_route[rt] = by_route.get(rt, 0) + 1
        d = r.ts.date().isoformat() if r.ts else "?"
        by_day[d] = round(by_day.get(d, 0.0) + c, 6)
    by_day_list = sorted(by_day.items())
    return {"days": days, "total_usd": round(total, 4),
            "input_tokens": in_tok, "output_tokens": out_tok,
            "by_day": by_day_list, "by_model": by_model,
            "by_route": by_route, "messages": len(rows)}


def agents_overview() -> list:
    """Vista de agentes registrados. Solo nombres, modelo, tools.
    NO devuelve el system prompt completo (puede contener instrucciones
    sensibles); devuelve un resumen del prompt (primeras N lineas)."""
    import agents as agent_pkg
    out = []
    for name, a in sorted(agent_pkg.AGENT_REGISTRY.items()):
        first_lines = [ln for ln in (a.system_prompt or "").splitlines()
                       if ln.strip()][:3]
        out.append({
            "name": a.name, "model": a.default_model,
            "tools": sorted(a.allowed_tools),
            "prompt_summary": "\n".join(first_lines)[:300],
        })
    return out


async def tool_usage(days: int = 7) -> list:
    """Conteo y errores por tool en los ultimos N dias."""
    if not db_mod.is_postgres_enabled():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with db_mod.session_scope() as s:
        rows = (await s.execute(
            select(models.ToolCall.name,
                   func.count().label("n"),
                   func.sum(func.cast(models.ToolCall.ok, models.JSON)).label("ok_sum"))
            .where(models.ToolCall.ts >= cutoff)
            .group_by(models.ToolCall.name)
        )).all()
    # Lo anterior puede fallar por cast en SQLite; lo hacemos manual:
    async with db_mod.session_scope() as s:
        all_rows = (await s.execute(
            select(models.ToolCall.name, models.ToolCall.ok)
            .where(models.ToolCall.ts >= cutoff)
        )).all()
    counts: dict = {}
    for name, ok in all_rows:
        d = counts.setdefault(name, {"n": 0, "ok": 0, "err": 0})
        d["n"] += 1
        if ok:
            d["ok"] += 1
        else:
            d["err"] += 1
    return [{"name": k, **v} for k, v in sorted(counts.items(),
                                                key=lambda kv: -kv[1]["n"])]


async def recent_errors(limit: int = 20) -> list:
    if not db_mod.is_postgres_enabled():
        return []
    async with db_mod.session_scope() as s:
        rows = (await s.execute(
            select(models.AgentRun)
            .where(models.AgentRun.error.is_not(None))
            .order_by(desc(models.AgentRun.started_at)).limit(limit)
        )).scalars().all()
        return [_run_to_dict(r) for r in rows]
