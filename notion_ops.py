"""
Operaciones sobre Notion. Cada funcion devuelve un dict serializable
para que se pueda mandar como tool_result a Claude.

Workspace reestructurado (10 DB bajo PERSONAL HQ). Las funciones de
creacion vinculan automaticamente el registro a la fila de Inbox/WhatsApp
Log del mensaje en curso (via contextvar) y registran el tipo detectado
para que el webhook pueda cerrar esa fila.
"""
import contextvars
import unicodedata
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

import dateparser
from notion_client import Client
from notion_client.errors import APIResponseError

import config

notion = Client(auth=config.NOTION_TOKEN or "missing-token",
                notion_version="2022-06-28")

TASKS_DB = config.TASKS_DB_ID
EVENTS_DB = config.EVENTS_DB_ID
PROJECTS_DB = config.PROJECTS_DB_ID
NOTES_DB = config.NOTES_DB_ID
EXPENSES_DB = config.EXPENSES_DB_ID
MEALS_DB = config.MEALS_DB_ID
HABITS_DB = config.HABITS_DB_ID
HABITLOG_DB = config.HABITLOG_DB_ID
INBOX_DB = config.INBOX_DB_ID

# enums vivos (deben coincidir con el esquema de Notion)
TASK_STATUS = {"To Do", "Doing", "Done"}
TASK_TYPES = {"Task", "Reminder"}
PRIORITY = {"High", "Med", "Low"}
EVENT_TYPES = {"Class", "Exam", "Appointment", "Deadline", "Personal"}
EVENT_TYPE_ES = {
    "clase": "Class", "parcial": "Exam", "examen": "Exam", "final": "Exam",
    "turno": "Appointment", "cita": "Appointment", "entrega": "Deadline",
    "deadline": "Deadline", "personal": "Personal", "otro": "Personal",
}
NOTE_TYPES = {"Note", "Idea", "Reference", "ClassNote"}
NOTE_TAGS = {"Estudio", "Tech", "Personal"}
EXPENSE_CATEGORIES = {"Supermercado", "Comida", "Transporte", "Servicios",
                      "Salud", "Auto", "Casa", "Ocio", "Otros"}
EXPENSE_METHODS = {"Efectivo", "Débito", "Crédito", "Transferencia"}
MEAL_TYPES = {"Desayuno", "Almuerzo", "Merienda", "Cena", "Snack"}
HABIT_STATUS = {"Done", "Skipped"}

# Dias de la semana en es/en -> numero de weekday() (lunes=0).
WEEKDAYS = {
    "lunes": 0, "monday": 0, "lun": 0, "mon": 0,
    "martes": 1, "tuesday": 1, "mar": 1, "tue": 1,
    "miercoles": 2, "wednesday": 2, "mie": 2, "wed": 2,
    "jueves": 3, "thursday": 3, "jue": 3, "thu": 3,
    "viernes": 4, "friday": 4, "vie": 4, "fri": 4,
    "sabado": 5, "saturday": 5, "sab": 5, "sat": 5,
    "domingo": 6, "sunday": 6, "dom": 6, "sun": 6,
}

# Tope de eventos por llamada a create_events_recurring. Existe para que un
# rango mal parseado (ej. "hasta 2030") no dispare cientos de escrituras.
MAX_RECURRING_EVENTS = 200


# ---------- contexto de Inbox (por request) ----------

_inbox_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "inbox_page_id", default=None)
_writes_ctx: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar(
    "writes", default=None)


def set_inbox(page_id: Optional[str]) -> None:
    """Activa el contexto: las creaciones siguientes se vinculan a esta fila."""
    _inbox_ctx.set(page_id)
    _writes_ctx.set([])


def clear_inbox() -> None:
    _inbox_ctx.set(None)
    _writes_ctx.set(None)


def _link_inbox(props: dict) -> None:
    pid = _inbox_ctx.get()
    if pid:
        props["Inbox"] = {"relation": [{"id": pid}]}


def _record_write(detected_type: Optional[str]) -> None:
    w = _writes_ctx.get()
    if w is not None:
        w.append(detected_type)


# ---------- helpers ----------

def _parse_date(text: str) -> Optional[str]:
    """Acepta 'mañana', '20 de mayo', '2026-05-20', etc. Devuelve YYYY-MM-DD."""
    if not text:
        return None
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    dt = dateparser.parse(text, languages=["es", "en"],
                          settings={"PREFER_DATES_FROM": "future"})
    return dt.date().isoformat() if dt else None


