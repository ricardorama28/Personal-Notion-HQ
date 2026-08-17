#!/usr/bin/env bash
# Levanta un quick tunnel de Cloudflare y muestra la URL publica.
#
# A diferencia de `make tunnel-up`, esto NO necesita token, dominio ni
# cuenta: Cloudflare asigna una URL efimera *.trycloudflare.com. Sirve para
# validar el flujo completo (WhatsApp → Twilio → app → Notion) sin infra.
#
# OJO: la URL cambia en cada arranque. Hay que pegarla en la consola de
# Twilio cada vez. Para uso diario, montá el tunel nombrado (perfil
# "tunnel") con hostname estable.
#
# Uso:
#   bash scripts/quick-tunnel.sh     # o: make quick-tunnel

set -euo pipefail

cd "$(dirname "$0")/.."

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
else
  COMPOSE=(docker-compose)
fi

SVC=cloudflared-quick

echo "──> Levantando stack + quick tunnel…"
"${COMPOSE[@]}" --profile quick-tunnel up -d --build

echo "──> Esperando a que Cloudflare asigne la URL…"

URL=""
for _ in $(seq 1 60); do
  # cloudflared imprime la URL en un banner ASCII al conectar.
  URL="$("${COMPOSE[@]}" logs --no-color "$SVC" 2>/dev/null \
        | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' \
        | tail -1 || true)"
  [[ -n "$URL" ]] && break
  sleep 2
done

if [[ -z "$URL" ]]; then
  echo "✗ No apareció la URL después de 120s." >&2
  echo "  Mirá los logs:  ${COMPOSE[*]} logs $SVC" >&2
  exit 1
fi

echo "──> Verificando el túnel de punta a punta…"

# El hostname recien creado tarda unos segundos en resolver por DNS. Un solo
# intento fallaba con "Could not resolve host" y hacia parecer que el tunel
# estaba roto cuando en realidad solo faltaba esperar. Reintentamos ~60s.
VERIFIED=""
for _ in $(seq 1 20); do
  if curl -fsS -m 10 "$URL/health" 2>/dev/null | grep -q '"ok"'; then
    VERIFIED=1
    break
  fi
  sleep 3
done

if [[ -n "$VERIFIED" ]]; then
  echo "  ✓ $URL/health responde"
else
  # No es fatal: el tunel esta levantado y la URL es valida. Lo mas comun es
  # que el DNS todavia no haya propagado. Damos la URL igual para no bloquear.
  echo "  ! No se pudo verificar /health a traves del tunel todavia."
  echo "    Casi siempre es DNS que aun no propago. Reintenta en un minuto:"
  echo "        curl -s $URL/health"
  echo "    Si local responde ($(curl -fsS -m 5 http://localhost:8000/health 2>/dev/null || echo 'sin respuesta')),"
  echo "    la app esta bien y el problema es solo de propagacion."
  echo
  echo "    Linea cruda de cloudflared, para confirmar el hostname exacto:"
  # `|| true` obligatorio: con pipefail, un grep sin matches abortaria el
  # script justo antes de imprimir la URL, que es lo unico que se vino a buscar.
  { "${COMPOSE[@]}" logs --no-color "$SVC" 2>/dev/null \
      | grep -F 'trycloudflare.com' | tail -2 | sed 's/^/      /'; } || true
fi

cat <<EOF

──────────────────────────────────────────────────────────────
  Pegá esto en Twilio:

    $URL/webhook

  Consola → Messaging → Try it out → WhatsApp → Sandbox settings
    When a message comes in : (la URL de arriba)
    Method                  : POST
    → Save

  Después mandá desde WhatsApp:  gasto 100 prueba
──────────────────────────────────────────────────────────────

  Esta URL vive mientras el contenedor siga arriba. Si lo reiniciás,
  Cloudflare asigna otra distinta y hay que repetir el paso en Twilio.

  Logs del túnel:  ${COMPOSE[*]} logs -f $SVC
  Bajar el túnel:  make quick-down
EOF
