"""
Definiciones de tools para Claude + dispatcher hacia notion_ops.
"""
import notion_ops as ops

TOOLS = [
    {
        "name": "list_projects",
        "description": "Lista los proyectos disponibles en Notion. Llamala primero si no estas seguro de los nombres exactos de los proyectos.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "create_task",
        "description": "Crea una tarea nueva en la database Tasks. Si la tarea es de un proyecto, pasalo en 'project'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nombre/titulo de la tarea"},
                "due": {"type": "string", "description": "Fecha de vencimiento. Acepta formato natural (mañana, 20 de mayo, viernes) o YYYY-MM-DD"},
                "priority": {"type": "string", "enum": ["Low", "Med", "High"]},
                "project": {"type": "string", "description": "Nombre del proyecto si aplica"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string", "description": "Notas adicionales"}
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
                "status": {"type": "string", "enum": ["Todo", "Doing", "Done"]},
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
                "status": {"type": "string", "enum": ["Todo", "Doing", "Done"]},
                "project": {"type": "string"},
                "due_before": {"type": "string", "description": "Acepta natural o YYYY-MM-DD"},
                "due_after": {"type": "string"},
                "limit": {"type": "integer", "default": 20}
            }
        }
    },
    {
        "name": "create_event",
        "description": "Crea un evento en el calendario (clase, parcial, etc.)",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "date": {"type": "string", "description": "Fecha del evento"},
                "event_type": {"type": "string", "enum": ["Clase", "Parcial", "Personal", "Otro"]},
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
        "description": "Agrega un apunte a la sub-pagina 'Apuntes' de un proyecto. NUNCA sobreescribe, siempre agrega al final con un heading de fecha.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Proyecto destino"},
                "content": {"type": "string", "description": "Contenido del apunte. Separa parrafos con doble newline."},
                "heading": {"type": "string", "description": "Opcional. Si no se pasa, se usa la fecha de hoy."}
            },
            "required": ["project", "content"]
        }
    },
    {
        "name": "create_diagram",
        "description": "Crea un diagrama de flujo como pagina nueva dentro de 'Diagramas' de un proyecto. El codigo debe ser Mermaid valido (flowchart, sequenceDiagram, stateDiagram, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "title": {"type": "string", "description": "Titulo del diagrama"},
                "mermaid_code": {"type": "string", "description": "Codigo Mermaid. Ej: 'graph TD\\n A-->B'"},
                "description": {"type": "string", "description": "Opcional: descripcion en lenguaje natural sobre que representa"}
            },
            "required": ["project", "title", "mermaid_code"]
        }
    },
]


DISPATCH = {
    "list_projects": ops.list_projects,
    "create_task": ops.create_task,
    "update_task": ops.update_task,
    "query_tasks": ops.query_tasks,
    "create_event": ops.create_event,
    "query_events": ops.query_events,
    "add_note": ops.add_note,
    "create_diagram": ops.create_diagram,
}


def execute_tool(name: str, args: dict) -> dict:
    """Ejecuta una tool con manejo de error."""
    fn = DISPATCH.get(name)
    if not fn:
        return {"error": f"tool desconocida: {name}"}
    try:
        return fn(**args)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
