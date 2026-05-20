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

## Hardening pre-Fase E

Antes de exponer el webhook publico (Cloudflare Tunnel), verificar:

| Item | Como | Estado |
|---|---|---|
| Puerto web solo en loopback | `ss -tlnp \| grep 8000` → `127.0.0.1:8000` | hecho en `docker-compose.yml` |
| Postgres NO expone puertos | `ss -tlnp \| grep 5432` → nada | hecho |
| `TWILIO_VALIDATE=true` en `.env` | `grep TWILIO_VALIDATE .env` | manual (te avisa con WARN en logs si esta en false) |
| `TWILIO_AUTH_TOKEN` seteado | `make shell -c 'env \| grep TWILIO_AUTH'` | manual |
| `ADMIN_TOKEN` seteado y unico | generar: `python -c "import secrets; print(secrets.token_urlsafe(32))"` | manual |
| `/health` publico minimo | `curl http://localhost:8000/health` → `{"ok": true}` | hecho |
| `/health/internal` requiere token | `curl -H "X-Admin-Token: $TOKEN" .../health/internal` | hecho |
| `/diag` requiere token | idem | hecho |
| Logs con rotacion | `docker inspect ... \| grep max-size` | hecho (`10m × 5`) |
| Backup periodico | `make backup`, restore probado al menos una vez | manual (cron del host) |

### `/health` y `/health/internal`

- `GET /health` → `{"ok": true}`. Publico, sin info sensible. Apto para el
  healthcheck de Cloudflare Tunnel y para cualquier monitor externo.
- `GET /health/internal` con header `X-Admin-Token: <ADMIN_TOKEN>` →
  detalle (`sessions_backend`, `database_ok`, qué tokens estan seteados, etc).
  Sin token o con token incorrecto, devuelve **404** (no 401) para no
  revelar la existencia del endpoint a scanners.
- `GET /diag` → idem `/health/internal`, hace probes de lectura contra
  Notion.

El healthcheck del Dockerfile (`scripts/healthcheck.py`) usa el endpoint
publico, y si encuentra `ADMIN_TOKEN` en el entorno, ademas valida
`database_ok` via `/health/internal`. Asi el compose detecta DB caida sin
filtrar nada al mundo.

### Backups de Postgres

```bash
make backup                              # backup a ./backups/wpp_YYYYMMDD_HHMMSS.sql.gz
BACKUP_RETAIN=30 make backup             # cambiar retencion (default 14)
BACKUP_DIR=/mnt/external make backup     # cambiar destino

# Programar diario via cron del host:
0 3 * * * cd /ruta/al/repo && make backup >> /var/log/wpp-backup.log 2>&1
```

Restore:

```bash
make restore FILE=backups/wpp_20260520_030000.sql.gz
# El dump usa --clean --if-exists, asi que sobreescribe lo que haya.
```

Conviene probar el restore al menos una vez en un volumen aparte antes
de confiar en los backups en serio:

```bash
docker volume create wpp_restore_test
# crear un compose alternativo apuntando al volumen restore_test y
# correr `make restore` contra el. Si los datos quedan ok, listo.
```

## Cloudflare Tunnel (Fase E)

Expone el webhook FastAPI corriendo en tu PC al mundo, sin abrir puertos
del router y manteniendo Postgres aislado. Twilio le pega al hostname
publico de Cloudflare; Cloudflare le habla a `web:8000` por la red
interna del compose.

### Por que Cloudflare Tunnel y no un puerto del router

- No abre puertos en tu router (ni en el ISP NAT).
- TLS termina en Cloudflare → no necesitas certificado en la PC.
- Si tu IP cambia (DHCP dinamico), no importa.
- Tu PC iniciar conexion saliente a Cloudflare:7844, no expone nada
  entrante.
- Free tier alcanza para un webhook personal de WhatsApp.

### Setup paso a paso

