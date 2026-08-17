#!/usr/bin/env bash
# Diagnostico end-to-end para reactivar el bot despues de un periodo parado.
#
# Recorre la cadena eslabon por eslabon y CORTA en el primero que falla,
# diciendo que hacer. La idea es no volver a debuggear a mano cual de los
# seis componentes se cayo.
#
#   WhatsApp → Twilio → URL publica → tunel → FastAPI → Postgres + Notion
#
# Uso:
#   bash scripts/reactivate.sh      # o: make reactivate
#
# Ver docs/REACTIVATION.md para el runbook completo.

set -uo pipefail

cd "$(dirname "$0")/.."

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
step() { printf '\n\033[1m──> %s\033[0m\n' "$1"; }

# Corta el script mostrando la accion concreta a ejecutar.
fail() {
  printf '  \033[31m✗\033[0m %s\n' "$1"
  shift
  if [[ $# -gt 0 ]]; then
    printf '\n\033[1mQue hacer:\033[0m\n'
    for line in "$@"; do printf '  %s\n' "$line"; done
  fi
  printf '\n\033[31mDiagnostico cortado en este punto.\033[0m Arreglá esto y volvé a correr.\n'
  exit 1
}

# Lee una var del .env sin evaluar el archivo (evita ejecutar codigo si
# alguien metio un $(...) en un valor).
envget() {
  [[ -f .env ]] || return 0
  sed -n "s/^${1}=//p" .env | tail -1 | sed 's/^["'\'']//; s/["'\'']$//'
}

printf '\033[1mReactivacion — Personal Notion HQ\033[0m\n'

# ----------------------------------------------------------------------
step '1/8  Docker disponible'
# ----------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail \
  'docker no esta instalado' \
  'macOS:  brew install --cask docker   (y despues abrí Docker Desktop una vez)' \
  'Linux:  https://docs.docker.com/engine/install/'

docker info >/dev/null 2>&1 || fail \
  'docker esta instalado pero el daemon no responde' \
  'Abrí Docker Desktop y esperá a que el icono deje de animarse.'

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
  warn 'usando docker-compose v1 (deprecado); considerá actualizar a Compose v2'
else
  fail 'no hay docker compose v2 ni docker-compose v1' \
       'Docker Desktop trae Compose v2 incluido. Instalalo y reintentá.'
fi
ok "docker + compose disponibles ($(docker --version | cut -d, -f1))"

# ----------------------------------------------------------------------
step '2/8  Archivo .env'
# ----------------------------------------------------------------------
[[ -f .env ]] || fail \
  'no existe .env' \
  'cp .env.example .env' \
  'y completá las credenciales (ver docs/INSTALL.md, seccion 2).'

missing=()
for var in NOTION_TOKEN ANTHROPIC_API_KEY TWILIO_AUTH_TOKEN MY_WHATSAPP POSTGRES_PASSWORD; do
  [[ -n "$(envget "$var")" ]] || missing+=("$var")
done
[[ ${#missing[@]} -eq 0 ]] || fail \
  "variables vacias en .env: ${missing[*]}" \
  'Completalas antes de seguir. Sin ellas la app arranca pero no hace nada util.'
ok 'credenciales obligatorias presentes'

MY_WHATSAPP="$(envget MY_WHATSAPP)"
case "$MY_WHATSAPP" in
  whatsapp:+[0-9]*) ok "MY_WHATSAPP=${MY_WHATSAPP:0:12}…" ;;
  *) fail "MY_WHATSAPP tiene formato raro: '$MY_WHATSAPP'" \
          'Tiene que ser exactamente: whatsapp:+549XXXXXXXXX' \
          'Si no coincide con tu numero real, el bot responde "No autorizado".' ;;
esac

# ----------------------------------------------------------------------
step '3/8  Backend de persistencia'
# ----------------------------------------------------------------------
# Este es el check que explica el sintoma "el bot contesta pero no registra
# nada": con backend=file, repos.MessageRepo.add() hace return None y la
# tabla messages nunca se escribe.
BACKEND="$(envget SESSIONS_BACKEND)"
if [[ "$BACKEND" != "postgres" ]]; then
  fail "SESSIONS_BACKEND='${BACKEND:-<vacio>}' (deberia ser 'postgres')" \
       'Con backend "file" el bot contesta pero NO guarda nada en la DB:' \
       'repos.py:97 corta con `return None` y la tabla messages queda vacia.' \
       '' \
       'Poné en .env:  SESSIONS_BACKEND=postgres' \
       'y despues:     make up && make migrate'
fi
ok 'SESSIONS_BACKEND=postgres'

# ----------------------------------------------------------------------
step '4/8  Contenedores arriba'
# ----------------------------------------------------------------------
running="$("${COMPOSE[@]}" ps --services --filter status=running 2>/dev/null)"
for svc in postgres web; do
  grep -qx "$svc" <<<"$running" || fail \
    "el servicio '$svc' no esta corriendo" \
    'make up          # levanta postgres + web' \
    'make logs        # si no arranca, mirá por que'
done
ok 'postgres y web corriendo'

# ----------------------------------------------------------------------
step '5/8  Health local'
# ----------------------------------------------------------------------
PORT="$(envget WEB_PORT)"; PORT="${PORT:-8000}"
BASE="http://localhost:${PORT}"

health="$(curl -fsS -m 10 "$BASE/health" 2>/dev/null)" || fail \
  "$BASE/health no responde" \
  'El contenedor esta up pero la app no contesta. Mirá:  make logs' \
  "Si el puerto esta ocupado por otra cosa, cambiá WEB_PORT en .env."
grep -q '"ok"' <<<"$health" || fail "/health devolvio algo inesperado: $health"
ok "/health OK en $BASE"

# ----------------------------------------------------------------------
step '6/8  Health interno (DB + credenciales cargadas)'
# ----------------------------------------------------------------------
ADMIN_TOKEN="$(envget ADMIN_TOKEN)"
if [[ -z "$ADMIN_TOKEN" ]]; then
  warn 'ADMIN_TOKEN vacio → /health/internal y /diag estan deshabilitados (404)'
  warn 'Se saltean los checks 6 y 7. Para habilitarlos, generá uno:'
  warn '  python3 -c "import secrets; print(secrets.token_urlsafe(32))"'
else
  internal="$(curl -fsS -m 10 -H "X-Admin-Token: $ADMIN_TOKEN" \
    "$BASE/health/internal" 2>/dev/null)" || fail \
    '/health/internal no responde (404 = ADMIN_TOKEN no matchea)' \
    'Verificá que el ADMIN_TOKEN del .env sea el que levanto el contenedor.' \
    'Si lo cambiaste recien:  make rebuild'

  # Extrae un booleano del JSON que devuelve _detailed_health() (main.py).
  # FastAPI serializa compacto ("k":true, sin espacio), asi que el patron
  # tiene que tolerar 0 o mas espacios alrededor de los dos puntos.
  jsonbool() {
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\([a-z]*\).*/\1/p" <<<"$internal" | head -1
  }

  [[ "$(jsonbool database_ok)" == "true" ]] || fail \
    'database_ok=false — la app no puede hablar con Postgres' \
    'make logs        # buscá el error de conexion' \
    'make migrate     # si la DB esta vacia, faltan las migraciones'
  ok 'database_ok=true'

  for flag in notion_token_set anthropic_key_set twilio_auth_token_set; do
    if [[ "$(jsonbool "$flag")" == "true" ]]; then
      ok "$flag=true"
    else
      fail "$flag=false — el contenedor no ve esa credencial" \
           'Esta en el .env pero no llego al contenedor. Rebuildeá:  make rebuild'
    fi
  done

  if [[ "$(jsonbool twilio_validate)" == "true" ]]; then
    ok 'twilio_validate=true (firma activa, correcto para uso real)'
  else
    warn 'twilio_validate=false — el webhook NO valida firma.'
    warn 'Sirve para curl local, pero NUNCA lo dejes asi con el tunel expuesto.'
  fi
fi

# ----------------------------------------------------------------------
step '7/8  Notion alcanzable (/diag)'
# ----------------------------------------------------------------------
if [[ -z "$ADMIN_TOKEN" ]]; then
  warn 'salteado (ADMIN_TOKEN vacio)'
else
  diag="$(curl -fsS -m 30 -H "X-Admin-Token: $ADMIN_TOKEN" "$BASE/diag" 2>/dev/null)" \
    || fail '/diag no responde' 'make logs'

  # Cada probe de _read_probe() (notion_ops.py:586) devuelve exactamente
  # "ok", "not_configured", o un objeto con el error de la API. Exigimos
  # "ok" en todas: cualquier otra cosa es un eslabon roto.
  pairs="$(grep -oE '"[a-z]+_db_read"[[:space:]]*:[[:space:]]*("[^"]*"|\{[^}]*\})' <<<"$diag")"

  # Si el regex no matcheo nada, /diag cambio de forma. Fallar fuerte: dar
  # "todo OK" porque no encontramos errores seria el peor resultado posible.
  [[ -n "$pairs" ]] || fail \
    '/diag respondio pero no se pudo interpretar la respuesta' \
    'Revisala a mano:' \
    "  curl -H \"X-Admin-Token: \$ADMIN_TOKEN\" $BASE/diag"

  bad="$(grep -vE ':[[:space:]]*"ok"$' <<<"$pairs" || true)"

  if [[ -n "$bad" ]]; then
    printf '  \033[31m✗\033[0m Notion no responde OK en todas las databases:\n\n'
    printf '%s\n' "$bad" | sed 's/^/      /'
    printf '\n'
    if grep -q 'not_configured' <<<"$bad"; then
      fail 'hay *_DB_ID sin configurar en el .env' \
           'Los IDs vienen pre-poblados en .env.example. Copialos de ahi,' \
           'o de la URL de cada database en Notion si las moviste.'
    fi
    fail 'Notion rechaza las lecturas' \
         'Causas tipicas despues de meses parado:' \
         '  - El token de la integracion fue revocado → generá uno nuevo en' \
         '    notion.so/my-integrations y actualizá NOTION_TOKEN.' \
         '  - Las databases ya no estan compartidas con la integracion.' \
         '    En cada una: ••• → Connections → agregá tu integracion.' \
         '  - Los *_DB_ID apuntan a paginas movidas o borradas.'
  fi
  ok "Notion responde OK en las $(grep -c . <<<"$pairs") databases"
fi

# ----------------------------------------------------------------------
step '8/8  URL publica'
# ----------------------------------------------------------------------
PUBLIC_HOST="$(envget PUBLIC_WEBHOOK_HOST)"
if [[ -n "$PUBLIC_HOST" ]]; then
  if curl -fsS -m 15 "https://${PUBLIC_HOST}/health" >/dev/null 2>&1; then
    ok "https://${PUBLIC_HOST}/health responde"
    printf '\n  URL para Twilio:  \033[1mhttps://%s/webhook\033[0m\n' "$PUBLIC_HOST"
  else
    fail "https://${PUBLIC_HOST}/health no responde" \
         'El tunel nombrado no esta levantado o el DNS no resuelve:' \
         '  make tunnel-up      # levanta cloudflared' \
         '  make tunnel-logs    # buscá "Registered tunnel connection"'
  fi
else
  warn 'PUBLIC_WEBHOOK_HOST vacio: no hay URL publica estable configurada.'
  warn 'Para validar sin dominio propio, levantá un tunel efimero:'
  warn '  make quick-tunnel'
fi

# ----------------------------------------------------------------------
cat <<'EOF'

──────────────────────────────────────────────────────────────
Todo lo local esta sano. Lo que falta es del lado de Twilio:

  Consola → Messaging → Try it out → WhatsApp → Sandbox settings
    When a message comes in : <tu URL publica>/webhook
    Method                  : POST

La URL tiene que coincidir EXACTA con la que sirve tu tunel. Con
TWILIO_VALIDATE=true, cualquier diferencia (http vs https, host
viejo, barra final) hace que la firma no valide y el mensaje se
caiga con 403 sin dejar rastro.

Despues mandá desde WhatsApp:  gasto 100 prueba
Y verificá:  make psql -c "select count(*) from messages;"   → 2
──────────────────────────────────────────────────────────────
EOF
