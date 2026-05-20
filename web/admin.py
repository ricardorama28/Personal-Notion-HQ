"""Router de /admin/* (Command Center).

Vistas:
- GET /admin/                       → dashboard (sidebar sesiones + chat vacio)
- GET /admin/login?token=...        → set cookie + redirect
- POST /admin/sessions/new          → crea sesion web y redirige
- GET /admin/sessions               → list (JSON o fragment)
- GET /admin/c/{session_key}        → chat de una sesion
- POST /admin/c/{session_key}/send  → manda mensaje, devuelve fragment
- POST /admin/c/{session_key}/approve → confirma plan pending
- POST /admin/c/{session_key}/cancel  → cancela plan pending
- GET /admin/runs                   → listado de runs
- GET /admin/runs/{run_id}          → detalle de run
- POST /admin/runs/{run_id}/retry   → reintenta async_error si es safe
- GET /admin/agents                 → vista de agentes/tools
- GET /admin/costs                  → vista de costos
- GET /admin/alerts                 → alertas
- GET /admin/config                 → flags publicos
- GET /admin/api/system             → JSON de health (resumen)

Todo gateado por `require_admin`. Sin token → 404.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import (APIRouter, Body, Cookie, Depends, Form, HTTPException,
                     Request, Response)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
import db as db_mod
import repos
from web import chat_pipeline, queries
from web.auth import check_token, require_admin

log = logging.getLogger("wpp.web")

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)

# Filtro pequeño para mostrar timestamps amistosos.
def _fmt_ts(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return v.strftime("%Y-%m-%d %H:%M")

templates.env.filters["fmt_ts"] = _fmt_ts


# ---------- Login (set cookie) ----------

@router.get("/login", response_class=HTMLResponse)
async def login(request: Request, token: Optional[str] = None):
    """GET /admin/login?token=... → set cookie httpOnly y redirect a /admin/.

    Sin token o token incorrecto → 404 para no revelar la UI.
    """
    if not config.ADMIN_TOKEN:
        raise HTTPException(status_code=404)
    if not token or not check_token(token):
        raise HTTPException(status_code=404)
    resp = RedirectResponse("/admin/", status_code=302)
    resp.set_cookie("admin_token", token, httponly=True, samesite="lax",
                    secure=False)  # secure=True cuando todo sea https
    return resp


# ---------- Index / sesiones ----------

@router.get("/", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
async def index(request: Request):
    sessions = await queries.list_sessions(source="web", limit=30)
    alerts = await queries.alerts()
    cost = await queries.cost_breakdown(days=7)
    pending = await queries.pending_confirmations()
    return templates.TemplateResponse(
        request, "index.html", {"sessions": sessions, "alerts": alerts,
         "cost": cost, "pending": pending,
         "agents": queries.agents_overview()},
    )


@router.get("/sessions", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
async def list_sessions_view(request: Request,
                              source: Optional[str] = None):
    sessions = await queries.list_sessions(source=source, limit=100)
    return templates.TemplateResponse(
        request, "sessions.html", {"sessions": sessions, "source": source},
    )


@router.post("/sessions/new", dependencies=[Depends(require_admin)])
async def new_session(request: Request):
    """Crea una nueva sesion web vacia y redirige a /admin/c/{key}."""
    key = chat_pipeline.new_session_key()
    await repos.sessions.save(key, [], source="web")
    return RedirectResponse(f"/admin/c/{key}", status_code=303)


# ---------- Chat de una sesion ----------

@router.get("/c/{session_key}", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
async def chat_view(request: Request, session_key: str):
    history = await repos.sessions.load(session_key)
    runs = await queries.session_runs(session_key, limit=30)
    cost = await queries.session_cost_summary(session_key)
    sessions = await queries.list_sessions(source="web", limit=30)
    pending = [p for p in await queries.pending_confirmations()
               if p["session_key"] == session_key]
    return templates.TemplateResponse(
        request, "chat.html", {"session_key": session_key,
         "history": history, "runs": runs, "cost": cost,
         "sessions": sessions, "pending": pending,
         "agents": queries.agents_overview()},
    )


@router.post("/c/{session_key}/send", response_class=HTMLResponse,
             dependencies=[Depends(require_admin)])
async def chat_send(request: Request, session_key: str,
                     body: str = Form(...)):
    """Procesa el mensaje y devuelve un fragment HTMX con la respuesta."""
    from main import client as anthropic
    result = await chat_pipeline.send_message(
        session_key=session_key, body=body, anthropic_client=anthropic,
    )
    return templates.TemplateResponse(
        request, "_chat_turn.html", {"user_text": body, "result": result,
         "session_key": session_key},
    )


@router.post("/c/{session_key}/approve", response_class=HTMLResponse,
             dependencies=[Depends(require_admin)])
async def chat_approve(request: Request, session_key: str):
    from main import client as anthropic
    result = await chat_pipeline.send_message(
        session_key=session_key, body="1", anthropic_client=anthropic,
    )
    return templates.TemplateResponse(
        request, "_chat_turn.html", {"user_text": "(confirmar)", "result": result,
         "session_key": session_key},
    )


@router.post("/c/{session_key}/cancel", response_class=HTMLResponse,
             dependencies=[Depends(require_admin)])
async def chat_cancel(request: Request, session_key: str):
    from main import client as anthropic
    result = await chat_pipeline.send_message(
        session_key=session_key, body="cancelar", anthropic_client=anthropic,
    )
    return templates.TemplateResponse(
        request, "_chat_turn.html", {"user_text": "(cancelar)", "result": result,
         "session_key": session_key},
    )


# ---------- Runs ----------

@router.get("/runs", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
async def runs_list(request: Request, async_state: Optional[str] = None):
    runs = await queries.recent_runs(limit=50, async_state=async_state)
    return templates.TemplateResponse(
        request, "runs.html", {"runs": runs, "async_state": async_state},
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
async def run_detail(request: Request, run_id: str):
    run = await queries.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "run_detail.html", {"run": run},
    )


@router.post("/runs/{run_id}/retry",
             dependencies=[Depends(require_admin)])
async def run_retry(request: Request, run_id: str):
    """Reintenta un async_error SOLO si safety_level=safe.

    No-op (404) para destructive/unsafe/bulk. Para safe: re-encola el
    plan en background usando el mismo session_key y el user_text del
    plan original.
    """
    run = await queries.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404)
    if run.get("safety_level") not in (None, "safe"):
        # explicit refusal
        return JSONResponse(
            {"ok": False, "error": "retry no permitido para "
                                   f"safety_level={run.get('safety_level')}"},
            status_code=400)
    if run.get("async_state") != "async_error":
        return JSONResponse(
            {"ok": False, "error": "solo se reintenta async_error"},
            status_code=400)
    # encolar nuevo run
    import async_runner
    from fastapi import BackgroundTasks
    # No tenemos BackgroundTasks aca; usamos asyncio.create_task como
    # equivalente sync (similar semantica: misma proceso, sin retry).
    import asyncio
    plan = run.get("plan") or {}
    sender = run.get("session_key") or ""
    new_run_id = await repos.agent_runs.create(
        sid=None, session_key=sender, route=plan.get("route", "unknown"),
        intent=plan.get("intent"), model=plan.get("model"),
        plan=plan, safety_level=plan.get("safety_level"),
        confirmed_from=run.get("id"), async_state="async_pending",
    )
    asyncio.create_task(async_runner.run_in_background(
        plan_payload=plan, sender=sender, sid=None,
        run_id=new_run_id, inbox_id=None,
    ))
    return JSONResponse({"ok": True, "new_run_id": new_run_id})


# ---------- Otras vistas ----------

@router.get("/agents", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
async def agents_view(request: Request):
    return templates.TemplateResponse(
        request, "agents.html", {"agents": queries.agents_overview(),
         "tool_usage": await queries.tool_usage(days=7)},
    )


@router.get("/costs", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
async def costs_view(request: Request, days: int = 7):
    cost = await queries.cost_breakdown(days=days)
    return templates.TemplateResponse(
        request, "costs.html", {"cost": cost, "days": days},
    )


@router.get("/alerts", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
async def alerts_view(request: Request):
    return templates.TemplateResponse(
        request, "alerts.html", {"alerts": await queries.alerts(),
         "errors": await queries.recent_errors(limit=30)},
    )


@router.get("/config", response_class=HTMLResponse,
            dependencies=[Depends(require_admin)])
async def config_view(request: Request):
    return templates.TemplateResponse(
        request, "config.html", {"flags": queries.secrets_redacted_config()},
    )


@router.get("/api/system", dependencies=[Depends(require_admin)])
async def system_status():
    """JSON con health resumido — el frontend lo polea para refrescar
    el indicador derecho."""
    db_ok = None
    if db_mod.is_postgres_enabled():
        try:
            await db_mod.ping()
            db_ok = True
        except Exception as e:
            db_ok = False
    return {
        "database_ok": db_ok,
        "sessions_backend": config.SESSIONS_BACKEND,
        "async_enabled": config.ASYNC_ENABLED,
        "twilio_outbound_configured": bool(
            config.TWILIO_ACCOUNT_SID and config.TWILIO_FROM_WHATSAPP),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
