# Instalación limpia (v0.1.0)

Esta guía asume PC propia (Linux/macOS) o servidor self-hosted con Docker.
Para correr en Railway sin Docker, ver el README principal.

## 0. Pre-requisitos

- [ ] Docker Engine 24+ y `docker compose` v2 (`docker compose version`).
- [ ] `make` (opcional pero recomendado).
- [ ] Cuenta de Twilio con número WhatsApp sandbox (o sender productivo).
- [ ] Cuenta Anthropic con API key.
- [ ] Cuenta Notion con integración creada y 9 databases compartidas con la integración.
- [ ] Dominio en Cloudflare (para Fase E / túnel público).

## 1. Clonar y configurar

```bash
git clone https://github.com/ricardorama28/personal-notion-hq.git
cd personal-notion-hq
git checkout v0.1.0
cp .env.example .env
```

## 2. Completar `.env`

Variables **obligatorias**:

```bash
# Notion (los 9 DB IDs vienen pre-poblados en .env.example; cambialos
# por los tuyos si moviste de workspace)
NOTION_TOKEN=ntn_...

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Twilio (firma del webhook + outbound async)
TWILIO_AUTH_TOKEN=...
TWILIO_ACCOUNT_SID=AC...
TWILIO_FROM_WHATSAPP=whatsapp:+14155238886
MY_WHATSAPP=whatsapp:+54XXXXXXXXXX
TWILIO_VALIDATE=true

# Admin (UI + endpoints internos)
ADMIN_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
ADMIN_COOKIE_SECURE=true            # true si vas a usar Cloudflare Tunnel
ADMIN_LOGIN_QUERY_ENABLED=false     # recomendado: solo form POST

# Persistencia
SESSIONS_BACKEND=postgres
POSTGRES_PASSWORD=<algo_largo_unico>

# Async (opcional, recomendado si vas a usar planner/writer/research)
ASYNC_ENABLED=true

# Cloudflare Tunnel (opcional pero recomendado para webhook público)
CF_TUNNEL_TOKEN=eyJ...
PUBLIC_WEBHOOK_HOST=webhook.tudominio.com
```

## 3. Crear el túnel en Cloudflare (opcional, recomendado)

- [ ] https://one.dash.cloudflare.com → **Networks → Tunnels → Create**.
- [ ] Tipo: **Cloudflared**, nombre `personal-notion-hq`.
- [ ] Copiar el **token** → pegarlo en `CF_TUNNEL_TOKEN` del `.env`.
- [ ] **Public Hostnames** → agregar:
  - Subdomain: `webhook`, Domain: `tudominio.com`.
  - Service type: `HTTP`, URL: `web:8000` (hostname interno del compose).

## 4. Levantar la stack

```bash
make tunnel-up          # postgres + web + cloudflared
# o sin túnel:
# make up
```

## 5. Verificar arranque

```bash
make logs               # debería verse "Registered tunnel connection"
curl http://localhost:8000/health
# {"ok": true}

curl -H "X-Admin-Token: $ADMIN_TOKEN" \
  http://localhost:8000/health/internal | python -m json.tool
# database_ok: true, sessions_backend: postgres, ...

curl http://localhost:8000/docs       # debe ser 404 (ENABLE_DOCS=false)
ss -tlnp | grep 5432                  # vacío (postgres no expuesto)
ss -tlnp | grep 8000                  # solo 127.0.0.1:8000
```

Con túnel:

```bash
make tunnel-status                    # GET https://$PUBLIC_WEBHOOK_HOST/health
curl -s https://$PUBLIC_WEBHOOK_HOST/health
```

## 6. Configurar Twilio

- [ ] **Twilio Console** → Develop → Messaging → Try it out → WhatsApp →
      Sandbox settings.
- [ ] **When a message comes in**: `https://$PUBLIC_WEBHOOK_HOST/webhook`
      (o la URL de Railway si seguís en fallback).
- [ ] **Method**: `POST`.
- [ ] Guardar.

## 7. Smoke real

Mandar desde tu WhatsApp al sandbox:

```
gasto 100 prueba
```

Verificar:

```bash
make psql -c "select sid, route, intent from agent_runs order by started_at desc limit 5;"
make psql -c "select count(*) from messages;"  # debería ser 2 (inbound + outbound)
```

## 8. Abrir Command Center web

- [ ] Browser: `https://$PUBLIC_WEBHOOK_HOST/admin/login` (o
      `http://localhost:8000/admin/login` en local).
- [ ] Pegar `ADMIN_TOKEN` en el form, **Entrar**.
- [ ] Verificar que `/admin/` cargue el dashboard con la sesión de
      WhatsApp y la última corrida.

## 9. Primer backup

```bash
make backup
ls -lah backups/                       # confirmar archivo > 0 bytes
```

Programar diario en cron del host:

```bash
0 3 * * * cd /ruta/al/repo && make backup >> /var/log/wpp-backup.log 2>&1
```

## 10. Monitor externo (opcional pero recomendado)

- [ ] Configurar UptimeRobot o healthchecks.io.
- [ ] URL: `https://$PUBLIC_WEBHOOK_HOST/health`.
- [ ] Frecuencia: cada 5 min.
- [ ] Alerta a tu mail si baja.

## Done

Mandate `gasto 50 cafe` desde WhatsApp y revisalo en
`/admin/c/whatsapp:+54XXXXXXXXXX`. Si todo aparece, listo para uso diario.
