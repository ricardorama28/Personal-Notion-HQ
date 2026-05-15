"""
Operaciones sobre Notion. Cada funcion devuelve un dict serializable
para que se pueda mandar como tool_result a Claude.
"""
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

import dateparser
from notion_client import Client

notion = Client(auth=os.environ["NOTION_TOKEN"], notion_version="2022-06-28")
TASKS_DB = os.environ["TASKS_DB_ID"]
EVENTS_DB = os.environ["EVENTS_DB_ID"]
PROJECTS_DB = os.environ["PROJECTS_DB_ID"]


# ---------- helpers ----------

def _parse_date(text: str) -> Optional[str]:
    """Acepta 'mañana', '20 de mayo', '2026-05-20', etc. Devuelve YYYY-MM-DD."""
    if not text:
        return None
    # ya viene formateado
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    dt = dateparser.parse(text, languages=["es", "en"], settings={"PREFER_DATES_FROM": "future"})
    return dt.date().isoformat() if dt else None


def _title(text: str) -> list:
    return [{"type": "text", "text": {"content": text}}]


def _rich(text: str) -> list:
    return [{"type": "text", "text": {"content": text}}]


def _extract_title(page: dict) -> str:
    """Extrae el titulo de una page (de DB o de pagina suelta)."""
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            parts = prop.get("title", [])
            return "".join(p.get("plain_text", "") for p in parts) or "(sin titulo)"
    return "(sin titulo)"


# ---------- Projects (cache) ----------

@lru_cache(maxsize=1)
def _projects_index() -> dict:
    """Mapa nombre_lowercase -> page_id. Se cachea en memoria del proceso."""
    res = notion.databases.query(database_id=PROJECTS_DB)
    out = {}
    for row in res["results"]:
        name = _extract_title(row).strip()
        out[name.lower()] = {"id": row["id"], "name": name}
    return out


def list_projects() -> dict:
    """Lista de proyectos disponibles. Llamala antes de crear cualquier cosa con proyecto."""
    idx = _projects_index()
    return {"projects": [v["name"] for v in idx.values()]}


def _find_project_id(name: str) -> Optional[str]:
    """Match fuzzy: exacto, luego startswith, luego contains."""
    if not name:
        return None
    idx = _projects_index()
    key = name.lower().strip()
    if key in idx:
        return idx[key]["id"]
    for k, v in idx.items():
        if k.startswith(key) or key.startswith(k):
            return v["id"]
    for k, v in idx.items():
        if key in k or k in key:
            return v["id"]
    return None


def _find_subpage(parent_id: str, title: str) -> Optional[str]:
    """Busca una sub-pagina (child_page) por titulo dentro de un padre."""
    title_lower = title.lower()
    cursor = None
    while True:
        res = notion.blocks.children.list(block_id=parent_id, start_cursor=cursor) if cursor \
              else notion.blocks.children.list(block_id=parent_id)
        for block in res["results"]:
            if block["type"] == "child_page" and block["child_page"]["title"].lower() == title_lower:
                return block["id"]
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")
    return None


# ---------- Tasks ----------

def create_task(
    name: str,
    due: Optional[str] = None,
    priority: Optional[str] = None,
    project: Optional[str] = None,
    tags: Optional[list] = None,
    notes: Optional[str] = None,
) -> dict:
    props = {
        "Name": {"title": _title(name)},
        "Status": {"select": {"name": "Todo"}},
    }
    if due:
        d = _parse_date(due)
        if d:
            props["Due"] = {"date": {"start": d}}
    if priority:
        props["Priority"] = {"select": {"name": priority}}
    if project:
        sid = _find_project_id(project)
        if sid:
            props["Project"] = {"relation": [{"id": sid}]}
        else:
            return {"error": f"No encontre el proyecto '{project}'. Proyectos disponibles: {list_projects()['projects']}"}
    if tags:
        props["Tags"] = {"multi_select": [{"name": t} for t in tags]}
    if notes:
        props["Notes"] = {"rich_text": _rich(notes)}

    page = notion.pages.create(parent={"database_id": TASKS_DB}, properties=props)
    return {"ok": True, "task_id": page["id"], "name": name}


def update_task(task_id: str, status: Optional[str] = None, due: Optional[str] = None,
                priority: Optional[str] = None) -> dict:
    props = {}
    if status:
        props["Status"] = {"select": {"name": status}}
    if due:
        d = _parse_date(due)
        if d:
            props["Due"] = {"date": {"start": d}}
    if priority:
        props["Priority"] = {"select": {"name": priority}}
    if not props:
        return {"error": "nada que actualizar"}
    notion.pages.update(page_id=task_id, properties=props)
    return {"ok": True, "task_id": task_id}


