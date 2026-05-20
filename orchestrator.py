"""Orchestrator central (Fase F).

Toma la `RouteDecision` del router y produce un `ActionPlan` declarativo.
El orquestador NO ejecuta; main.py es quien dispatch-ea (rule → execute_tool,
agent → run_agent). Asi el plan es trazable y persistible.

Confirmaciones:
- needs_confirmation=True → main.py crea un PendingConfirmation con el plan
  serializado como payload. Responde por WhatsApp pidiendo "1"/"cancelar".
- Si el siguiente mensaje del usuario matchea is_confirmation_reply, main
  saca el plan y lo ejecuta (o lo descarta).

Requiere `SESSIONS_BACKEND=postgres` para la persistencia de la
confirmacion. Con backend=file el orquestador igual planifica, pero el
caller (main.py) ignora needs_confirmation y cae al flujo Sonnet de
siempre — eso lo documentamos como deuda hasta que se use postgres.
"""
from dataclasses import asdict, dataclass, field
from typing import Optional

import config
import router as wpp_router


# Fase G: mapeo intent → nombre de agente especializado. Si un intent no
# matchea aca, cae al `run_agent` legacy (route = haiku_agent/sonnet_agent).
_INTENT_TO_AGENT = {
    # captura (Haiku)
    "add_expense": "capture_agent",
    "add_meal": "capture_agent",
    "log_habit": "capture_agent",
    "add_note": "capture_agent",
    "create_task": "capture_agent",
    "create_event": "capture_agent",
    "query_tasks": "capture_agent",
    "query_events": "capture_agent",
    # planificacion (Sonnet, requiere confirmacion por bulk)
    "plan": "planner_agent",
    "reorganize": "planner_agent",
    "reorganizar": "planner_agent",
    "bulk_create": "planner_agent",
    # redaccion (Sonnet)
    "write": "writer_agent",
    "redactar": "writer_agent",
    # investigacion (Haiku stub)
    "research": "research_agent",
    "investigar": "research_agent",
}


def select_agent(intent: str) -> str | None:
    """Devuelve el nombre del agente especializado para un intent, o None
    si conviene caer al run_agent legacy."""
    return _INTENT_TO_AGENT.get((intent or "").strip())


# ---------- Constantes de clasificacion ----------

# Intents que el agente puede ejecutar sin pedir confirmacion.
SAFE_AGENT_INTENTS = {
    "add_expense", "add_meal", "log_habit", "add_note",
    "create_task", "create_event",
    "query_tasks", "query_events", "query_notes",
}

# Intents que SIEMPRE piden confirmacion por ser bulk/planificacion.
BULK_INTENTS = {"plan", "reorganize", "reorganizar", "bulk_create"}

# Intents marcados explicitamente como destructivos por el clasificador.
DESTRUCTIVE_INTENTS = {"destructive", "delete", "delete_all"}

# Palabras del usuario que cuentan como "si" o "no" a una confirmacion.
CONFIRM_AFFIRMATIVE = {"1", "si", "sí", "yes", "y", "ok", "dale",
                      "confirmo", "confirm", "confirmar", "go"}
CONFIRM_NEGATIVE = {"2", "0", "no", "n", "cancelar", "cancel", "cancelo",
                    "abortar", "abort", "stop"}


# Texto generico al bloquear una accion unsafe. Deliberadamente vago:
# no confirma al atacante de que detectamos un intento de prompt
# injection ni que tipo de heuristica disparo.
BLOCKED_UNSAFE_REPLY = (
    "⚠ No puedo ejecutar eso. Si era un pedido genuino, reformulalo "
    "describiendo la accion concreta que queres."
)

# Texto cuando una accion requiere confirmacion pero el backend no
# puede persistirla (file). Se le dice al usuario que esa accion no se
# puede ejecutar sin postgres; no se cae a Sonnet automaticamente.
NEEDS_POSTGRES_REPLY = (
    "⚠ Esta accion requiere confirmacion y este deploy esta corriendo "
    "con backend=file (sin Postgres). No la puedo ejecutar de forma "
    "segura. Si queres seguir, dividi el pedido en acciones simples o "
    "habilita SESSIONS_BACKEND=postgres."
)


# ---------- ActionPlan ----------

