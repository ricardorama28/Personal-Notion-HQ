#!/usr/bin/env python3
"""Healthcheck para el contenedor.

Politica:
1. Siempre golpear /health (publico) → confirma que el proceso responde.
2. Si ADMIN_TOKEN esta en el entorno, golpear /health/internal con el
   header y validar database_ok != False. Asi el HEALTHCHECK del docker
   sabe de la DB sin exponer info en el endpoint publico.

Sale 1 si:
- /health no responde o ok != true;
- /health/internal devuelve database_ok=False (cuando hay token).
"""
import json
import os
import sys
import urllib.error
import urllib.request

PUBLIC_URL = os.environ.get("HEALTHCHECK_URL", "http://127.0.0.1:8000/health")
INTERNAL_URL = os.environ.get("HEALTHCHECK_INTERNAL_URL",
                              "http://127.0.0.1:8000/health/internal")
TOKEN = os.environ.get("ADMIN_TOKEN", "")


def fetch(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=4) as r:
        return json.loads(r.read().decode("utf-8"))


# 1) Publico
try:
    body = fetch(PUBLIC_URL)
except Exception as e:
    print(f"healthcheck: /health no responde: {e}", file=sys.stderr)
    sys.exit(1)

if not body.get("ok"):
    print(f"healthcheck: /health ok=false body={body}", file=sys.stderr)
    sys.exit(1)

# 2) Interno (opcional, solo si tenemos token)
if TOKEN:
    try:
        body = fetch(INTERNAL_URL, headers={"X-Admin-Token": TOKEN})
    except urllib.error.HTTPError as e:
        # 404 = token incorrecto, no es razon para marcar el contenedor unhealthy.
        # Logueamos warning y damos OK porque /health publico anduvo.
        print(f"healthcheck: /health/internal {e.code} (sin chequeo profundo)",
              file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"healthcheck: /health/internal falló: {e}", file=sys.stderr)
        sys.exit(0)  # publico OK, no degradamos por esto
    if body.get("database_ok") is False:
        print(f"healthcheck: database_ok=False ({body.get('database_error')})",
              file=sys.stderr)
        sys.exit(1)

sys.exit(0)
