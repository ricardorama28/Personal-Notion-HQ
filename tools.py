"""
Definiciones de tools para Claude + dispatcher hacia notion_ops.
"""
import logging

from notion_client.errors import APIResponseError

import notion_ops as ops

log = logging.getLogger("wpp")

TOOLS = [
    {
        "name": "list_projects",
        "description": "Lista los proyectos disponibles en Notion. Llamala primero si no estas seguro de los nombres exactos de los proyectos.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "list_habits",
        "description": "Lista los habitos activos. Llamala antes de loguear un habito para usar el nombre exacto.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "create_task",
        "description": "Crea una tarea o recordatorio en la DB Tasks. Usa task_type='Reminder' para recordatorios ('recordame ...').",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nombre/titulo de la tarea"},
                "due": {"type": "string", "description": "Fecha de vencimiento. Acepta formato natural (mañana, 20 de mayo, viernes) o YYYY-MM-DD"},
                "priority": {"type": "string", "enum": ["Low", "Med", "High"]},
                "project": {"type": "string", "description": "Nombre del proyecto si aplica"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string", "description": "Notas adicionales"},
                "task_type": {"type": "string", "enum": ["Task", "Reminder"], "description": "Task (default) o Reminder"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "update_task",
        "description": "Actualiza una tarea existente. Necesitas el task_id (lo obtenes de query_tasks).",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {"type": "string", "enum": ["To Do", "Doing", "Done"]},
                "due": {"type": "string"},
                "priority": {"type": "string", "enum": ["Low", "Med", "High"]}
            },
            "required": ["task_id"]
        }
    },
    {
        "name": "query_tasks",
        "description": "Busca tareas con filtros opcionales. Sin filtros devuelve las proximas 20.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["To Do", "Doing", "Done"]},
                "project": {"type": "string"},
                "due_before": {"type": "string", "description": "Acepta natural o YYYY-MM-DD"},
                "due_after": {"type": "string"},
                "limit": {"type": "integer", "default": 20}
            }
        }
    },
    {
        "name": "create_event",
        "description": "Crea un evento en el calendario (clase, parcial, turno, entrega, personal).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "date": {"type": "string", "description": "Fecha del evento"},
                "event_type": {"type": "string", "enum": ["Class", "Exam", "Appointment", "Deadline", "Personal"]},
                "project": {"type": "string", "description": "Proyecto si aplica"}
            },
            "required": ["name", "date"]
        }
    },
    {
        "name": "query_events",
        "description": "Busca eventos del calendario. Sin filtros devuelve los proximos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "project": {"type": "string"},
                "limit": {"type": "integer", "default": 20}
            }
        }
    },
    {
        "name": "add_note",
        "description": "Crea una nota en la DB Notes. Usala para apuntes de facultad, ideas, referencias.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Contenido de la nota"},
                "title": {"type": "string", "description": "Opcional. Si no se pasa, se usa el inicio del contenido."},
                "note_type": {"type": "string", "enum": ["Note", "Idea", "Reference", "ClassNote"], "description": "Default Note. ClassNote para apuntes de clase, Idea para ideas sueltas."},
                "project": {"type": "string", "description": "Proyecto si aplica"},
                "tags": {"type": "array", "items": {"type": "string", "enum": ["Estudio", "Tech", "Personal"]}}
            },
            "required": ["content"]
        }
    },
    {
        "name": "create_diagram",
        "description": "Crea una nota tipo Reference con un diagrama Mermaid. El codigo debe ser Mermaid valido (flowchart, sequenceDiagram, stateDiagram, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titulo del diagrama"},
                "mermaid_code": {"type": "string", "description": "Codigo Mermaid. Ej: 'graph TD\\n A-->B'"},
                "description": {"type": "string", "description": "Opcional: descripcion en lenguaje natural sobre que representa"},
                "project": {"type": "string", "description": "Opcional: proyecto si aplica"}
            },
            "required": ["title", "mermaid_code"]
        }
    },
    {
        "name": "add_expense",
        "description": "Registra un gasto en la DB Expenses. Ej: 'gasté 5000 en super con débito'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Descripcion corta del gasto"},
                "amount": {"type": "number", "description": "Monto en pesos"},
                "category": {"type": "string", "enum": ["Supermercado", "Comida", "Transporte", "Servicios", "Salud", "Auto", "Casa", "Ocio", "Otros"]},
                "method": {"type": "string", "enum": ["Efectivo", "Débito", "Crédito", "Transferencia"]},
                "date": {"type": "string", "description": "Opcional. Default hoy."},
                "notes": {"type": "string"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "add_meal",
        "description": "Registra una comida en la DB Meals. Ej: 'comí milanesa con puré al almuerzo'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Que comio"},
                "meal_type": {"type": "string", "enum": ["Desayuno", "Almuerzo", "Merienda", "Cena", "Snack"]},
                "date": {"type": "string", "description": "Opcional. Default hoy."},
                "ingredients": {"type": "string"},
                "rating": {"type": "number", "description": "Opcional, 1-5"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "log_habit",
        "description": "Registra el cumplimiento de un habito en HABIT LOG. Ej: 'hice ejercicio'. Llama list_habits si no sabes el nombre exacto.",
        "input_schema": {
            "type": "object",
            "properties": {
                "habit_name": {"type": "string", "description": "Nombre del habito (match aproximado contra los activos)"},
                "status": {"type": "string", "enum": ["Done", "Skipped"], "description": "Default Done"},
                "date": {"type": "string", "description": "Opcional. Default hoy."}
            },
            "required": ["habit_name"]
        }
    },
]


DISPATCH = {
    "list_projects": ops.list_projects,
    "list_habits": ops.list_habits,
    "create_task": ops.create_task,
    "update_task": ops.update_task,
    "query_tasks": ops.query_tasks,
    "create_event": ops.create_event,
    "query_events": ops.query_events,
    "add_note": ops.add_note,
    "create_diagram": ops.create_diagram,
    "add_expense": ops.add_expense,
    "add_meal": ops.add_meal,
    "log_habit": ops.log_habit,
}


def execute_tool(name: str, args: dict) -> dict:
    """Ejecuta una tool con manejo de error."""
    fn = DISPATCH.get(name)
    if not fn:
        return {"error": f"tool desconocida: {name}"}
    try:
        return fn(**args)
    except APIResponseError as e:
        log.error(
            "Notion API error en tool=%s status=%s code=%s body=%s",
            name, e.status, e.code, getattr(e, "body", None),
        )
        return {"error": f"NotionAPIError status={e.status} code={e.code}: {e}"}
    except Exception as e:
        log.exception("error ejecutando tool=%s", name)
        return {"error": f"{type(e).__name__}: {e}"}