def query_tasks(status: Optional[str] = None, project: Optional[str] = None,
                due_before: Optional[str] = None, due_after: Optional[str] = None,
                limit: int = 20) -> dict:
    filters = []
    if status:
        filters.append({"property": "Status", "select": {"equals": status}})
    if project:
        sid = _find_project_id(project)
        if sid:
            filters.append({"property": "Project", "relation": {"contains": sid}})
    if due_before:
        d = _parse_date(due_before)
        if d:
            filters.append({"property": "Due", "date": {"on_or_before": d}})
    if due_after:
        d = _parse_date(due_after)
        if d:
            filters.append({"property": "Due", "date": {"on_or_after": d}})

    query = {"database_id": TASKS_DB, "page_size": limit}
    if filters:
        query["filter"] = {"and": filters} if len(filters) > 1 else filters[0]
    query["sorts"] = [{"property": "Due", "direction": "ascending"}]

    res = notion.databases.query(**query)
    tasks = []
    for row in res["results"]:
        p = row["properties"]
        tasks.append({
            "id": row["id"],
            "name": _extract_title(row),
            "status": (p.get("Status", {}).get("select") or {}).get("name"),
            "priority": (p.get("Priority", {}).get("select") or {}).get("name"),
            "due": (p.get("Due", {}).get("date") or {}).get("start"),
        })
    return {"tasks": tasks, "count": len(tasks)}


# ---------- Events ----------

def create_event(name: str, date: str, event_type: Optional[str] = None,
                 project: Optional[str] = None) -> dict:
    d = _parse_date(date)
    if not d:
        return {"error": f"no pude parsear la fecha '{date}'"}
    props = {
        "Name": {"title": _title(name)},
        "Date": {"date": {"start": d}},
    }
    if event_type:
        props["Type"] = {"select": {"name": event_type}}
    if project:
        sid = _find_project_id(project)
        if sid:
            props["Project"] = {"relation": [{"id": sid}]}
    page = notion.pages.create(parent={"database_id": EVENTS_DB}, properties=props)
    return {"ok": True, "event_id": page["id"], "name": name, "date": d}


def query_events(date_from: Optional[str] = None, date_to: Optional[str] = None,
                 project: Optional[str] = None, limit: int = 20) -> dict:
    filters = []
    if date_from:
        d = _parse_date(date_from)
        if d:
            filters.append({"property": "Date", "date": {"on_or_after": d}})
    if date_to:
        d = _parse_date(date_to)
        if d:
            filters.append({"property": "Date", "date": {"on_or_before": d}})
    if project:
        sid = _find_project_id(project)
        if sid:
            filters.append({"property": "Project", "relation": {"contains": sid}})

    query = {"database_id": EVENTS_DB, "page_size": limit,
             "sorts": [{"property": "Date", "direction": "ascending"}]}
    if filters:
        query["filter"] = {"and": filters} if len(filters) > 1 else filters[0]

    res = notion.databases.query(**query)
    events = []
    for row in res["results"]:
        p = row["properties"]
        events.append({
            "id": row["id"],
            "name": _extract_title(row),
            "date": (p.get("Date", {}).get("date") or {}).get("start"),
            "type": (p.get("Type", {}).get("select") or {}).get("name"),
        })
    return {"events": events, "count": len(events)}


# ---------- Notes (apuntes) ----------

def add_note(project: str, content: str, heading: Optional[str] = None) -> dict:
    """Agrega bloques a la sub-pagina 'Apuntes' del proyecto."""
    sid = _find_project_id(project)
    if not sid:
        return {"error": f"no encontre el proyecto '{project}'. Disponibles: {list_projects()['projects']}"}

    apuntes_id = _find_subpage(sid, "Apuntes")
    if not apuntes_id:
        return {"error": f"el proyecto '{project}' no tiene sub-pagina 'Apuntes'"}

    children = []
    # heading con fecha si no se pasa heading explicito
    h = heading or f"{datetime.now().strftime('%Y-%m-%d')}"
    children.append({
        "type": "heading_3",
        "heading_3": {"rich_text": _rich(h)}
    })
    # contenido como parrafos (split por doble newline)
    for paragraph in content.split("\n\n"):
        if paragraph.strip():
            children.append({
                "type": "paragraph",
                "paragraph": {"rich_text": _rich(paragraph.strip())}
            })

    notion.blocks.children.append(block_id=apuntes_id, children=children)
    return {"ok": True, "project": project, "added_blocks": len(children)}


# ---------- Diagrams ----------

def create_diagram(project: str, title: str, mermaid_code: str,
                   description: Optional[str] = None) -> dict:
    """Crea una pagina nueva dentro de 'Diagramas' del proyecto, con un code block mermaid."""
    sid = _find_project_id(project)
    if not sid:
        return {"error": f"no encontre el proyecto '{project}'"}

    diagramas_id = _find_subpage(sid, "Diagramas")
    if not diagramas_id:
        return {"error": f"el proyecto '{project}' no tiene sub-pagina 'Diagramas'"}

    children = []
    if description:
        children.append({
            "type": "paragraph",
            "paragraph": {"rich_text": _rich(description)}
        })
    children.append({
        "type": "code",
        "code": {
            "rich_text": _rich(mermaid_code),
            "language": "mermaid"
        }
    })

    new_page = notion.pages.create(
        parent={"page_id": diagramas_id},
        properties={"title": {"title": _title(title)}},
        children=children,
    )
    return {"ok": True, "page_id": new_page["id"], "title": title, "url": new_page.get("url")}


# ---------- Context helper ----------

def today_context() -> str:
    """Para inyectar en el system prompt."""
    now = datetime.now()
    dias = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
    return f"Hoy es {dias[now.weekday()]} {now.strftime('%Y-%m-%d')}."