def _title(text: str) -> list:
    return [{"type": "text", "text": {"content": text}}]


def _rich(text: str) -> list:
    return [{"type": "text", "text": {"content": text}}]


def _sel(value: Optional[str], allowed: set) -> Optional[dict]:
    """Devuelve un select prop solo si el valor es una opcion valida."""
    if value and value in allowed:
        return {"select": {"name": value}}
    return None


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
    """Lista de proyectos disponibles. Llamala antes de crear algo con proyecto."""
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


@lru_cache(maxsize=1)
def _projects_schema() -> dict:
    """Propiedades de PROJECTS_DB, cacheadas.

    A diferencia del resto de las DB, el esquema de Projects no esta
    replicado en constantes arriba: no sabemos como se llaman sus columnas.
    Lo descubrimos en runtime en vez de asumir "Name"/"Status".
    """
    return notion.databases.retrieve(database_id=PROJECTS_DB).get("properties", {})


def _title_prop_name(props: dict) -> str:
    """Nombre de la propiedad de tipo title (la que hace de titulo de fila)."""
    for name, spec in props.items():
        if spec.get("type") == "title":
            return name
    return "Name"


def create_project(name: str, status: Optional[str] = None) -> dict:
    """Crea un proyecto en PROJECTS_DB.

    Setea el titulo siempre; `status` es best-effort: solo se aplica si la DB
    tiene una propiedad select/status con ese nombre de opcion.
    """
    name = (name or "").strip()
    if not name:
        return {"error": "el proyecto necesita un nombre"}

    # No duplicar: si ya existe con ese nombre, devolvemos el que hay.
    idx = _projects_index()
    existing = idx.get(name.lower())
    if existing:
        return {"ok": True, "existing": True, "project_id": existing["id"],
                "name": existing["name"]}

    try:
        schema = _projects_schema()
    except APIResponseError as e:
        return {"error": f"no pude leer el esquema de Projects: {e}"}

    props = {_title_prop_name(schema): {"title": _title(name)}}

    if status:
        for prop_name, spec in schema.items():
            kind = spec.get("type")
            if kind not in ("select", "status"):
                continue
            options = (spec.get(kind) or {}).get("options", [])
            match = next((o["name"] for o in options
                          if o.get("name", "").lower() == status.lower()), None)
            if match:
                props[prop_name] = {kind: {"name": match}}
                break

    page = notion.pages.create(parent={"database_id": PROJECTS_DB},
                               properties=props)

    # Sin esto, _projects_index() sigue devolviendo el cache viejo por el resto
    # de la vida del proceso y create_task/create_event no encontrarian el
    # proyecto recien creado.
    _projects_index.cache_clear()
    _record_write(None)
    return {"ok": True, "project_id": page["id"], "name": name}


# ---------- Habits (cache) ----------

@lru_cache(maxsize=1)
def _habits_index() -> dict:
    """Mapa nombre_lowercase -> {id, name, active}."""
    if not HABITS_DB:
        return {}
    res = notion.databases.query(database_id=HABITS_DB)
    out = {}
    for row in res["results"]:
        name = _extract_title(row).strip()
        active = (row["properties"].get("Active", {}) or {}).get("checkbox", False)
        out[name.lower()] = {"id": row["id"], "name": name, "active": active}
    return out


def list_habits() -> dict:
    """Lista de habitos activos. Llamala antes de loguear un habito."""
    idx = _habits_index()
    return {"habits": [v["name"] for v in idx.values() if v["active"]]}


def _find_habit(name: str) -> Optional[dict]:
    if not name:
        return None
    idx = _habits_index()
    key = name.lower().strip()
    if key in idx:
        return idx[key]
    for k, v in idx.items():
        if k.startswith(key) or key.startswith(k):
            return v
    for k, v in idx.items():
        if key in k or k in key:
            return v
    return None


# ---------- Inbox / WhatsApp Log ----------

