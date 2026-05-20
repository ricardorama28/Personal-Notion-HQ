# Notion WhatsApp Bot

Bot personal de WhatsApp que gestiona tu Notion (PERSONAL HQ) usando Claude con tool use: tareas, eventos, notas/apuntes, diagramas, gastos, comidas y hábitos.

## Estructura en Notion (10 DB bajo PERSONAL HQ)

`Projects`, `Tasks`, `Events`, `Areas`, `Inbox/WhatsApp Log`, `Notes`,
`Expenses`, `Meals`, `Habits`, `Habit Log` + dashboards.

Cada mensaje de WhatsApp crea una fila en **Inbox/WhatsApp Log** y los
registros que se generen (tarea, nota, gasto, comida, log de hábito) quedan
vinculados a esa fila (relación `Inbox`, dual). Al terminar, la fila se cierra
con `Processing Status` (Auto-processed / Needs review), `Detected Type` y
`Action Taken`. Si llega el mismo `MessageSid` (retry de Twilio) no se duplica.

## Stack

- **FastAPI** webhook receiver
- **Twilio WhatsApp Sandbox** (gratis, para empezar)
- **Anthropic API** con tool use clasico (no MCP)
- **notion-client** directo contra Notion API

## Setup

### 1. Variables de entorno

```bash
cp .env.example .env
# editar .env con tus valores reales
```

`.env.example` ya trae prellenados los 9 database IDs (Projects, Tasks, Events,
Notes, Expenses, Meals, Habits, Habit Log, Inbox). Solo completá `NOTION_TOKEN`,
`ANTHROPIC_API_KEY` y `MY_WHATSAPP`.

La integración de Notion tiene que seguir compartida en la página **PERSONAL
HQ**: las DB nuevas heredan el acceso. Verificá permisos con `GET /diag`.

**IMPORTANTE:** rotá el `NOTION_TOKEN` si alguna vez quedó expuesto en chat o en un commit.

### 2. Instalar dependencias

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Correr local

```bash
uvicorn main:app --reload --port 8000
```

### 4. Exponer el webhook

Para probar local con Twilio necesitás un tunel:

```bash
# en otra terminal
ngrok http 8000
```

Copiá la URL HTTPS de ngrok (algo como `https://abc123.ngrok.io`).

### 5. Configurar Twilio Sandbox

1. Ir a [Twilio Console > Messaging > Try it out > WhatsApp Sandbox](https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn)
2. Unirte al sandbox mandando el codigo (algo tipo "join xxx-yyy") al numero que te muestran, desde tu WhatsApp
3. En la config del sandbox, en "When a message comes in" pegar: `https://abc123.ngrok.io/webhook`
4. Method: HTTP POST

### 6. Probar

Mandate un mensaje al numero de sandbox:

> agregá tarea estudiar algoritmos para el viernes prioridad alta

Deberia responderte algo como `✓ tarea creada para el 16/05`.

## Comandos utiles

- `nuevo` o `/reset` → limpia el historial de la sesion
- Cualquier otra cosa → procesado por Claude

## Deploy a Railway

```bash
# requiere railway CLI
railway login
railway init
railway up
```

El repo incluye un `Procfile` (`web: uvicorn main:app --host 0.0.0.0 --port $PORT`)
para que Railway levante el server bindeado al puerto correcto. Sin esto la app
no es alcanzable y Twilio responde con su eco por defecto.

Pasos:

1. Setear **todas** las env vars en el dashboard de Railway (el `.env` local NO
   se deploya): `NOTION_TOKEN`, `ANTHROPIC_API_KEY`, `MY_WHATSAPP` y los 9
   IDs de DB (`TASKS_DB_ID`, `EVENTS_DB_ID`, `PROJECTS_DB_ID`, `NOTES_DB_ID`,
   `EXPENSES_DB_ID`, `MEALS_DB_ID`, `HABITS_DB_ID`, `HABITLOG_DB_ID`,
   `INBOX_DB_ID`). **Tras agregar los IDs nuevos hay que redeployar.**
2. En el Sandbox de Twilio, "When a message comes in":
   `https://<tu-url-railway>/webhook`, método **HTTP POST**.

## Debug

Si Twilio responde *"You said: ... Configure your WhatsApp Sandbox's Inbound URL
to change this message"*, **Twilio no está llegando a la app**. Checklist:

1. Abrí `https://<tu-url-railway>/health` en el navegador. Tiene que devolver
   `{"ok": true, "my_whatsapp_set": true, "anthropic_key_set": true,
   "notion_token_set": true}`. Si algún booleano es `false`, falta esa env var
   en Railway.
