#!/usr/bin/env bash
# Simula webhooks de Twilio contra la app local.
#
# Uso:
#   TWILIO_VALIDATE=false uvicorn main:app --reload --port 8000
#   bash scripts/simulate_webhook.sh                    # corre todos los casos
#   bash scripts/simulate_webhook.sh "tarea: estudiar"  # corre uno solo
#
# Para probar la validacion de firma con TWILIO_VALIDATE=true necesitas
# generar la firma con el SDK de Twilio o usar `twilio-cli`. Para uso comun
# en local, dejá TWILIO_VALIDATE=false.

set -euo pipefail

URL="${WEBHOOK_URL:-http://localhost:8000/webhook}"
FROM="${MY_WHATSAPP:-whatsapp:+5491100000000}"

send() {
  local body="$1"
  local sid="SM$(date +%s%N | head -c 16)"
  echo "──> $body  (sid=$sid)"
  curl -sS -X POST "$URL" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "From=$FROM" \
    --data-urlencode "Body=$body" \
    --data-urlencode "MessageSid=$sid"
  echo -e "\n"
}

if [[ $# -gt 0 ]]; then
  send "$*"
  exit 0
fi

echo "== Salud =="
curl -sS "${URL%/webhook}/health"; echo

echo "== Casos por tipo =="
send "gasto 450 super con debito"
send "recordame mañana comprar leche"
send "nota: idea para el TP de IA"
send "comi milanesa al almuerzo"
send "hice ejercicio"
send "parcial de algebra el viernes"
send "xyzzy lorem ipsum"

echo "== Idempotencia: mismo SID dos veces =="
SID_FIX="SMtestrepeat$(date +%s)"
for i in 1 2; do
  echo "── intento $i sid=$SID_FIX"
  curl -sS -X POST "$URL" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "From=$FROM" \
    --data-urlencode "Body=gasto 100 cafe" \
    --data-urlencode "MessageSid=$SID_FIX"
  echo
done

echo "== Numero no autorizado =="
curl -sS -X POST "$URL" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "From=whatsapp:+99999999" \
  --data-urlencode "Body=hola" \
  --data-urlencode "MessageSid=SMnoauth"
echo

echo "== Mensaje muy largo (>4096) =="
big=$(printf 'x%.0s' $(seq 1 5000))
curl -sS -X POST "$URL" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "From=$FROM" \
  --data-urlencode "Body=$big" \
  --data-urlencode "MessageSid=SMbig"
echo
