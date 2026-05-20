# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/);
versionado [SemVer](https://semver.org/lang/es/).

## [v0.1.0] - 2026-05-20

Primera versión estable. Personal Orchestrator HQ funcional con
WhatsApp + chat web + Postgres + Cloudflare Tunnel + workers async.
**206 tests passing.**

### Fase A — MVP estable
- Webhook FastAPI con validación de firma Twilio (`X-Twilio-Signature`).
- Idempotencia por `MessageSid` (Twilio reintenta sin duplicar).
- Inbox de Notion con cierre garantizado (try/finally), incluso ante
  excepción del agente.
- Tope de tamaño del body (`MAX_BODY_BYTES`, default 4 KB).
- Tests pytest: tools por dominio, idempotencia, autorización, firma.

### Fase B — Router de costo
- `router.py`: reglas regex deterministas (add_expense, query_tasks)
  → 0 tokens.
- Clasificador Haiku con JSON estructurado: `{intent, complexity,
  confidence, destructive}`. Política simple+confiado → Haiku; complex/
  destructive → Sonnet.
- `cost_log.py`: JSONL append-only con summary 7d. Comando WhatsApp
  `/cost`. Ahorro estimado 50-70% vs todo-Sonnet.

### Fase C — Persistencia Postgres
- `models.py`: 6 tablas (`sessions`, `messages`, `agent_runs`,
  `tool_calls`, `cost_logs`, `pending_confirmations`).
- `db.py` engine async (asyncpg) + `session_scope()`.
- `repos.py`: `SessionRepo` con backend `file`/`postgres` switch.
- Alembic con migraciones portables (SQLite tests + Postgres prod).
- `/cost` lee de SQL si hay Postgres con fallback a JSONL.
- `/health/internal` con `ping()` async.
- Defaults inteligentes: `/data` si existe, `/tmp` si no.

### Fase D — Docker Compose
- Dockerfile multi-stage, non-root (uid 1000), tini PID 1.
- `docker-compose.yml`: postgres + web con volúmenes nombrados,
  healthchecks, log rotation, restart unless-stopped.
- `Makefile`: up/down/logs/migrate/shell/psql/test/smoke/clean/backup/
  restore.
- `scripts/migrate.sh` espera 30 s a Postgres antes de `alembic upgrade
  head`. No-op con backend file.

### Hardening pre-E
- Puerto web en loopback `127.0.0.1:8000`.
- `/health` público minimal `{"ok": true}`; detalle en
  `/health/internal` con `X-Admin-Token`.
- `/diag` protegido por mismo token.
- `scripts/backup.sh` con `pg_dump` comprimido + rotación.

### Fase E — Cloudflare Tunnel
- Servicio `cloudflared` opt-in (`profiles: [tunnel]`).
- Apunta a `web:8000` por red interna del compose (Postgres jamás
  expuesto).
- `ENABLE_DOCS=false` por default (desactiva `/docs`, `/redoc`,
  `/openapi.json`).
- Make targets: `tunnel-up`, `tunnel-down`, `tunnel-logs`,
  `tunnel-status`.

### Fase F — Orchestrator + ActionPlan
- `orchestrator.py` con `ActionPlan` declarativo (intent, route,
  model, payload, needs_confirmation, safety_level).
- Política de safety: `safe` ejecuta directo; `bulk`/`destructive`
  piden confirmación via `pending_confirmations`; `unsafe` bloquea.
- Confirmaciones por WhatsApp con `1`/`cancelar` y TTL configurable.

### Fase G — Agentes especializados
- 5 agentes: `capture_agent` (Haiku, 11 tools), `planner_agent`
  (Sonnet, 7 tools), `writer_agent` (Sonnet, 1 tool), `research_agent`
  (Haiku stub, 5 tools), `critic_agent` (Haiku, 0 tools).
- Defensa en profundidad: whitelist filtrada al modelo + validación
  en ejecución.
- `critic.review_plan()` veta unsafe antes de pending. Default
  conservador: `review` ante JSON mal o excepción.

### Fase H — Workers async
- `async_runner.run_in_background` con `BackgroundTasks` de FastAPI.
- `agent_runs.async_state`: `async_pending` → `async_running` →
  `async_done`/`async_error`.
- `twilio_outbound.send()` para respuesta posterior por WhatsApp;
  fail-soft si falta config, anti-fuga al MY_WHATSAPP.

### Fase I — Command Center web
- `web/` con FastAPI + Jinja2 + HTMX + Tailwind CDN. Sin build step.
- Layout 3 paneles tipo ChatGPT/Claude.
- 16 rutas `/admin/*` gateadas por `ADMIN_TOKEN` (sin token = 404).
- Chat web reutiliza el orquestador completo (sesiones con prefijo
  `web:`).
- Vistas: sesiones, runs (con plan + tool_calls + links Notion),
  agentes, costos, alertas, config (sin secrets).

### Hardening post-I
- Form POST `/admin/login` en vez de `?token=` por URL.
- `/admin/logout` borra cookie.
- Cookie con `HttpOnly`, `SameSite=lax`, `Secure` según
  `ADMIN_COOKIE_SECURE`.
- `ADMIN_LOGIN_QUERY_ENABLED` permite desactivar el atajo `?token=`.
- Chat web async respeta `ASYNC_ENABLED`; polling cada 3 s al endpoint
  `/runs/{id}/status` para refrescar estado del run.

### Fase J — Polish final
- Pins Docker: `postgres:16.4-alpine`, `cloudflare/cloudflared:2024.10.0`.
- `web/queries.py`: limpieza de código muerto (`session_cost_summary`,
  `tool_usage`).
- `web/templates/base.html`: `@apply` → CSS plano (Tailwind CDN
  no soporta `@apply` en runtime).
- Chat web async + `SESSIONS_BACKEND=file` → cae a sync (sin
  trazabilidad persistente, mejor UX previsible).
- Rotación tipo `RotatingFileHandler` para `COST_LOG_FILE` JSONL
  (default 10 MB × 5 backups = 50 MB).

### Métricas finales
- **206 tests** passing en ~3 s.
- **4 migraciones** Alembic versionadas (0001 → 0004).
- **5 agentes** especializados + 1 critic.
- **3 canales** soportados: WhatsApp inbound, WhatsApp outbound async,
  chat web `/admin`.
- **2 backends**: `file` (Railway, dev) o `postgres` (self-hosted).

[v0.1.0]: https://github.com/ricardorama28/personal-notion-hq/releases/tag/v0.1.0