def create_inbox_entry(raw: str, sender: str,
                       twilio_sid: Optional[str] = None) -> dict:
    """Crea (o recupera) la fila del mensaje en Inbox/WhatsApp Log.

    Idempotencia: si llega el mismo Twilio SID (retry de Twilio) devuelve
    la fila existente con existing=True para no duplicar el procesamiento.
    """
    if not INBOX_DB:
        return {"page_id": None, "existing": False}

    if twilio_sid:
        try:
            found = notion.databases.query(
                database_id=INBOX_DB, page_size=1,
                filter={"property": "Twilio SID",
                        "rich_text": {"equals": twilio_sid}},
            )
            if found["results"]:
                return {"page_id": found["results"][0]["id"], "existing": True}
        except APIResponseError:
            pass  # ante error de query seguimos y creamos igual

    clean_sender = sender.replace("whatsapp:", "").strip()
    props = {
        "Title": {"title": _title((raw[:60] or "(vacío)"))},
        "Raw Message": {"rich_text": _rich(raw[:1900])},
        "Sender": {"phone_number": clean_sender or None},
        "Source": {"select": {"name": "WhatsApp"}},
        "Received At": {"date": {
            "start": datetime.now(timezone.utc).isoformat()}},
        "Processing Status": {"select": {"name": "Pending"}},
    }
    if twilio_sid:
        props["Twilio SID"] = {"rich_text": _rich(twilio_sid)}
    page = notion.pages.create(parent={"database_id": INBOX_DB},
                               properties=props)
    return {"page_id": page["id"], "existing": False}


def finalize_inbox(page_id: str, action_taken: str) -> dict:
    """Cierra la fila del Inbox segun lo que se haya hecho con el mensaje."""
    if not page_id:
        return {"ok": False}
    writes = _writes_ctx.get() or []
    detected = next((w for w in writes if w), None) or "Unknown"
    did_write = len(writes) > 0
    status = "Auto-processed" if did_write else "Needs review"
    props = {
        "Processing Status": {"select": {"name": status}},
        "Detected Type": {"select": {"name": detected}},
        "Action Taken": {"rich_text": _rich((action_taken or "")[:1900])},
    }
    try:
        notion.pages.update(page_id=page_id, properties=props)
        return {"ok": True, "status": status, "detected": detected}
    except APIResponseError as e:
        return {"ok": False, "error": str(e)}


# ---------- Tasks ----------

def create_task(name: str, due: Optional[str] = None,
                 priority: Optional[str] = None, project: Optional[str] = None,
                 tags: Optional[list] = None, notes: Optional[str] = None,
                 task_type: str = "Task") -> dict:
    tt = task_type if task_type in TASK_TYPES else "Task"
    props = {
        "Name": {"title": _title(name)},
        "Status": {"select": {"name": "To Do"}},
        "Type": {"select": {"name": tt}},
        "Source": {"select": {"name": "WhatsApp"}},
    }
    if due:
        d = _parse_date(due)
        if d:
            props["Due"] = {"date": {"start": d}}
    p = _sel(priority, PRIORITY)
    if p:
        props["Priority"] = p
    if project:
        sid = _find_project_id(project)
        if sid:
            props["Project"] = {"relation": [{"id": sid}]}
        else:
            return {"error": f"No encontre el proyecto '{project}'. "
                             f"Disponibles: {list_projects()['projects']}"}
    if tags:
        props["Tags"] = {"multi_select": [{"name": t} for t in tags]}
    if notes:
        props["Notes"] = {"rich_text": _rich(notes)}
    _link_inbox(props)

    page = notion.pages.create(parent={"database_id": TASKS_DB},
                               properties=props)
    _record_write("Reminder" if tt == "Reminder" else "Task")
    return {"ok": True, "task_id": page["id"], "name": name, "type": tt}


def update_task(task_id: str, status: Optional[str] = None,
                due: Optional[str] = None,
                priority: Optional[str] = None) -> dict:
    props = {}
    s = _sel(status, TASK_STATUS)
    if s:
        props["Status"] = s
    if due:
        d = _parse_date(due)
        if d:
            props["Due"] = {"date": {"start": d}}
    p = _sel(priority, PRIORITY)
    if p:
        props["Priority"] = p
    if not props:
        return {"error": "nada que actualizar"}
    notion.pages.update(page_id=task_id, properties=props)
    return {"ok": True, "task_id": task_id}


