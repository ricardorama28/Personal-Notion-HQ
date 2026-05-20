#!/usr/bin/env bash
# pg_dump del Postgres del compose, comprimido, con timestamp y retencion.
#
# Uso:
#   bash scripts/backup.sh                 # backup a ./backups/
#   BACKUP_DIR=/mnt/foo bash scripts/backup.sh
#   BACKUP_RETAIN=30 bash scripts/backup.sh
#
# Restore:
#   gunzip -c backups/wpp_YYYYMMDD_HHMMSS.sql.gz | \
#     docker compose exec -T postgres psql -U wpp -d wpp
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_RETAIN="${BACKUP_RETAIN:-14}"
mkdir -p "$BACKUP_DIR"

# Tomar credenciales del .env si existe (sin pisar lo que ya este en env).
if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a; source .env; set +a
fi
PG_USER="${POSTGRES_USER:-wpp}"
PG_DB="${POSTGRES_DB:-wpp}"

# Detectar docker compose v2 vs v1.
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
else
  COMPOSE="docker-compose"
fi

DATE=$(date +%Y%m%d_%H%M%S)
FILE="$BACKUP_DIR/wpp_${DATE}.sql.gz"

echo "[backup] pg_dump -> $FILE"
$COMPOSE exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" \
    --clean --if-exists --no-owner --no-privileges \
  | gzip -9 > "$FILE"

size=$(du -h "$FILE" | cut -f1)
echo "[backup] ✓ $FILE ($size)"

# Retencion: mantener los ultimos N.
to_delete=$(ls -1t "$BACKUP_DIR"/wpp_*.sql.gz 2>/dev/null | tail -n +$((BACKUP_RETAIN+1)) || true)
if [[ -n "$to_delete" ]]; then
  echo "[backup] borrando viejos (retencion=$BACKUP_RETAIN):"
  echo "$to_delete" | xargs -r rm -v
fi
