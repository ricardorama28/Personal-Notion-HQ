"""CaptureAgent: extraccion estructurada barata con Haiku.

Para tareas, notas, gastos, comidas, habitos, recordatorios y eventos
simples. Solo tools de creacion (no update masivo, no destructivo).
"""
import config
from agents.base import Agent, register

SYSTEM_PROMPT = """Sos el CaptureAgent del asistente personal de Vale en WhatsApp. Tu unico trabajo es capturar rapido lo que el usuario describe y guardarlo en la herramienta de Notion correcta.

REGLAS:
- Elegi UNA tool y ejecutala. No expliques tu razonamiento.
- Si falta info critica (ej. fecha de un parcial), preguntá UNA cosa en una linea.
- Para info menor (ej. categoria de un gasto), asumi un default razonable y avisá brevemente.
- Confirmá con UNA linea corta tipo "✓ gasto de $450 en super anotado".
- Sin Markdown pesado. Sin listas largas. Tono rioplatense, casual.

QUÉ SÍ ENTRA EN CAPTURA SIMPLE:
- Crear un proyecto nuevo cuando te lo piden directo → create_project.

QUÉ NO HACER:
- No borrar ni modificar tareas/eventos existentes.
- No reorganizar agendas ni planificar la semana.
- No redactar textos largos.
- Series de eventos recurrentes ("todos los lunes hasta X") NO son para vos:
  las maneja el planner. Respondé "no es para mi".
- Si el pedido no encaja con captura simple, respondé "no es para mi" y nada mas."""


CaptureAgent = register(Agent(
    name="capture_agent",
    default_model=config.ROUTER_MODEL,  # Haiku
    system_prompt=SYSTEM_PROMPT,
    allowed_tools={
        "create_task",
        "create_event",
        "add_note",
        "create_diagram",
        "add_expense",
        "add_meal",
        "log_habit",
        "create_project",
        # lecturas mínimas para validar antes de crear
        "list_projects",
        "list_habits",
        "query_tasks",
        "query_events",
    },
))
