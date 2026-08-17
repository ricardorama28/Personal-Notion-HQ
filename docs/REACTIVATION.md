# Reactivación

Guía para volver a poner el bot en funcionamiento después de un período
parado. Si es una instalación desde cero, andá a `docs/INSTALL.md`.

## Lo primero: `join` no significa que el bot funcione

Cuando el sandbox de Twilio te contesta *"You're all set"* después de mandar
`join <tus-dos-palabras>`, eso confirma **una sola cosa**: que tu número quedó
vinculado al sandbox. Es un intercambio que ocurre enteramente adentro de
Twilio.

Lo que pasa con el mensaje **siguiente** es otra historia. Twilio lo reenvía
por HTTP POST a la URL que vos configuraste en *Sandbox settings*. Si esa URL
no responde, no hay respuesta ni registro en ninguna parte, y desde WhatsApp
se ve idéntico a que el bot estuviera roto.

La cadena completa:

```
WhatsApp → Twilio Sandbox → URL pública → túnel → FastAPI /webhook → Postgres + Notion
```

Después de un tiempo sin usar el proyecto, el eslabón que se cae casi siempre
es el del medio: el hosting desapareció, el túnel no está levantado, o la URL
en Twilio quedó apuntando a un deploy que ya no existe.

## Diagnóstico en un comando

```bash
make reactivate
```

Recorre la cadena de punta a punta y **corta en el primer eslabón roto**,
diciendo qué ejecutar. Corrélo antes de tocar nada.

## Levantar todo en local

El camino sin costo: la app en tu máquina, expuesta con un túnel efímero de
Cloudflare. No necesita dominio, tarjeta ni cuenta de Cloudflare. Sirve para
validar que todo anda antes de decidir dónde hostear en serio.

### 1. Docker

```bash
# macOS
brew install --cask docker
open -a Docker          # abrilo una vez y esperá a que arranque el daemon

docker compose version  # debe imprimir v2.x
```

Ojo con la sintaxis: los targets llevan guión. Es `make tunnel-up`, no
`make tunnel up` — esto último hace que `make` busque dos targets separados y
falle con `No rule to make target 'tunnel'`.

### 2. Revisar el `.env`

```bash
ls -la .env || cp .env.example .env
```

Estas tienen que estar completas:

| Variable | Por qué importa |
|---|---|
| `NOTION_TOKEN` | Sin esto no escribe nada en Notion |
| `ANTHROPIC_API_KEY` | Sin esto no entiende los mensajes |
| `TWILIO_AUTH_TOKEN` | Valida la firma de los webhooks |
| `MY_WHATSAPP` | Formato exacto `whatsapp:+549XXXXXXXXX` |
| `POSTGRES_PASSWORD` | El contenedor de Postgres no arranca sin esto |
| `SESSIONS_BACKEND` | **Tiene que decir `postgres`** — ver abajo |

Sobre `SESSIONS_BACKEND`: con el valor por defecto `file`, el bot contesta
normal pero **no persiste nada**. `MessageRepo.add()` (`repos.py:97`) hace
`return None` cuando el backend no es Postgres, así que la tabla `messages`
queda vacía para siempre. Si tu síntoma es "contesta pero no registra", esto es
la causa.

### 3. Levantar la stack

```bash
make up          # postgres + web
make reactivate  # verificar los 8 eslabones locales
```

`make reactivate` no debería llegar al final todavía: el último check te va a
avisar que no hay URL pública. Eso es lo que sigue.

### 4. Abrir el túnel

```bash
make quick-tunnel
```

Levanta `cloudflared` en modo quick tunnel, espera a que Cloudflare asigne la
URL, verifica que `/health` responda a través de ella, y te la imprime lista
para copiar.

### 5. Reapuntar Twilio

Consola de Twilio → **Messaging → Try it out → WhatsApp → Sandbox settings**:

- **When a message comes in**: la URL del paso anterior, con `/webhook` al final
- **Method**: `POST`
- **Save**

La URL tiene que coincidir **exacta**. Con `TWILIO_VALIDATE=true`, Twilio firma
el request usando la URL que vos configuraste, y la app reconstruye la suya
desde los headers del proxy (`_public_url()`, `main.py:78`) para comparar
firmas. Cualquier diferencia — `http` en vez de `https`, un host viejo, una
barra final de más — hace que la firma no valide y el webhook responda `403`
sin registrar nada.

### 6. Probar

Mandá desde WhatsApp:

```
gasto 100 prueba
```

