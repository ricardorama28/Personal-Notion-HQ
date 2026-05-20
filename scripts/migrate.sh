#!/usr/bin/env bash
# Aplica las migraciones Alembic. Pensado para ser invocado:
#   - como entrypoint en Docker (`scripts/migrate.sh && uvicorn ...`)
#   - manualmente en local/Railway antes de un deploy
#
# Requiere DATABASE_URL en el entorno. Si SESSIONS_BACKEND=file, no hace
# nada (cero dependencia con Postgres).
set -euo pipefail

if [[ "${SESSIONS_BACKEND:-file}" != "postgres" ]]; then
  echo "[migrate] SESSIONS_BACKEND != postgres → skip"
  exit 0
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[migrate] DATABASE_URL no esta seteada con SESSIONS_BACKEND=postgres" >&2
  exit 1
fi

# Espera hasta 30s a que Postgres responda (util cuando se invoca en compose
# antes del healthcheck completo). Hace SELECT 1 via python+sqlalchemy para
# evitar depender de psql en la imagen.
python - <<'PY'
import asyncio, os, sys, time
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

url = os.environ["DATABASE_URL"]
deadline = time.time() + 30
async def wait():
    last = None
    while time.time() < deadline:
        try:
            eng = create_async_engine(url)
            async with eng.connect() as c:
                await c.execute(text("SELECT 1"))
            await eng.dispose()
            return
        except Exception as e:
            last = e
            await asyncio.sleep(1)
    print(f"[migrate] DB no responde tras 30s: {last}", file=sys.stderr)
    sys.exit(2)
asyncio.run(wait())
PY

echo "[migrate] aplicando alembic upgrade head"
alembic upgrade head
echo "[migrate] ok"
