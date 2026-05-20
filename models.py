"""Modelos SQLAlchemy para la persistencia en Postgres (Fase C).

Compatibles con Postgres (asyncpg) y SQLite (aiosqlite) para tests. Tipos
JSON funcionan en ambos; UUIDs como String(36) para portabilidad.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Message(Base):
    """Cada mensaje (inbound o outbound) por WhatsApp."""
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    sid: Mapped[str | None] = mapped_column(String(64), unique=True,
                                            nullable=True, index=True)
    sender: Mapped[str] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(16))  # inbound|outbound
    body: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True)
    inbox_page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Session(Base):
    """Historial de conversacion (reemplaza /tmp/wpp_sessions.json)."""
    __tablename__ = "sessions"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    history: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    # Fase I: distinguir sesiones web vs whatsapp para la UI.
    source: Mapped[str | None] = mapped_column(String(16), nullable=True,
                                                index=True)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class AgentRun(Base):
    """Una corrida del loop de tool use o de la regla del router."""
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    sid: Mapped[str | None] = mapped_column(String(64), nullable=True,
                                            index=True)
    session_key: Mapped[str | None] = mapped_column(String(64), nullable=True,
                                                    index=True)
    route: Mapped[str] = mapped_column(String(32))  # rule|haiku_agent|sonnet_agent|admin|error
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fase F: el ActionPlan completo serializado y la clasificacion de
    # seguridad. confirmed_from = id del pending_confirmation que disparo
    # esta corrida (null si fue ejecucion directa).
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    safety_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confirmed_from: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Fase H: estado del ejecutor async.
    # None = sync; async_pending | async_running | async_done | async_error
    async_state: Mapped[str | None] = mapped_column(String(16), nullable=True)


class ToolCall(Base):
    """Cada tool_use ejecutado durante un AgentRun (o por una regla)."""
    __tablename__ = "tool_calls"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True,
                                                     index=True)
    sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)


class CostLog(Base):
    """Mismo esquema que las filas del JSONL (cost_log.py) pero en SQL."""
    __tablename__ = "cost_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True)
    sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    route: Mapped[str] = mapped_column(String(32))
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class PendingConfirmation(Base):
    """Confirmaciones pendientes (Fase F las usa; tabla ya disponible)."""
    __tablename__ = "pending_confirmations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_key: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