Verificá:

```bash
make logs                                        # "inbound webhook From=..."
make psql -c "select count(*) from messages;"    # 2 (inbound + outbound)
```

Y abrí el Command Center en `http://localhost:8000/admin/login`.

## Síntoma → causa → arreglo

| Síntoma | Causa probable | Arreglo |
|---|---|---|
| No contesta nada | No hay app corriendo | `make up`, después `make reactivate` |
| No contesta, `/health` local anda | La URL en Twilio apunta a un deploy muerto | Reapuntar *Sandbox settings* |
| No contesta, el túnel anda | `403` por firma: la URL no coincide | Ver la URL exacta en `make logs` (el warning la imprime) y alinearla |
| `⚠️ No autorizado` con un `From=` | `MY_WHATSAPP` no coincide | Copiar ese `From=` tal cual al `.env` y `make rebuild` |
| Contesta pero `messages` vacía | `SESSIONS_BACKEND=file` | Poner `postgres`, `make up && make migrate` |
| Contesta pero nada en Notion | Token revocado o DBs no compartidas | `/diag` lo detalla; recompartir las 10 DBs con la integración |
| `/diag`: `AttributeError: 'DatabasesEndpoint' object has no attribute 'query'` | `notion-client` >= 2.6 | Ver abajo |
| `make: No rule to make target 'tunnel'` | Falta el guión | `make tunnel-up` |

Para el caso del `403`: desde esta versión el log incluye la URL que la app
usó para validar. Comparala carácter por carácter con la de la consola de
Twilio.

## Trampa: las dependencias se mueven aunque tu código no

El repo no tiene lockfile, así que `make rebuild` resuelve las versiones el día
que lo corrés. Un contenedor construido hace meses y uno construido hoy no
tienen las mismas librerías, aunque el commit sea idéntico. Es un modo de falla
típico al reactivar: el código no cambió, pero la dependencia debajo sí.

Ya pasó una vez con Notion. `notion-client` 2.6.0 eliminó `databases.query()`
al adoptar la API de data sources (versión `2025-09-03` de Notion), y
`notion_ops.py` la usa en 6 lugares. Con esa versión, las 9 databases fallan en
`/diag` con:

```
AttributeError: 'DatabasesEndpoint' object has no attribute 'query'
```

Por eso `requirements.txt` ahora tiene techo en `<2.6`. **No lo subas sin
migrar antes** los 6 llamados de `notion.databases.query()` a
`DataSourcesEndpoint`; ese trabajo queda pendiente.

Si aparece un `AttributeError` parecido con otra librería, el patrón es el
mismo: mirá qué versión se instaló (`make shell` → `pip show <lib>`), compará
con lo que el código espera, y poné techo en `requirements.txt`.

## Límites del túnel efímero

- **La URL cambia en cada arranque** del contenedor. Hay que repetir el paso 5
  en Twilio cada vez. Es lo que lo hace inviable para uso diario.
- **El bot solo anda con la máquina prendida** y sin suspender.
- El sandbox de Twilio caduca por inactividad: si pasa mucho tiempo, hay que
  volver a mandar `join <tus-dos-palabras>`. Eso **no** borra la URL del
  webhook, que queda como la dejaste.

## Cuando decidas hosting definitivo

Dos caminos, según qué te importe más.

**Túnel nombrado de Cloudflare** — gratis, hostname estable, requiere un
dominio propio. Creás el túnel en Cloudflare Zero Trust, ponés `CF_TUNNEL_TOKEN`
y `PUBLIC_WEBHOOK_HOST` en el `.env`, y usás `make tunnel-up` en vez de
`make quick-tunnel`. Pasos completos en `docs/INSTALL.md`, sección 3. Seguís
atado a tener la máquina prendida.

**PaaS (Railway u otro)** — la máquina no tiene que estar prendida, pero se
paga. En Railway hay que tener presente que el mínimo de $5/mes cubre $5 de uso,
y esta app necesita **dos** servicios 24/7 (web + Postgres), así que el consumo
real suele quedar por encima de eso. Si el trial expiró, los deployments quedan
pausados y el dominio devuelve `404 Application not found` con el header
`x-railway-fallback: true` — que es exactamente el síntoma de "no registra
nada". Al despausar, verificá que el dominio asignado siga siendo el mismo antes
de dar por hecho que Twilio sigue apuntando bien.

En cualquiera de los dos casos, después de mover el hosting **siempre** hay que
reapuntar *Sandbox settings* y correr `make reactivate`.
