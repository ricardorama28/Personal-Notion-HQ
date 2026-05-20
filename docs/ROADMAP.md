# Roadmap — más allá de v0.1.0

Mejoras priorizadas por valor/esfuerzo, derivadas de la auditoría post-Fase J.
Nada acá está implementado en v0.1.0. **Orden recomendado de pickup.**

---

## Prioridad alta (próximos sprints)

### 1. Retries automáticos en `async_runner`
**Por qué**: Anthropic y Notion son flaky con 429/5xx. Un blip transitorio
hoy marca `async_error`.
**Esfuerzo**: S.
**Cómo**: wrappear `_execute_plan` en el worker con loop tipo
`tenacity.retry(stop_after_attempt=3, wait=exponential(min=2, max=30))`,
solo para excepciones de red. Registrar el intento `N/3` en
`agent_runs.error`.

### 2. Sanitización de `messages.body` inbound
**Por qué**: hoy se persisten secrets si el usuario los pega.
**Esfuerzo**: S.
**Cómo**: regex de patrones tipo `(token|password|api[_-]?key|secret)=[\w\-\.]+`,
reemplazar por `[redacted]` antes de `repos.messages.add(direction="inbound")`.
Mantener el body original en memoria para el procesamiento, redactar solo
al persistir.

### 3. Healthcheck externo configurado
**Por qué**: si la PC muere a las 3 AM, no te enterás.
**Esfuerzo**: XS (config externa, no código).
**Cómo**: UptimeRobot o healthchecks.io free, apuntando a
`https://$PUBLIC_WEBHOOK_HOST/health`, alerta por mail o Telegram cada
5 min si baja.

### 4. Cron de backups + verificación
**Por qué**: backups sin verificar = ilusión de backups.
**Esfuerzo**: S.
**Cómo**: cron `0 3 * * *` ya documentado. Sumar `scripts/verify_backup.sh`
que cada N días tome el último .sql.gz y haga restore en un volumen
test, contando filas y comparando contra el dump original.

---

## Prioridad media

### 5. Rate limiting básico en `/webhook` y `/admin/*`
**Por qué**: si `TWILIO_AUTH_TOKEN` o `ADMIN_TOKEN` se filtran, abuso
ilimitado.
**Esfuerzo**: M.
**Cómo**: contador en Postgres de últimos 60 s por sender, o
`slowapi` con `@limiter.limit("60/minute")`. Tope soft que devuelve
429 con `Retry-After`.

### 6. Budget cap de Anthropic con kill switch
**Por qué**: hoy un loop o intent caótico puede gastar mucho sin freno
real.
**Esfuerzo**: M.
**Cómo**: query a `cost_logs` al inicio del request; si el costo del
día pasa `DAILY_BUDGET_USD`, devolver una respuesta predefinida y
loggear alert. Recovery: editar env o esperar al día siguiente.

### 7. Invalidación de cache Notion
**Por qué**: `@lru_cache` de proyectos/hábitos solo se reinicia con
restart.
**Esfuerzo**: S.
**Cómo**: TTL de 5 min en el `lru_cache` (usar `cachetools.TTLCache`)
o agregar endpoint `POST /admin/cache/clear` gateado.

### 8. FKs reales `tool_calls.agent_run_id` y `agent_runs.confirmed_from`
**Por qué**: joins en el panel se vuelven robustos.
**Esfuerzo**: S (migración + ajuste de tests).
**Cómo**: migración 0005 que agrega `FOREIGN KEY ... ON DELETE SET NULL`.

### 9. Refactor `run_agent` legacy
**Por qué**: duplica el loop de `Agent.run` con casi el mismo código.
**Esfuerzo**: S.
**Cómo**: que `main.run_agent` instancie un `Agent` con system prompt
del bot principal + `allowed_tools = {todas las del registry}` y delegue.
Los tests pasan sin cambios.

### 10. SSE en chat web (streaming)
**Por qué**: el polling cada 3 s funciona pero gasta DB y se siente
"viejo".
**Esfuerzo**: M.
**Cómo**: endpoint `GET /admin/runs/{id}/stream` con `text/event-stream`,
worker emite eventos al modificar `agent_runs.async_state`. Reemplaza
`hx-trigger='every 3s'` por SSE listener.

