"""Repositorios de persistencia.

- `SessionRepo` se ramifica por backend (`file` o `postgres`) y reemplaza
  los antiguos `load_sessions/save_sessions`.
- Los otros repos (`MessageRepo`, `AgentRunRepo`, `ToolCallRepo`,
  `CostLogRepo`, `PendingConfirmationRepo`) son no-op cuando el backend
  es `file`, asi el call site no necesita branchear.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select, update

import config
import db
import models

log = logging.getLogger("wpp.repos")


# ---------- Sessions ----------

class SessionRepo:
    """Carga/guarda el historial de mensajes por usuario.

    backend=file → JSON en config.SESSIONS_FILE (compat MVP).
    backend=postgres → tabla `sessions`.
    """

    @staticmethod
    def _read_file() -> dict:
        if config.SESSIONS_FILE.exists():
            try:
                return json.loads(config.SESSIONS_FILE.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _write_file(sessions: dict) -> None:
        config.SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.SESSIONS_FILE.write_text(json.dumps(sessions, ensure_ascii=False))

    async def load(self, key: str) -> list:
        if config.SESSIONS_BACKEND == "postgres":
            async with db.session_scope() as s:
                row = await s.get(models.Session, key)
                return list(row.history) if row else []
        return self._read_file().get(key, [])

    async def save(self, key: str, history: list) -> None:
        if config.SESSIONS_BACKEND == "postgres":
            async with db.session_scope() as s:
                row = await s.get(models.Session, key)
                if row is None:
                    s.add(models.Session(key=key, history=history))
                else:
                    row.history = history
                    row.updated_at = datetime.now(timezone.utc)
            return
        sessions = self._read_file()
        sessions[key] = history
        self._write_file(sessions)

    async def clear(self, key: str) -> None:
        if config.SESSIONS_BACKEND == "postgres":
            async with db.session_scope() as s:
                await s.execute(
                    delete(models.Session).where(models.Session.key == key)
                )
            return
        sessions = self._read_file()
        sessions[key] = []
        self._write_file(sessions)


# ---------- Otros repos (no-op si backend != postgres) ----------

class MessageRepo:
    async def add(self, *, sid: Optional[str], sender: str, body: str,
                  direction: str = "inbound",
                  inbox_page_id: Optional[str] = None) -> Optional[str]:
        if not db.is_postgres_enabled():
            return None
        async with db.session_scope() as s:
            m = models.Message(sid=sid, sender=sender, body=body,
                               direction=direction,
                               inbox_page_id=inbox_page_id)
            s.add(m)
            await s.flush()
            return m.id


class AgentRunRepo:
    async def create(self, *, sid: Optional[str], session_key: Optional[str],
                     route: str, intent: Optional[str],
                     model: Optional[str],
                     plan: Optional[dict] = None,
                     safety_level: Optional[str] = None,
                     confirmed_from: Optional[str] = None,
                     async_state: Optional[str] = None) -> Optional[str]:
        if not db.is_postgres_enabled():
            return None
        async with db.session_scope() as s:
            run = models.AgentRun(sid=sid, session_key=session_key, route=route,
                                  intent=intent, model=model,
                                  plan=plan, safety_level=safety_level,
                                  confirmed_from=confirmed_from,
                                  async_state=async_state)
            s.add(run)
            await s.flush()
            return run.id

    async def finish(self, run_id: Optional[str], *, input_tokens: int = 0,
                     output_tokens: int = 0, iterations: int = 0,
                     reply: Optional[str] = None,
                     error: Optional[str] = None) -> None:
        if not run_id or not db.is_postgres_enabled():
            return
        async with db.session_scope() as s:
            await s.execute(
                update(models.AgentRun)
                .where(models.AgentRun.id == run_id)
                .values(finished_at=datetime.now(timezone.utc),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        iterations=iterations, reply=reply, error=error)
            )


class ToolCallRepo:
    async def add(self, *, agent_run_id: Optional[str], sid: Optional[str],
                  name: str, args: dict, result: dict) -> Optional[str]:
        if not db.is_postgres_enabled():
            return None
        ok = "error" not in (result or {})
        async with db.session_scope() as s:
            tc = models.ToolCall(agent_run_id=agent_run_id, sid=sid, name=name,
                                 args=args, result=result, ok=ok)
            s.add(tc)
            await s.flush()
            return tc.id


class CostLogRepo:
    async def add(self, record: dict) -> None:
        if not db.is_postgres_enabled():
            return
        async with db.session_scope() as s:
            s.add(models.CostLog(
                ts=_parse_ts(record.get("ts")),
                sid=record.get("sid"),
                route=record.get("route") or "-",
                intent=record.get("intent"),
                model=record.get("model"),
                input_tokens=int(record.get("input_tokens") or 0),
                output_tokens=int(record.get("output_tokens") or 0),
                cost_usd=float(record.get("cost_usd") or 0.0),
                extra={k: v for k, v in record.items()
                       if k not in {"ts", "sid", "route", "intent", "model",
                                    "input_tokens", "output_tokens",
                                    "cost_usd"}} or None,
            ))


def _parse_ts(raw) -> datetime:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


class PendingConfirmationRepo:
    async def create(self, *, session_key: str, payload: dict,
                     ttl_minutes: Optional[int] = None) -> Optional[str]:
        if not db.is_postgres_enabled():
            return None
        ttl = ttl_minutes or config.CONFIRMATION_TTL_MINUTES
        expires = datetime.now(timezone.utc) + timedelta(minutes=ttl)
        async with db.session_scope() as s:
            row = models.PendingConfirmation(
                session_key=session_key, payload=payload,
                expires_at=expires)
            s.add(row)
            await s.flush()
            return row.id

    async def pop_latest(self, session_key: str) -> Optional[dict]:
        """Devuelve y consume la confirmacion vigente mas reciente."""
        if not db.is_postgres_enabled():
            return None
        now = datetime.now(timezone.utc)
        async with db.session_scope() as s:
            stmt = (select(models.PendingConfirmation)
                    .where(models.PendingConfirmation.session_key == session_key,
                           models.PendingConfirmation.consumed.is_(False),
                           models.PendingConfirmation.expires_at > now)
                    .order_by(models.PendingConfirmation.created_at.desc())
                    .limit(1))
            row = (await s.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            row.consumed = True
            return dict(row.payload)

    async def purge_expired(self) -> int:
        if not db.is_postgres_enabled():
            return 0
        now = datetime.now(timezone.utc)
        async with db.session_scope() as s:
            res = await s.execute(
                delete(models.PendingConfirmation)
                .where(models.PendingConfirmation.expires_at <= now)
            )
            return res.rowcount or 0


# Singletons para callers.
sessions = SessionRepo()
messages = MessageRepo()
agent_runs = AgentRunRepo()
tool_calls = ToolCallRepo()
cost_logs = CostLogRepo()
confirmations = PendingConfirmationRepo()