def query_tasks(status: Optional[str] = None, project: Optional[str] = None,
                due_before: Optional[str] = None, due_after: Optional[str] = None,
                limit: int = 20) -> dict:
    filters = []
    if status and status in TASK_STATUS:
        filters.append({"property": "Status", "select": {"equals": status}})
    if project:
        sid = _find_project_id(project)
        if sid:
            filters.append({"property": "Project",
                            "relation": {"contains": sid}})
    if due_before:
        d = _parse_date(due_before)
        if d:
            filters.append({"property": "Due", "date": {"on_or_before": d}})
    if due_after:
        d = _parse_date(due_after)
        if d:
            filters.append({"property": "Due", "date": {"on_or_after": d}})

    query = {"database_id": TASKS_DB, "page_size": limit,
             "sorts": [{"property": "Due", "direction": "ascending"}]}
    if filters:
        query["filter"] = {"and": filters} if len(filters) > 1 else filters[0]

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
    et = "Personal"
    if event_type:
        et = event_type if event_type in EVENT_TYPES else \
            EVENT_TYPE_ES.get(event_type.lower(), "Personal")
    props["Type"] = {"select": {"name": et}}
    if project:
        sid = _find_project_id(project)
        if sid:
            props["Project"] = {"relation": [{"id": sid}]}
    page = notion.pages.create(parent={"database_id": EVENTS_DB},
                               properties=props)
    # EVENTS no tiene relacion Inbox; igual cuenta como escritura.
    _record_write(None)
    return {"ok": True, "event_id": page["id"], "name": name, "date": d,
            "type": et}


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def _weekday_numbers(weekdays: list) -> tuple:
    """['lunes', 'Miércoles'] -> ([0, 2], []). Devuelve (numeros, no_reconocidos)."""
    nums, unknown = [], []
    for raw in weekdays or []:
        key = _strip_accents(str(raw)).strip().lower()
        if key in WEEKDAYS:
            n = WEEKDAYS[key]
            if n not in nums:
                nums.append(n)
        else:
            unknown.append(str(raw))
    return sorted(nums), unknown


def expand_weekdays(date_from: str, date_until: str, weekdays: list) -> dict:
    """Expande un rango a las fechas concretas que caen en esos dias.

    Separada de la creacion para poder testearla sin tocar Notion. Ambos
    extremos del rango son inclusivos.
    """
    start = _parse_date(date_from)
    end = _parse_date(date_until)
    if not start:
        return {"error": f"no pude parsear la fecha de inicio '{date_from}'"}
    if not end:
        return {"error": f"no pude parsear la fecha de fin '{date_until}'"}

    nums, unknown = _weekday_numbers(weekdays)
    if unknown:
        return {"error": f"no reconozco estos dias: {', '.join(unknown)}"}
    if not nums:
        return {"error": "necesito al menos un dia de la semana"}

    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    if d1 < d0:
        return {"error": f"el rango esta invertido: {start} es posterior a {end}"}

    dates = []
    cur = d0
    while cur <= d1:
        if cur.weekday() in nums:
            dates.append(cur.isoformat())
            if len(dates) > MAX_RECURRING_EVENTS:
                return {"error": f"el rango genera mas de {MAX_RECURRING_EVENTS} "
                                 f"eventos ({start} a {end}); acotalo"}
        cur += timedelta(days=1)

    return {"dates": dates, "date_from": start, "date_until": end}


def _existing_event_dates(name: str, date_from: str, date_to: str) -> set:
    """Fechas del rango que ya tienen un evento con este nombre.

    Pagina la query porque el rango puede contener muchos eventos ajenos y
    un page_size fijo dejaria duplicados afuera del chequeo.
    """
    target = name.strip().lower()
    found, cursor = set(), None
    for _ in range(10):  # tope de paginas; 10 x 100 = 1000 eventos
        query = {
            "database_id": EVENTS_DB,
            "page_size": 100,
            "filter": {"and": [
                {"property": "Date", "date": {"on_or_after": date_from}},
                {"property": "Date", "date": {"on_or_before": date_to}},
            ]},
        }
        if cursor:
            query["start_cursor"] = cursor
        res = notion.databases.query(**query)
        for row in res.get("results", []):
            if _extract_title(row).strip().lower() != target:
                continue
            d = (row["properties"].get("Date", {}).get("date") or {}).get("start")
            if d:
                found.add(d[:10])
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")
    return found


