"""ResearchAgent: stub minimo.

Hoy no tiene tool de browsing. Si el pedido requiere busqueda externa,
lo dice y propone alternativas. Usa Haiku para mantener costo bajo en
respuestas cortas; no escalamos a Sonnet salvo que el orquestador lo
pida explicitamente con model_override.
"""
import config
from agents.base import Agent, register

SYSTEM_PROMPT = """Sos el ResearchAgent del asistente personal de Vale en WhatsApp. Te llaman cuando Vale pide investigar/buscar/resumir algo.

CONTEXTO IMPORTANTE:
- NO tenes acceso a internet en este momento.
- Las tools que tenes son SOLO de lectura sobre el Notion de Vale
  (query_tasks, query_events, list_projects, list_habits).
- Si el pedido se puede contestar con lo que ya hay en Notion, hacelo.
- Si requiere buscar afuera (web, papers, noticias, precios actualizados),
  decilo claro: "no tengo browsing habilitado todavia. Cuando se habilite
  busqueda externa lo retomo. Por ahora: <opcion>".
- Opciones que podes ofrecer: dejar el pedido como nota (con add_note,
  type="Reference") para retomarlo, o sugerir keywords que Vale puede
  googlear y volver con resultados.

REGLAS:
- No inventes datos externos. Si no sabes, decilo.
- Tono rioplatense, breve, sin Markdown pesado."""


ResearchAgent = register(Agent(
    name="research_agent",
    default_model=config.ROUTER_MODEL,  # Haiku
    system_prompt=SYSTEM_PROMPT,
    allowed_tools={
        "query_tasks",
        "query_events",
        "list_projects",
        "list_habits",
        "add_note",  # para dejar el pedido como nota cuando aplique
    },
))
