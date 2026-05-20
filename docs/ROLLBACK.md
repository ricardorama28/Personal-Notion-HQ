# Rollback

Tres escenarios cubiertos: (A) volver a un commit anterior local, (B)
switch de Twilio webhook a Railway, (C) restore de Postgres desde backup.

---

## A. Rollback de código (PC self-hosted)

Cuando un `git pull && make rebuild` rompió algo y querés volver atrás.

```bash
# 1. Ver el último tag estable que funcionaba
git tag --sort=-v:refname | head -5

# 2. Volver al tag
git checkout v0.1.0

# 3. Rebuild
make rebuild

# 4. Confirmar arranque
make logs
curl -s http://localhost:8000/health
```

Si una migración nueva ya corrió y no es compatible con el código viejo:

```bash
# Bajar una revisión
make shell
alembic downgrade -1
exit
make rebuild
```

Si seguís rompiendo, ir al escenario C (restore de backup).

---

## B. Switch del webhook Twilio → Railway

Sirve cuando la PC propia está caída, sin energía, sin red, o querés
mantenimiento programado.

### Pre-requisitos

- [ ] Una app Railway desplegada con el mismo repo.
- [ ] Variables en Railway:
  ```
  NOTION_TOKEN, ANTHROPIC_API_KEY, TWILIO_AUTH_TOKEN, MY_WHATSAPP
  TWILIO_VALIDATE=true
  SESSIONS_BACKEND=file        # Railway corre con file backend
  ROUTER_ENABLED=true          # opcional
  ADMIN_TOKEN=<otro>           # si querés UI en Railway también
  + los 9 DB IDs de Notion
  ```
- [ ] `Procfile` intacto en el repo (lo está en v0.1.0).

### Pasos

1. **Twilio Console** → Develop → Messaging → Sandbox/Sender settings.
2. **When a message comes in** → cambiar URL a
   `https://<tu-app>.up.railway.app/webhook`.
3. **Method**: `POST`.
4. Guardar.
5. Mandar un mensaje de prueba: `gasto 50 cafe`. Confirmar respuesta.
6. (Opcional) `make tunnel-down` en la PC para liberar recursos.

### Diferencias durante el fallback

| Cosa | PC postgres | Railway file |
|---|---|---|
| Historial de sesión | Postgres, sobrevive | `/tmp` efímero |
| `messages`, `agent_runs`, etc. | persistidos | no existen |
| `/cost` | desde SQL | JSONL local efímero |
| `/admin/*` | UI completa | tablas vacías |
| Async workers | sí | sin Twilio outbound configurado |
| Confirmaciones destructive | sí | rechazadas (no hay tabla pending) |

### Volver a la PC

1. **Twilio Console** → webhook URL → `https://$PUBLIC_WEBHOOK_HOST/webhook`.
2. `make tunnel-up` en la PC.
3. `make tunnel-status` debe responder 200.
4. Mensaje de prueba.

---

## C. Restore de Postgres

Cuando perdiste datos por error humano, corrupción o un experimento que
salió mal.

### Restore in-place (sobre la DB actual)

⚠️ **Destruye los datos actuales.** Para uso solo cuando estás seguro.

```bash
make down                                                    # 1. apagar app
make up                                                      # 2. solo postgres
sleep 5                                                      # esperar healthcheck
make restore FILE=backups/wpp_20260520_030000.sql.gz         # 3. restaurar
make logs                                                    # 4. confirmar
```

### Restore en volumen aparte (sin afectar producción)

```bash
docker volume create restore_test
docker run --rm -d --name pg-test \
  -v restore_test:/var/lib/postgresql/data \
  -e POSTGRES_PASSWORD=test -p 127.0.0.1:55432:5432 \
  postgres:16.4-alpine
sleep 5
gunzip -c backups/wpp_xxx.sql.gz \
  | psql -h 127.0.0.1 -p 55432 -U postgres
# inspeccionar:
psql -h 127.0.0.1 -p 55432 -U postgres -d postgres -c "\dt"
psql -h 127.0.0.1 -p 55432 -U postgres -d postgres \
  -c "select count(*) from messages;"
# limpieza:
docker rm -f pg-test
docker volume rm restore_test
```

---

## Checklist de emergencia ("se rompió todo")

1. [ ] **Twilio sigue recibiendo mensajes?** Lo notás si tu WhatsApp
       devuelve "delivered" → el sandbox los acepta. Si Twilio falla,
       buscar en Twilio Status.
2. [ ] **PC arriba?** `ssh`, `make ps`, `make logs`.
3. [ ] **Túnel arriba?** `make tunnel-logs` → "Registered tunnel connection".
4. [ ] **Postgres responde?** `make psql -c "select 1;"`.
5. [ ] **`/health` responde?** `curl https://$PUBLIC_WEBHOOK_HOST/health`.
6. [ ] **Tests pasan en el commit actual?** `make test`.

Si alguno de 2–5 falla y no es trivial:

```bash
# Switch a Railway en 30 segundos
# (en Twilio Console, cambiar webhook URL al endpoint Railway)
```

Si los datos están corruptos:

```bash
make restore FILE=backups/<último válido>.sql.gz
```

Si no podés diagnosticar y necesitás tiempo:

```bash
make down       # apaga la stack pero mantiene volúmenes
# Investigar tranquilo; volver con make up cuando estés listo.
```