def create_events_recurring(name: str, weekdays: list, date_from: str,
                            date_until: str, event_type: Optional[str] = None,
                            project: Optional[str] = None) -> dict:
    """Crea una serie de eventos: los `weekdays` entre dos fechas, inclusive.

    Expande el rango del lado del servidor y crea todo en una sola tool call.
    Si el modelo emitiera un create_event por fecha, chocaria contra el tope
    de iteraciones del agente (agents/base.py) mucho antes de terminar.
    """
    exp = expand_weekdays(date_from, date_until, weekdays)
    if "error" in exp:
        return exp
    dates = exp["dates"]
    if not dates:
        return {"error": f"no hay ninguna de esas fechas entre "
                         f"{exp['date_from']} y {exp['date_until']}"}

    et = "Personal"
    if event_type:
        et = event_type if event_type in EVENT_TYPES else \
            EVENT_TYPE_ES.get(event_type.lower(), "Personal")

    project_id = _find_project_id(project) if project else None
    if project and not project_id:
        return {"error": f"No encontre el proyecto '{project}'. "
                         f"Disponibles: {list_projects()['projects']}"}

    # Repetir el mismo pedido no debe duplicar la serie entera.
    already = _existing_event_dates(name, exp["date_from"], exp["date_until"])

    created, skipped, failed = [], [], []
    for d in dates:
        if d in already:
            skipped.append(d)
            continue
        props = {
            "Name": {"title": _title(name)},
            "Date": {"date": {"start": d}},
            "Type": {"select": {"name": et}},
        }
        if project_id:
            props["Project"] = {"relation": [{"id": project_id}]}
        try:
            notion.pages.create(parent={"database_id": EVENTS_DB},
                                properties=props)
            created.append(d)
            _record_write(None)
        except APIResponseError as e:
            # No abortamos: informamos parciales para que el usuario sepa
            # exactamente que quedo cargado y que no.
            failed.append({"date": d, "error": str(e)})

    out = {"ok": True, "name": name, "type": et,
           "date_from": exp["date_from"], "date_until": exp["date_until"],
           "created": len(created), "created_dates": created}
    if skipped:
        out["skipped_existing"] = len(skipped)
        out["skipped_dates"] = skipped
    if failed:
        out["failed"] = failed
    return out


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
            filters.append({"property": "Project",
                            "relation": {"contains": sid}})

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


# ---------- Notes ----------

def add_note(content: str, title: Optional[str] = None,
             note_type: str = "Note", project: Optional[str] = None,
             tags: Optional[list] = None) -> dict:
    """Crea una nota en la DB NOTES (reemplaza la vieja sub-pagina Apuntes)."""
    if not NOTES_DB:
        return {"error": "NOTES_DB_ID no configurado"}
    nt = note_type if note_type in NOTE_TYPES else "Note"
    props = {
        "Name": {"title": _title(title or content[:60] or "Nota")},
        "Body": {"rich_text": _rich(content[:1900])},
        "Type": {"select": {"name": nt}},
    }
    if project:
        sid = _find_project_id(project)
        if sid:
            props["Project"] = {"relation": [{"id": sid}]}
    if tags:
        valid = [{"name": t} for t in tags if t in NOTE_TAGS]
        if valid:
            props["Tags"] = {"multi_select": valid}
    _link_inbox(props)
    page = notion.pages.create(parent={"database_id": NOTES_DB},
                               properties=props)
    _record_write("Idea" if nt == "Idea" else "Note")
    return {"ok": True, "note_id": page["id"],
            "title": title or content[:60]}


# ---------- Diagrams ----------

def create_diagram(title: str, mermaid_code: str,
                   description: Optional[str] = None,
                   project: Optional[str] = None) -> dict:
    """Crea una nota tipo Reference con un bloque de codigo Mermaid.

    La vieja sub-pagina 'Diagramas' fue eliminada; ahora vive en NOTES.
    """
    if not NOTES_DB:
        return {"error": "NOTES_DB_ID no configurado"}
    props = {
        "Name": {"title": _title(title)},
        "Type": {"select": {"name": "Reference"}},
    }
    if description:
        props["Body"] = {"rich_text": _rich(description[:1900])}
    if project:
        sid = _find_project_id(project)
        if sid:
            props["Project"] = {"relation": [{"id": sid}]}
    _link_inbox(props)

    children = []
    if description:
        children.append({"type": "paragraph",
                         "paragraph": {"rich_text": _rich(description)}})
    children.append({"type": "code",
                     "code": {"rich_text": _rich(mermaid_code),
                              "language": "mermaid"}})

    page = notion.pages.create(parent={"database_id": NOTES_DB},
                               properties=props, children=children)
    _record_write("Note")
    return {"ok": True, "note_id": page["id"], "title": title,
            "url": page.get("url")}


# ---------- Expenses ----------