2. Verificá que el webhook de Twilio sea exactamente `.../webhook` con **POST**.
3. Mandá un WhatsApp y mirá los logs de Railway: debe aparecer
   `inbound webhook From=... Body=...`. Si no aparece, Twilio no está llegando
   (revisá URL/deploy).
4. Si te responde `⚠️ No autorizado. Recibí From=...`: copiá ese valor exacto
   a la env var `MY_WHATSAPP` en Railway y redeployá.

## Validacion de firma Twilio

A partir de la Fase A reforzada, `/webhook` verifica el header
`X-Twilio-Signature` con `TWILIO_AUTH_TOKEN`. Si la firma no es valida,
el endpoint responde **403**.

- En produccion (Railway / PC propia con tunel): `TWILIO_VALIDATE=true` y
  `TWILIO_AUTH_TOKEN=<token>` (Twilio Console > Account > Auth Token).
- En local con `curl` o `scripts/simulate_webhook.sh`: `TWILIO_VALIDATE=false`.

La URL absoluta que se usa para validar respeta `X-Forwarded-Proto/Host`,
asi que funciona detras de Railway o Cloudflare Tunnel sin configuracion
extra.

## Router de costo (Fase B)

`router.py` decide como procesar cada mensaje:

1. **Reglas regex** (0 tokens):
   - `gasto/gasté <monto> <texto>` → `add_expense` directo, infiere
     categoria y metodo cuando hay keywords (super, débito, etc.).
   - `qué tengo hoy/mañana/esta semana/proxima semana` → `query_tasks`
     directo con rango de fechas.
2. **Clasificador Haiku** devuelve `{intent, complexity, confidence,
   destructive}`:
   - `complexity=low` + `confidence ≥ ROUTER_CONFIDENCE_THRESHOLD` →
     loop de tool use con **Haiku** (barato).
   - `intent ∈ {plan, write, research}`, `complexity=high`, `destructive`
     o `prompt_injection` → loop con **Sonnet**.
   - Bad JSON, error o baja confianza → fallback a Sonnet.
3. `ROUTER_ENABLED=false` salta el router y usa siempre `ORCHESTRATOR_MODEL`.

Comando WhatsApp `/cost` muestra los ultimos 7 dias (USD, tokens,
distribucion por ruta) leyendo `COST_LOG_FILE` (JSONL).

## Persistencia: file vs postgres (Fase C)

El bot soporta dos backends de sesion, elegidos con `SESSIONS_BACKEND`:

| Backend | Donde vive el historial | Otros datos (messages, agent_runs, tool_calls, cost_logs, pending_confirmations) |
|---|---|---|
| `file` (default) | `/tmp/wpp_sessions.json` | no se persisten en SQL |
| `postgres` | tabla `sessions` | tambien se persisten todas las tablas |

Si falta `DATABASE_URL` y `SESSIONS_BACKEND=postgres`, la app falla al
arrancar con un mensaje claro. Mientras se mantenga `SESSIONS_BACKEND=file`,
el bot funciona exactamente como antes y no necesita Postgres.

### Configurar Postgres local

```bash
# 1. Instalar Postgres (Debian/Ubuntu) y crear DB
sudo apt install postgresql
sudo -u postgres createdb wpp
sudo -u postgres createuser wpp -P  # poné una clave

# 2. Variables en .env
SESSIONS_BACKEND=postgres
DATABASE_URL=postgresql+asyncpg://wpp:<password>@localhost:5432/wpp

# 3. Correr migraciones
alembic upgrade head
```

### Migraciones Alembic

```bash
# aplicar todas las migraciones pendientes
alembic upgrade head

# ver estado
alembic current
alembic history

# generar nueva (cuando se cambien los modelos)
alembic revision -m "descripcion del cambio" --autogenerate
```

#### Migraciones en entorno dockerizado

`scripts/migrate.sh` esta pensado para ser invocado como paso previo al
arranque del servidor. Hace:

1. Si `SESSIONS_BACKEND != postgres` → exit 0 (no requiere DB).
2. Si falta `DATABASE_URL` con backend postgres → exit 1 con mensaje.
3. Espera hasta 30s a que Postgres responda `SELECT 1` (`asyncpg`),
   util cuando el contenedor de la app arranca antes que el de la DB.
4. Corre `alembic upgrade head`.

