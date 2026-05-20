"""Engine async de SQLAlchemy y session factory.

El engine se construye lazy en `get_engine()` para que importar `db` no
abra conexiones (importante en tests con backend=file que ni siquiera
necesitan DB).
"""
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (AsyncEngine, AsyncSession,
                                    async_sessionmaker, create_async_engine)

import config
import models

log = logging.getLogger("wpp.db")

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def is_postgres_enabled() -> bool:
    return config.SESSIONS_BACKEND == "postgres" and bool(config.DATABASE_URL)


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        if not config.DATABASE_URL:
            raise RuntimeError("DATABASE_URL no esta seteada")
        _engine = create_async_engine(
            config.DATABASE_URL,
            future=True,
            # echo no por defecto: evita filtrar SQL con secretos en logs
            echo=False,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
        log.info("engine creado (driver=%s)",
                 config.DATABASE_URL.split("://", 1)[0])
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    return _sessionmaker  # type: ignore[return-value]


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Sesion con commit/rollback automatico."""
    sm = get_sessionmaker()
    async with sm() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


async def create_all_for_tests() -> None:
    """Crea las tablas via metadata. Solo para tests; en prod usar Alembic."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