def add_expense(name: str, amount: Optional[float] = None,
                category: Optional[str] = None, method: Optional[str] = None,
                date: Optional[str] = None, notes: Optional[str] = None) -> dict:
    if not EXPENSES_DB:
        return {"error": "EXPENSES_DB_ID no configurado"}
    props = {
        "Name": {"title": _title(name)},
        "Source": {"select": {"name": "WhatsApp"}},
    }
    if amount is not None:
        props["Amount"] = {"number": float(amount)}
    c = _sel(category, EXPENSE_CATEGORIES)
    if c:
        props["Category"] = c
    m = _sel(method, EXPENSE_METHODS)
    if m:
        props["Method"] = m
    d = _parse_date(date) if date else datetime.now().date().isoformat()
    if d:
        props["Date"] = {"date": {"start": d}}
    if notes:
        props["Notes"] = {"rich_text": _rich(notes)}
    _link_inbox(props)
    page = notion.pages.create(parent={"database_id": EXPENSES_DB},
                               properties=props)
    _record_write("Expense")
    return {"ok": True, "expense_id": page["id"], "name": name,
            "amount": amount}


# ---------- Meals ----------

def add_meal(name: str, meal_type: Optional[str] = None,
             date: Optional[str] = None, ingredients: Optional[str] = None,
             rating: Optional[float] = None) -> dict:
    if not MEALS_DB:
        return {"error": "MEALS_DB_ID no configurado"}
    props = {
        "Name": {"title": _title(name)},
        "Source": {"select": {"name": "WhatsApp"}},
    }
    mt = _sel(meal_type, MEAL_TYPES)
    if mt:
        props["Meal type"] = mt
    d = _parse_date(date) if date else datetime.now().date().isoformat()
    if d:
        props["Date"] = {"date": {"start": d}}
    if ingredients:
        props["Ingredients"] = {"rich_text": _rich(ingredients)}
    if rating is not None:
        props["Rating"] = {"number": float(rating)}
    _link_inbox(props)
    page = notion.pages.create(parent={"database_id": MEALS_DB},
                               properties=props)
    _record_write("Meal")
    return {"ok": True, "meal_id": page["id"], "name": name}


# ---------- Habits ----------

def log_habit(habit_name: str, status: str = "Done",
              date: Optional[str] = None) -> dict:
    if not HABITLOG_DB:
        return {"error": "HABITLOG_DB_ID no configurado"}
    habit = _find_habit(habit_name)
    if not habit:
        return {"error": f"no encontre el habito '{habit_name}'. "
                         f"Activos: {list_habits()['habits']}"}
    st = status if status in HABIT_STATUS else "Done"
    d = _parse_date(date) if date else datetime.now().date().isoformat()
    props = {
        "Name": {"title": _title(f"{habit['name']} — {d}")},
        "Habit": {"relation": [{"id": habit["id"]}]},
        "Status": {"select": {"name": st}},
        "Source": {"select": {"name": "WhatsApp"}},
    }
    if d:
        props["Date"] = {"date": {"start": d}}
    _link_inbox(props)
    page = notion.pages.create(parent={"database_id": HABITLOG_DB},
                               properties=props)
    _record_write("Habit")
    return {"ok": True, "habit_log_id": page["id"], "habit": habit["name"],
            "status": st}


# ---------- Context helper ----------

def today_context() -> str:
    """Para inyectar en el system prompt."""
    now = datetime.now()
    dias = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado",
            "domingo"]
    return f"Hoy es {dias[now.weekday()]} {now.strftime('%Y-%m-%d')}."


# ---------- Diagnostics ----------

def _read_probe(db_id: str):
    """Probe de lectura sobre una DB. Devuelve 'ok' o el detalle del error."""
    if not db_id:
        return "not_configured"
    try:
        notion.databases.query(database_id=db_id, page_size=1)
        return "ok"
    except APIResponseError as e:
        return {"status": e.status, "code": e.code, "message": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def diagnostics() -> dict:
    """Solo probes de LECTURA (no escribe) para diagnosticar permisos."""
    return {
        "token_set": bool(config.NOTION_TOKEN),
        "projects_db_read": _read_probe(PROJECTS_DB),
        "tasks_db_read": _read_probe(TASKS_DB),
        "events_db_read": _read_probe(EVENTS_DB),
        "notes_db_read": _read_probe(NOTES_DB),
        "expenses_db_read": _read_probe(EXPENSES_DB),
        "meals_db_read": _read_probe(MEALS_DB),
        "habits_db_read": _read_probe(HABITS_DB),
        "habitlog_db_read": _read_probe(HABITLOG_DB),
        "inbox_db_read": _read_probe(INBOX_DB),
    }
