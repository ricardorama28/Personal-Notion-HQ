"""Clase base de los agentes especializados.

`Agent.run()` corre el loop de tool use con la whitelist propia.
Reutiliza `tools.execute_tool` para ejecutar las herramientas.

El loop es muy similar al `run_agent` legacy de main.py pero:
- filtra TOOLS por whitelist del agente (defensa en profundidad: aunque
  el modelo no las "vea" en el request, validamos al ejecutar);
- registra cost_log con route = nombre del agente.
"""
import json
import logging
from dataclasses import dataclass, field

import config
import cost_log
from tools import TOOLS, execute_tool

log = logging.getLogger("wpp.agents")


@dataclass
class Agent:
    """Metadata + loop de un agente especializado."""
    name: str                       # ej. "capture_agent"
    default_model: str
    system_prompt: str
    allowed_tools: set              # whitelist de nombres de tools
    max_iterations: int = field(default=8)

    # Costo route key para cost_log (e.g. "capture_agent", "planner_agent").
    @property
    def cost_route(self) -> str:
        return self.name

    def tools_schema(self) -> list:
        """Schema de tools a pasarle al modelo: filtrado por whitelist."""
        return [t for t in TOOLS if t["name"] in self.allowed_tools]

    def run(self, history: list, *, anthropic_client, sid: str | None = None,
            intent: str | None = None,
            model_override: str | None = None) -> tuple[str, list, dict]:
        """Loop de tool use con la whitelist y system prompt propios.

        Retorna (reply_text, mensajes_actualizados, run_meta).
        """
        from notion_ops import today_context

        model = model_override or self.default_model
        system = f"{self.system_prompt}\n\n{today_context()}"
        allowed_schema = self.tools_schema()

        total_in = 0
        total_out = 0
        iterations = 0
        tool_invocations: list = []

        try:
            for _ in range(self.max_iterations):
                iterations += 1
                response = anthropic_client.messages.create(
                    model=model,
                    max_tokens=2048,
                    system=system,
                    tools=allowed_schema,
                    messages=history,
                )
                total_in += getattr(response.usage, "input_tokens", 0)
                total_out += getattr(response.usage, "output_tokens", 0)

                history.append({
                    "role": "assistant",
                    "content": [b.model_dump() for b in response.content],
                })

                if response.stop_reason != "tool_use":
                    text = "\n".join(
                        b.text for b in response.content if b.type == "text"
                    )
                    return (
                        text.strip() or "✓",
                        history,
                        {"model": model, "agent": self.name,
                         "input_tokens": total_in, "output_tokens": total_out,
                         "iterations": iterations,
                         "tool_calls": tool_invocations},
                    )

                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    name = block.name
                    # Defense-in-depth: aunque pasamos schema filtrado, si
                    # de algun modo el modelo pide algo fuera de la
                    # whitelist, devolvemos error sin ejecutar.
                    if name not in self.allowed_tools:
                        log.warning("agente %s intento tool fuera de "
                                    "whitelist: %s", self.name, name)
                        result = {"error": f"tool '{name}' no permitida para "
                                           f"agente {self.name}"}
                    else:
                        result = execute_tool(name, block.input)
                    tool_invocations.append({"name": name,
                                             "args": block.input,
                                             "result": result})
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })

                history.append({"role": "user", "content": tool_results})

            return (
                "Llegue al limite de iteraciones, probá reformular.",
                history,
                {"model": model, "agent": self.name,
                 "input_tokens": total_in, "output_tokens": total_out,
                 "iterations": iterations, "tool_calls": tool_invocations},
            )
        finally:
            cost_log.log_event(
                route=self.cost_route, intent=intent, model=model, sid=sid,
                input_tokens=total_in, output_tokens=total_out,
                extra={"iterations": iterations, "agent": self.name},
            )


# Registry global. Cada agente se registra desde su modulo.
AGENT_REGISTRY: dict[str, Agent] = {}


def register(agent: Agent) -> Agent:
    AGENT_REGISTRY[agent.name] = agent
    return agent


def get_agent(name: str) -> Agent | None:
    return AGENT_REGISTRY.get(name)


def AGENT_NAMES() -> set:  # noqa: N802 -- "AGENT_NAMES" como constante
    return set(AGENT_REGISTRY.keys())
