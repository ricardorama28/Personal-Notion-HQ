"""Agentes especializados (Fase G).

Cada agente tiene un system prompt propio, una whitelist de tools y un
modelo default. El orquestador (orchestrator.py) elige al agente segun
el intent/safety del ActionPlan; `_execute_plan` en main.py dispatch-ea
contra `get_agent(name)`. Agentes desconocidos caen al `run_agent` viejo
como fallback.
"""
from agents.base import Agent, AGENT_REGISTRY, get_agent, AGENT_NAMES
from agents.capture import CaptureAgent
from agents.planner import PlannerAgent
from agents.writer import WriterAgent
from agents.research import ResearchAgent
from agents.critic import CriticAgent, review_plan

__all__ = [
    "Agent", "AGENT_REGISTRY", "get_agent", "AGENT_NAMES",
    "CaptureAgent", "PlannerAgent", "WriterAgent", "ResearchAgent",
    "CriticAgent", "review_plan",
]
