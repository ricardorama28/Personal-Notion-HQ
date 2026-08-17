# syntax=docker/dockerfile:1.7

# ----------------------------------------------------------------------------
# Stage 1: builder. Compila wheels para todas las deps de runtime.
# ----------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Deps del compilador para asyncpg/psycopg/etc. Solo en builder.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# ----------------------------------------------------------------------------
# Stage 2: runtime. Solo lo que la app necesita para correr.
# ----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# bash para scripts/migrate.sh; tini para señales/PID 1.
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash tini ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app

WORKDIR /app

# Wheels desde el builder. Instalacion a nivel SISTEMA, no `--user`: este RUN
# corre como root (el USER app viene mas abajo), asi que `--user` mandaba todo
# a /root/.local/, invisible para el usuario `app` que corre el proceso. El
# sintoma era un crash-loop con "ModuleNotFoundError: No module named
# 'sqlalchemy'" desde el heredoc de scripts/migrate.sh, que cortaba el `&&`
# del CMD y hacia que uvicorn nunca arrancara.
# En /usr/local/lib/python3.11/site-packages los ve cualquier usuario, y los
# ejecutables (uvicorn, alembic) quedan en /usr/local/bin, ya en el PATH.
COPY --from=builder /wheels /wheels
COPY requirements.txt ./
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Codigo de la app.
COPY --chown=app:app . /app

# /data es el volumen persistente (sessions JSON, cost log JSONL).
# Lo creamos y le damos permisos antes de dropear privilegios.
RUN mkdir -p /data && chown -R app:app /data /app

USER app

EXPOSE 8000

# Healthcheck: golpea /health y aprueba si database_ok != false y ok=true.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python scripts/healthcheck.py || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
# migrate.sh hace no-op si SESSIONS_BACKEND=file, asi el contenedor funciona
# en ambos modos sin condicionales aca.
CMD ["bash", "-c", "scripts/migrate.sh && exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
