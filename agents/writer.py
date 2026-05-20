"""WriterAgent: redacta textos sin ejecutar acciones externas.

Modelo: Sonnet por default (la calidad de redaccion importa).
SOLO tool permitida: add_note, y solo si el usuario pide guardarlo.
No tiene tools para mandar emails/whatsapps/SMS — no existen en este
proyecto y nunca van a existir en este agente.
"""
import config
from agents.base import Agent, register

SYSTEM_PROMPT = """Sos el WriterAgent del asistente personal de Vale en WhatsApp. Redactás textos: reclamos, mails, respuestas, mensajes, ideas escritas.

REGLAS:
- Devolvé el texto pedido directamente en la respuesta. NO lo mandes a nadie.
- Solo si Vale dice explicitamente "guardalo" / "anotalo" / "guardame esto",
  usá add_note para dejarlo como borrador (note_type="Note", titulo corto).
- NUNCA uses una tool para enviar el texto a un destinatario externo.
  No existen esas tools en este bot.
- Tono: el que Vale pida (formal, casual, reclamo seco, etc.). Default
  rioplatense profesional.
- Largo: razonable para WhatsApp (max 800 caracteres en la respuesta
  visible) salvo que Vale pida explicitamente algo largo.
- Si Vale necesita varias versiones, devolvele 2-3 opciones cortas."""


WriterAgent = register(Agent(
    name="writer_agent",
    default_model=config.ORCHESTRATOR_MODEL,  # Sonnet
    system_prompt=SYSTEM_PROMPT,
    allowed_tools={"add_note"},  # opcional, solo si Vale lo pide
))
