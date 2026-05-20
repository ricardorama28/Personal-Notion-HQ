# Operación diaria

## Comandos rápidos

```bash
make logs                  # tail -f de todos los servicios
make ps                    # estado de los contenedores
make tunnel-status         # confirma que el dominio público responde
make psql                  # psql adentro del contenedor postgres
make shell                 # bash en el contenedor web
make backup                # pg_dump comprimido en ./backups/
```

## Rutina diaria

- [ ] Mirar `/admin/alerts` al menos una vez (errores async, blocked,
      confirmaciones vencidas).
- [ ] Mirar `/admin/costs` si te interesa monitorear gasto Anthropic.
- [ ] Si hay run en `async_error`: abrir detalle, decidir si reintentar
      (botón solo aparece para `safety_level=safe`).
- [ ] Confirmar que el monitor externo (UptimeRobot) no disparó alertas
      durante la noche.

## Operaciones comunes

### Mandar un comando admin desde WhatsApp

```
/reset    → limpia historial de la sesión actual
/cost     → resumen de gasto 7 días
/status   → idem
```

### Aprobar/cancelar confirmaciones

- **Por WhatsApp**: responder `1` (aprobar) o `cancelar`.
- **Por web**: abrir `/admin/c/<key>` o `/admin/`, tocar los botones de
  la tarjeta amarilla.

### Reintentar un run fallido

- Solo si `safety_level == safe`.
- `/admin/runs/<id>` → botón **Reintentar** (visible solo cuando
  corresponde).
- El retry encola en background; mirar `/admin/runs?async_state=async_running`.

### Pasar de WhatsApp a chat web (o viceversa)

Sesiones de WhatsApp tienen key `whatsapp:+54...`; sesiones web `web:<uuid>`.
Son independientes a propósito (distinto contexto). Si querés trasladar
contexto, copiá/pegá los últimos N mensajes a mano.

### Aumentar/disminuir budget de Anthropic

Sin budget cap propio. Mitigación: bajar `MAX_TOOL_ITERATIONS` (default 8)
en `.env` y `make rebuild`. O bajar `ROUTER_CONFIDENCE_THRESHOLD` para
que más mensajes vayan a Sonnet con menos chequeo (gasto sube).

### Cambiar el modo del chat web (Auto/Capture/...)

Hoy el selector es decorativo; el orquestador decide. Forzar un agente
específico requiere pasar `?agent=<name>` por query (no implementado en
v0.1.0).

## Updates

```bash
git fetch
git log --oneline v0.1.0..origin/main  # ver qué cambia
git pull
make rebuild                            # rebuild + up; Alembic corre solo
make logs                               # confirmar arranque limpio
```

Si algo se rompe: ver sección rollback en `docs/ROLLBACK.md`.

## Inspección rápida vía SQL

```sql
-- Últimos 20 mensajes
SELECT received_at, direction, body FROM messages
  ORDER BY received_at DESC LIMIT 20;

-- Runs en error en las últimas 24 h
SELECT id, intent, safety_level, error FROM agent_runs
  WHERE error IS NOT NULL AND started_at > now() - interval '24 hours';

-- Costo por modelo, últimos 7 días
SELECT model, count(*), sum(cost_usd) FROM cost_logs
  WHERE ts > now() - interval '7 days' GROUP BY model;

-- Sesiones web activas
SELECT key, n_messages := jsonb_array_length(history::jsonb), updated_at
  FROM sessions WHERE source = 'web' ORDER BY updated_at DESC;

-- Confirmaciones vivas
SELECT id, session_key, payload->>'intent' AS intent, expires_at
  FROM pending_confirmations WHERE consumed = false
    AND expires_at > now() ORDER BY created_at DESC;
```

## Backup / Restore

### Backup manual

```bash
make backup                       # ./backups/wpp_YYYYMMDD_HHMMSS.sql.gz
BACKUP_DIR=/mnt/foo make backup   # destino custom
BACKUP_RETAIN=30 make backup      # cambia retención
```

### Restore

```bash
make restore FILE=backups/wpp_20260520_030000.sql.gz
```

⚠️ El dump usa `--clean --if-exists` → sobreescribe la DB actual.

### Probar restore (recomendado al menos una vez)

```bash
docker volume create wpp_restore_test
docker run --rm -d --name pg-test \
  -v wpp_restore_test:/var/lib/postgresql/data \
  -e POSTGRES_PASSWORD=test -p 127.0.0.1:55432:5432 \
  postgres:16.4-alpine
sleep 5
gunzip -c backups/wpp_<timestamp>.sql.gz \
  | psql -h 127.0.0.1 -p 55432 -U postgres
psql -h 127.0.0.1 -p 55432 -U postgres -c "select count(*) from messages;"
docker rm -f pg-test && docker volume rm wpp_restore_test
```

## Rotación de tokens

### Anthropic

1. Generar nuevo en console.anthropic.com.
2. `.env` → `ANTHROPIC_API_KEY=<nuevo>`.
3. `make rebuild`.
4. Revocar el viejo en la console.

### Twilio Auth Token

1. Twilio Console → Account → Auth Token → rotate.
2. `.env` → `TWILIO_AUTH_TOKEN=<nuevo>`.
3. `make rebuild`.
4. ⚠️ Mandar un mensaje de prueba: si el bot responde 403 a Twilio, hay
   discordancia entre el token rotado y el que firma; revisar.

### Admin token

```bash
new=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
# editar .env: ADMIN_TOKEN=$new
make rebuild
# Re-loguearse en /admin/login con el nuevo
```

### Cloudflare Tunnel token

1. Cloudflare Zero Trust → Tunnels → tu túnel → Refresh token.
2. `.env` → `CF_TUNNEL_TOKEN=<nuevo>`.
3. `docker compose restart cloudflared`.

## Una vez por mes

- [ ] Probar `make restore` contra un volumen test.
- [ ] Revisar `docker system df -v` (tamaño de volúmenes).
- [ ] Ver releases nuevas de `postgres` y `cloudflared` y considerar
      bump de pins en `docker-compose.yml`.
- [ ] Rotar `ADMIN_TOKEN` si pasó tiempo.
- [ ] `make backup` manual extra antes de cualquier cambio grande.