---

## Prioridad baja

### 11. Multi-usuario
**Por qué**: hoy solo `MY_WHATSAPP` está permitido. Si querés invitar
a un familiar a tu bot, no se puede.
**Esfuerzo**: L (auth, users table, scoping de Notion DBs por user).
**Cómo**: tabla `users` (whatsapp_id, notion_token, allowed_dbs).
Cambiar checks de `MY_WHATSAPP` por lookup. **Riesgo alto** para uso
personal — requiere repensar muchas asunciones.

### 12. RQ + Redis
**Por qué**: si `BackgroundTasks` pierde tareas (uvicorn crash),
querés persistencia.
**Esfuerzo**: M.
**Cómo**: ya documentado en el README sección Fase H. Service `redis`
en compose, worker separado, `rq.enqueue(run_in_background, ...)`.

### 13. Pruebas E2E con Playwright
**Por qué**: cubrir clicks reales del Command Center.
**Esfuerzo**: M.
**Cómo**: `pytest-playwright` apuntando a un compose levantado.
Skipear en CI sin Docker.

### 14. Logs estructurados a Loki/Grafana
**Por qué**: `grep` se queda corto cuando hay 100+ runs por día.
**Esfuerzo**: M.
**Cómo**: `structlog` o `python-json-logger` → Loki vía driver de
Docker → Grafana dashboards.

### 15. Más reglas regex en el router
**Por qué**: cada regla ahorra dos llamadas Anthropic.
**Esfuerzo**: XS por regla.
**Ideas**: `"hice <hábito>"`, `"agendá <evento> el <fecha>"`,
`"recordame <texto> <fecha>"`, `"foto"` (placeholder).

### 16. Browser tool real para `ResearchAgent`
**Por qué**: hoy es stub.
**Esfuerzo**: M-L según API que se use.
**Cómo**: Tavily, Brave Search API, o web fetch directo + summarization.
Sumar a `tools.py` con whitelist en `ResearchAgent`.

### 17. Email/calendar tools
**Por qué**: completar el "asistente personal".
**Esfuerzo**: L (auth OAuth, tools nuevas).
**Cómo**: Google Calendar y Gmail con scopes mínimos; tools
`schedule_event_in_calendar`, `draft_email`. Solo draft, nunca send.

### 18. Panel de admin con métricas históricas
**Por qué**: gráficos serios > tablas.
**Esfuerzo**: M.
**Cómo**: Chart.js via CDN en `costs.html`, queries agregadas en
`web/queries.py`.

### 19. Migración a `psycopg3` async
**Por qué**: `asyncpg` es excelente pero `psycopg3` tiene mejor
compatibilidad con el ecosistema (más herramientas) y soporta sync+async.
**Esfuerzo**: S.
**Cómo**: cambiar URL a `postgresql+psycopg://` y agregar `psycopg[binary]`
en requirements.

### 20. Auditoría de safety con un LLM externo
**Por qué**: el critic actual es 1 sola llamada Haiku — bueno, pero
limitado.
**Esfuerzo**: M.
**Cómo**: dual critic (uno Haiku rápido, uno Sonnet en casos dudosos),
o pedir verdict + reason + 3-bullet-summary y mostrarlo en la UI.

---

## Lo que NO conviene tocar a menos que duela

- Pipeline `router → orchestrator → agents` (estable, 206 tests).
- Schema de `ActionPlan` (hay payloads serializados en `pending_confirmations`).
- Defaults seguros (`TWILIO_VALIDATE=true`, `ENABLE_DOCS=false`, etc.).
- Backend `file` fallback (Railway depende).
- `Procfile`.
- Postgres no expuesto en compose.
- `/admin/*` gateado por `ADMIN_TOKEN` con 404 (no 401).
- Critic + safety_level policy.

---

## Cómo elegir qué seguir

Sugerencia personal:

- **Si lo vas a usar 30+ días seguidos**: 1, 2, 3, 4 primero.
- **Si lo querés compartir con alguien más**: 1-4 + 5 (rate limit) + 11
  (multi-user).
- **Si te molesta que se sienta básico**: 10 (SSE) + 18 (charts).
- **Si te interesa la profundidad técnica**: 6 (budget), 8 (FKs),
  12 (Redis), 14 (Loki).
