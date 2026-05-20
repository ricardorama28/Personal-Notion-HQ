#!/usr/bin/env python3
"""Healthcheck para el contenedor.

Usado por HEALTHCHECK del Dockerfile y por el healthcheck del compose.
Sale 0 si /health responde con ok=true. Si la app esta arriba pero la DB
esta caida (ok=false), sale 1 — esto le da al compose una senal real para
reintentar/reiniciar.
"""
import json
import os
import sys
import urllib.request

URL = os.environ.get("HEALTHCHECK_URL", "http://127.0.0.1:8000/health")

try:
    with urllib.request.urlopen(URL, timeout=4) as r:
        body = json.loads(r.read().decode("utf-8"))
except Exception as e:
    print(f"healthcheck: error fetching {URL}: {e}", file=sys.stderr)
    sys.exit(1)

if not body.get("ok"):
    print(f"healthcheck: ok=false body={body}", file=sys.stderr)
    sys.exit(1)

sys.exit(0)