En el `Dockerfile` (Fase D) el `CMD` sera algo como:

```
CMD ["sh", "-c", "scripts/migrate.sh && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

Para entornos con multiples replicas, conviene moverlo a un init
container/job aparte; Alembic toma un lock pero es mas limpio aislarlo.

### Persistencia de archivos (sessions JSON, cost log JSONL)

Los defaults son **inteligentes**:

- Si `SESSIONS_FILE` / `COST_LOG_FILE` estan en el entorno, se usan.
- Si no, se prefiere `/data/` si existe y es escribible.
- Si `/data/` no existe (Railway, dev local sin volumen), cae a `/tmp/`.

Esto deja el repo listo para Docker Compose (donde se monta un volumen
en `/data`) sin romper Railway ni el modo local.

### Volver al backend file

```bash
SESSIONS_BACKEND=file
# DATABASE_URL puede quedar seteada o vacia, no se usa
```

El historial anterior en `/tmp/wpp_sessions.json` queda intacto. No hay
migracion automatica de un backend al otro (la sesion en curso se va a
recrear con el primer mensaje nuevo).

### Que cambia respecto a `/tmp/wpp_sessions.json`

- `file`: idéntico al MVP. Se pierde con reinicio del contenedor.
- `postgres`: sobrevive a reinicios; ademas guarda un registro completo
  de cada mensaje, agent_run, tool_call y cost_log que Fase F (orquestador)
  y Fase I (panel) van a usar.

## Docker Compose (Fase D)

Para correr en tu PC (o cualquier server) con Postgres incluido,
healthchecks, volumen persistente y migraciones automaticas.

### Requisitos previos

- Docker Engine 24+ y `docker compose` v2 (`docker compose version`).
- `make` (opcional, hace los comandos mas cortos).
- Tokens de Notion, Anthropic y Twilio en el `.env`.

### Crear `.env`

```bash
cp .env.example .env
# editar .env y completar:
#   NOTION_TOKEN, ANTHROPIC_API_KEY, TWILIO_AUTH_TOKEN, MY_WHATSAPP
#   POSTGRES_PASSWORD  (¡cambiala SIEMPRE!)
#   SESSIONS_BACKEND=postgres  (para usar la DB del compose)
```

`.env` esta en `.gitignore` y nunca se commitea. `.env.example` no lleva
secretos: solo placeholders.

### Levantar

```bash
make up              # build + up -d, espera a /health
# equivalente sin make:
# docker compose up -d --build
```

La primera vez:
1. `postgres` arranca y queda `healthy` (`pg_isready`).
2. `web` se construye, `scripts/migrate.sh` espera a Postgres, corre
   `alembic upgrade head` y arranca `uvicorn` en `:8000`.
3. El healthcheck de `web` golpea `/health` y aprueba si `ok=true`.

### Migraciones

`scripts/migrate.sh` se ejecuta automaticamente en cada arranque del
contenedor `web`. Si necesitas correrlas manualmente:

```bash
make migrate
# equivalente:
# docker compose exec web alembic upgrade head
```

Cuando cambies modelos:

```bash
make shell
alembic revision -m "descripcion" --autogenerate
# revisa el archivo generado en alembic/versions/
exit
make migrate
```

### Probar `/health`

```bash
curl -s http://localhost:8000/health | python -m json.tool
```

Esperable cuando todo esta OK:
```json
{
  "ok": true,
  "sessions_backend": "postgres",
  "database_url_set": true,
  "database_ok": true,
  ...
}
```

Si `database_ok=false`, `ok=false` y `make logs` muestra el detalle.

### Simular un webhook

`TWILIO_VALIDATE=false` en el `.env` durante pruebas locales (sino
necesitas firmar el request con el Auth Token):

```bash
make smoke                          # /health + un mensaje de prueba
# o uno particular:
WEBHOOK_URL=http://localhost:8000/webhook \
  bash scripts/simulate_webhook.sh "gasto 100 cafe"
