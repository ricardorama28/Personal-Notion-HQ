"""Command Center web UI (Fase I).

Layout 3-paneles tipo ChatGPT/Claude. Sin build step:
FastAPI + Jinja2 + HTMX + Alpine.js + Tailwind CDN.

Todo `/admin/*` esta protegido por ADMIN_TOKEN. Sin token = 404 (no
revela que la UI existe).
"""
from web.admin import router

__all__ = ["router"]