#### 1. Crear el tunel en Cloudflare

1. Iniciar sesion en https://one.dash.cloudflare.com (Zero Trust).
2. **Networks → Tunnels → Create a tunnel**.
3. Tipo: **Cloudflared**. Nombre: `personal-notion-hq` (o como quieras).
4. Copiar el **token** de la pantalla "Install and run a connector"
   (es un string largo `eyJ...`).
5. En **Public Hostnames**, agregar:
   - **Subdomain**: `webhook` (o el que prefieras).
   - **Domain**: un dominio que tengas en Cloudflare.
   - **Service Type**: `HTTP`.
   - **URL**: `web:8000`  ← el hostname del servicio compose, **no** localhost.

#### 2. Configurar `.env`

```bash
CF_TUNNEL_TOKEN=eyJhIjoi...     # el token del paso 1
PUBLIC_WEBHOOK_HOST=webhook.tudominio.com
TWILIO_VALIDATE=true            # obligatorio antes de exponer
TWILIO_AUTH_TOKEN=...           # obligatorio
ADMIN_TOKEN=...                 # generar con secrets.token_urlsafe(32)
ENABLE_DOCS=false               # default; no exponer Swagger UI
SESSIONS_BACKEND=postgres
POSTGRES_PASSWORD=...           # cambialo!
```

#### 3. Levantar con el profile tunnel

```bash
make tunnel-up                  # postgres + web + cloudflared
# equivalente:
# docker compose --profile tunnel up -d --build
```

`make up` (sin `tunnel-`) sigue funcionando para dev sin tunel: solo
levanta postgres+web.

#### 4. Verificar que el tunel conecta

```bash
make tunnel-logs                # esperar "Registered tunnel connection"
make tunnel-status              # GET https://$PUBLIC_WEBHOOK_HOST/health
# Esperable: {"ok": true}
```

Si `tunnel-status` no responde:
- `make tunnel-logs` — buscar errores de auth (`401 invalid token`),
  resolucion DNS o problemas de hostname.
- Verificar que el hostname en Cloudflare apunta a `web:8000` (no `localhost`).
- Verificar que `web` esta `healthy`: `make ps`.

#### 5. Configurar Twilio

**Sandbox** (mientras pruebas):
1. https://console.twilio.com → Develop → Messaging → Try it out → Send a
   WhatsApp message → Sandbox settings.
2. **When a message comes in**: `https://webhook.tudominio.com/webhook`.
3. **Method**: `POST`.

**Production** (cuando estes listo):
- En la Sender de WhatsApp Business: same URL, same method. NO se
  modifica automaticamente desde aca — es un paso manual deliberado.

### Checklist obligatorio antes de apuntar Twilio

| Check | Como verificar |
|---|---|
| `TWILIO_VALIDATE=true` en `.env` | `grep TWILIO_VALIDATE .env` |
| `TWILIO_AUTH_TOKEN` seteado | logs no muestran warning "TWILIO_AUTH_TOKEN esta vacio" |
| `MY_WHATSAPP` correcto | `grep MY_WHATSAPP .env` |
| `ADMIN_TOKEN` seteado | `make shell` → `echo $ADMIN_TOKEN` |
| `/health` publico devuelve `{"ok": true}` solo | `curl https://$PUBLIC_WEBHOOK_HOST/health` |
| `/health/internal` requiere token | `curl https://$PUBLIC_WEBHOOK_HOST/health/internal` → 404 |
| `/diag` requiere token | `curl https://$PUBLIC_WEBHOOK_HOST/diag` → 404 |
| `/docs` deshabilitado | `curl https://$PUBLIC_WEBHOOK_HOST/docs` → 404 |
| Postgres no expone puerto | `ss -tlnp \| grep 5432` → vacio |
| Backup probado | `make backup` corrio al menos 1 vez; restore probado en volumen test |
| Logs con rotacion | `docker inspect personal-notion-hq-web-1 \| grep max-size` |