```

### Ver logs

```bash
make logs                           # tail -f de todos los servicios
docker compose logs -f web          # solo web
docker compose logs -f postgres     # solo db
```

### Apagar y reiniciar

```bash
make down              # baja contenedores, MANTIENE volumenes
make up                # los vuelve a levantar
make rebuild           # down + build --no-cache + up
make clean             # ⚠ down + BORRA volumenes (pierde Postgres y /data)
```

### Persistencia de datos

| Que | Donde | Sobrevive a `make down` | Sobrevive a `make clean` |
|---|---|---|---|
| Tablas Postgres | volumen `personal-notion-hq_pgdata` | sí | **no** |
| Sesiones (file backend) / cost JSONL | volumen `personal-notion-hq_appdata` (`/data` en web) | sí | **no** |

Verificar:

```bash
docker volume ls | grep personal-notion-hq
docker volume inspect personal-notion-hq_pgdata
make psql -c "select count(*) from messages;"
make shell -c "ls -la /data"
```

### Acceso a Postgres desde el host (opcional, dev)

Por seguridad el contenedor de Postgres **no expone puertos** al host.
Para conectar con `psql`/DBeaver desde tu maquina:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
make rebuild
psql -h 127.0.0.1 -p 5432 -U wpp -d wpp
```

El override solo abre `127.0.0.1:5432` (no toda la red). Esta gitignored,
nunca lo commitees. Sin override, podes usar `make psql` (psql dentro del
contenedor) sin abrir nada.

### Volver a correr sin Docker

Railway, dev local sin Docker o cualquier otro entorno siguen
funcionando igual:

```bash
make down                                # apagar la stack docker
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
TWILIO_VALIDATE=false uvicorn main:app --reload --port 8000
```

`Procfile` sigue intacto para Railway. `SESSIONS_BACKEND=file` (default)
no necesita Postgres y guarda en `/tmp/wpp_sessions.json` (o `/data/` si
existe).

### Smoke checks de Docker

```bash
make up
make smoke                       # /health + webhook de prueba
make logs                        # verificar que no haya errores
make psql                        # entrar a la DB y consultar tablas
\dt                              # ver las 6 tablas creadas
select count(*) from agent_runs; # ver corridas
```

Si todo eso pasa, el deploy local esta OK.

## Tests

```bash
pip install -r requirements-dev.txt
TWILIO_VALIDATE=false pytest -v
```

Cubren: tools de Notion (task, reminder, note, expense, meal, habit, event,
unknown), webhook (firma, autorizacion, idempotencia, tamano, reset) e
Inbox (cierre con Detected Type, Needs review cuando no hubo writes,
recuperacion ante excepcion).

## Simular webhook localmente

```bash
TWILIO_VALIDATE=false uvicorn main:app --reload --port 8000
bash scripts/simulate_webhook.sh                  # corre toda la bateria
bash scripts/simulate_webhook.sh "gasto 100 cafe" # un caso suelto
```

## Roadmap (Personal Orchestrator HQ)

El MVP actual evoluciona en fases incrementales hacia un orquestador
personal autoalojado. Cada fase entrega valor por si sola.

- **Fase A**: MVP estable. Firma Twilio, idempotencia, Inbox
  endurecido, tests, `config.py` centralizado.
- **Fase B**: Router de costo. Reglas/regex para mensajes obvios,
  Haiku para clasificar, Sonnet solo cuando hace falta. Logging de tokens
  y costo a JSONL (`COST_LOG_FILE`). Comando `/cost` por WhatsApp.
- **Fase C** (actual): Persistencia opcional en Postgres con flag
  `SESSIONS_BACKEND=file|postgres`. Tablas `messages`, `sessions`,
  `agent_runs`, `tool_calls`, `cost_logs`, `pending_confirmations`.
  SQLAlchemy async + Alembic.
- **Fase D** (actual): Docker Compose local/self-hosted (`web` +
  `postgres`, opcionales: `redis`, `worker`, `cloudflared` para Fase E).
- **Fase E**: Webhook publico desde PC propia via Cloudflare Tunnel.
  Railway queda como fallback documentado.
- **Fase F**: Orchestrator central con `ActionPlan` y confirmaciones
  para acciones destructivas.
- **Fase G**: Agentes especializados (Capture / Planner / Writer /
  Research / Critic-Safety).
- **Fase H**: Workers async (`BackgroundTasks` → `rq` si crece).
- **Fase I** (opcional): panel web `/admin` con FastAPI + Jinja2.

## Limites del MVP

- Solo texto (sin audio, sin imagenes)
- Sesion guardada en `/tmp/` (se pierde si Railway reinicia el contenedor)
- Solo respondes a un numero (`MY_WHATSAPP`)
- Si renombras un proyecto en Notion, restart del proceso para limpiar el cache

## Proximos pasos (Fase 2)

- Whisper para transcribir audios
- Vision API para procesar fotos del pizarron → apuntes
- Persistencia de sesiones en Postgres
- Resumen automatico cuando el historial pasa de 30 vueltas