@dataclass
class ActionPlan:
    """Decision declarativa de que hacer con un mensaje.

    Lo emite el orquestador y lo consume main.py. Tambien se serializa a
    JSON cuando se guarda como payload de un PendingConfirmation.
    """
    intent: str
    route: str                       # rule | haiku_agent | sonnet_agent
    model: Optional[str] = None      # modelo a usar cuando route es *_agent
    tools: list = field(default_factory=list)  # tools previstas (puede ser []
                                                # cuando lo decide el agente)
    payload: dict = field(default_factory=dict)
    needs_confirmation: bool = False
    confirmation_reason: str = ""
    safety_level: str = "safe"       # safe | destructive | bulk | unsafe
    async_required: bool = False     # reservado Fase H (workers)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "ActionPlan":
        # filtro campos desconocidos por compat si cambia el esquema
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


# ---------- Planning ----------

def plan_from_decision(decision, user_text: str) -> ActionPlan:
    """Convierte una RouteDecision del router en un ActionPlan."""
    intent = (decision.intent or "unknown").strip()

    # Caso 1: regla deterministica del router. Por construccion es safe.
    if decision.route == wpp_router.ROUTE_RULE:
        return ActionPlan(
            intent=intent,
            route="rule",
            tools=[decision.tool] if decision.tool else [],
            payload={"tool": decision.tool, "args": decision.tool_args or {}},
            safety_level="safe",
        )

    # Caso 2: agente. Clasificacion de safety.
    safety = "safe"
    needs_conf = False
    reason = ""

    if intent == "prompt_injection":
        safety = "unsafe"
        needs_conf = True
        reason = "el mensaje parece un intento de cambiar mis instrucciones; confirmá si querés que lo ejecute igual"
    elif decision.destructive or intent in DESTRUCTIVE_INTENTS:
        safety = "destructive"
        needs_conf = True
        reason = f"accion destructiva ({intent or 'sin intent'}); va a borrar/modificar datos"
    elif intent in BULK_INTENTS:
        safety = "bulk"
        needs_conf = True
        reason = f"accion compleja ({intent}); puede crear/mover varios registros"

    # Eleccion de agente especializado (Fase G). Si hay match, override la
    # route. Si no, mantenemos haiku_agent/sonnet_agent (fallback legacy).
    agent_name = select_agent(intent)
    if agent_name:
        route_str = agent_name
        # Ajustamos el modelo al default del agente solo si el router NO
        # tenia preferencia explicita. Pre-seteamos None y que _execute_plan
        # use el default del agente.
        model = decision.model
        if agent_name in {"capture_agent", "research_agent", "critic_agent"}:
            # Estos agentes son Haiku por default — si el router ya eligio
            # Sonnet, igualmente preferimos el barato.
            model = config.ROUTER_MODEL
        elif agent_name in {"planner_agent", "writer_agent"}:
            model = config.ORCHESTRATOR_MODEL
    else:
        route_str = ("haiku_agent" if decision.route == wpp_router.ROUTE_HAIKU
                     else "sonnet_agent")
        model = decision.model

    return ActionPlan(
        intent=intent,
        route=route_str,
        model=model,
        tools=[],
        payload={
            "user_text": user_text,
            "router_reason": decision.reason,
            "confidence": decision.confidence,
            "destructive": decision.destructive,
        },
        needs_confirmation=needs_conf,
        confirmation_reason=reason,
        safety_level=safety,
    )


# ---------- Confirmacion ----------

def is_confirmation_reply(body: str) -> Optional[bool]:
    """True = confirmar, False = cancelar, None = no parece respuesta.

    Mantenemos el set chico para evitar falsos positivos. Si el mensaje
    tiene cualquier otro texto (mas largo, con espacios, etc.) lo tratamos
    como mensaje normal.
    """
    t = (body or "").strip().lower()
    if not t:
        return None
    if t in CONFIRM_AFFIRMATIVE:
        return True
    if t in CONFIRM_NEGATIVE:
        return False
    return None


def confirmation_prompt(plan: ActionPlan) -> str:
    """Mensaje WhatsApp que pide confirmacion."""
    reason = plan.confirmation_reason or "Necesito confirmar"
    ttl = config.CONFIRMATION_TTL_MINUTES
    return (f"⚠ {reason}.\n"
            f"Respondé '1' para confirmar o 'cancelar' para abortar "
            f"(expira en {ttl} min).")