### Smoke checks end-to-end

```bash
# 1) Health local
curl -s http://localhost:8000/health
# {"ok": true}

# 2) Health publico (por dominio Cloudflare)
make tunnel-status

# 3) Health interno publico con token
curl -sH "X-Admin-Token: $ADMIN_TOKEN" \
  https://$PUBLIC_WEBHOOK_HOST/health/internal | python -m json.tool

# 4) Webhook simulado (con TWILIO_VALIDATE=false, solo dev)
WEBHOOK_URL=http://localhost:8000/webhook bash scripts/simulate_webhook.sh "gasto 100 cafe"

# 5) Mensaje real desde WhatsApp
#    Mandate al sandbox un "gasto 450 super" y verifica:
make logs                                           # ves el POST de Twilio
make psql -c "select sid, body from messages order by received_at desc limit 5;"
# La fila tiene sid (Twilio MessageSid), body, direction.

# 6) Idempotencia: si Twilio reintenta el mismo MessageSid
#    (no se puede simular real-real sin replay), igualmente:
make psql -c "select sid, count(*) from messages group by sid having count(*) > 1;"
# Esperable: cero filas. Idempotencia OK.

# 7) Logs del tunel
make tunnel-logs
# esperar: "Registered tunnel connection", "Connection registered"
```

### Operaciones del tunel

```bash
make tunnel-up         # arrancar todo + tunel
make tunnel-down       # bajar SOLO el tunel (postgres+web siguen corriendo)
make tunnel-logs       # tail de cloudflared
make tunnel-status     # curl al hostname publico

# Rotar token: generar nuevo en Cloudflare, actualizar .env, restart:
docker compose restart cloudflared
```

### Volver a Railway si el tunel falla

El bot sigue corriendo en Railway en paralelo. Para failover:

1. **Twilio**: cambiar la URL del webhook al endpoint Railway (anotalo
   en algun lado para tenerlo a mano):
   `https://<tu-app>.up.railway.app/webhook`
2. **Railway** debe tener en sus variables:
   ```
   NOTION_TOKEN, ANTHROPIC_API_KEY, TWILIO_AUTH_TOKEN, MY_WHATSAPP
   TWILIO_VALIDATE=true
   SESSIONS_BACKEND=file              # el simple, sin Postgres
   # IDs de databases de Notion (los mismos)
   ```
3. Diferencias entre Railway (file) y PC (postgres):
   - Sesiones en `/tmp` se pierden con cada redeploy de Railway. No es
     critico: el primer mensaje despues reconstruye contexto.
   - No hay `messages`, `agent_runs`, `cost_logs` en Railway (solo
     existen en Postgres de la PC). Mientras esta Railway atendiendo,
     hay un hueco en esas tablas. Cuando vuelvas a la PC, los proximos
     mensajes se siguen registrando; los del intervalo Railway quedan
     en logs/Notion solamente.
   - `/cost` en Railway lee del JSONL (`/tmp/wpp_cost_log.jsonl`, tambien
     volatil). Pierde el detalle, no es bloqueante.
4. Cuando el tunel/PC vuelve, repetir paso 1 apuntando a
   `https://$PUBLIC_WEBHOOK_HOST/webhook`.

### Por que el tunel no expone Postgres ni `/data`

Cloudflare Tunnel solo enruta el hostname publico hacia el `service`
que pusiste en el dashboard (`web:8000`). Postgres, `/health/internal`,
`/diag` y el volumen `/data` no son alcanzables desde el tunel. Si
alguien escanea el dominio publico solo encuentra `/webhook` y `/health`.

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
- **Fase D**: Docker Compose local/self-hosted (`web` + `postgres`,
  opcional `cloudflared` activable con profile).
- **Fase E** (actual): Webhook publico desde PC propia via Cloudflare
  Tunnel. Railway queda como fallback documentado.
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
