"""Envio de mensajes outbound por la REST API de Twilio.

Lo usa el worker async (Fase H) para responder despues de un plan
pesado. NUNCA envia a destinos arbitrarios: solo al MY_WHATSAPP autorizado
(o al `to` que recibio el mensaje original, que es el mismo numero).

Si falta cualquier credencial, `send()` registra warning y devuelve
False. NO crashea — el resultado del plan ya queda registrado en la
tabla `agent_runs` para que el usuario pueda recuperarlo cuando
configure Twilio outbound.
"""
import logging
from typing import Optional

import config

log = logging.getLogger("wpp.outbound")


def is_configured() -> bool:
    """True si tenemos lo minimo para mandar un WhatsApp."""
    return bool(
        config.TWILIO_ACCOUNT_SID
        and config.TWILIO_AUTH_TOKEN
        and config.TWILIO_FROM_WHATSAPP
    )


def send(to: str, body: str, *, client=None) -> bool:
    """Manda un WhatsApp `body` al destinatario `to`.

    - `to` debe ser igual a MY_WHATSAPP (anti-fuga). Si no, no se manda.
    - Si falta config Twilio outbound, no manda; registra y devuelve False.
    - `client` es inyectable para tests (twilio.rest.Client mock).
    """
    if not body:
        log.warning("outbound: body vacio, skip")
        return False

    # Anti-fuga: solo respondemos al numero autorizado.
    if not config.MY_WHATSAPP:
        log.warning("outbound: MY_WHATSAPP no configurado, skip")
        return False
    if _normalize(to) != _normalize(config.MY_WHATSAPP):
        log.warning("outbound: destinatario distinto al autorizado, skip "
                    "(to=%s)", _mask(to))
        return False

    if not is_configured():
        log.warning("outbound: TWILIO_ACCOUNT_SID / TWILIO_FROM_WHATSAPP / "
                    "TWILIO_AUTH_TOKEN faltan — resultado quedó en DB, no "
                    "se envia WhatsApp")
        return False

    try:
        if client is None:
            from twilio.rest import Client
            client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        msg = client.messages.create(
            from_=config.TWILIO_FROM_WHATSAPP,
            to=to,
            body=body[:1500],  # tope WhatsApp / TwiML; cortamos por las dudas
        )
        log.info("outbound enviado sid=%s to=%s", getattr(msg, "sid", "?"),
                 _mask(to))
        return True
    except Exception as e:
        log.warning("outbound: fallo Twilio (%s): %s", type(e).__name__, e)
        return False


def _normalize(s: str) -> str:
    return (s or "").strip().replace(" ", "").lower()


def _mask(s: str) -> str:
    s = (s or "").strip()
    return f"{s[:6]}…{s[-2:]}" if len(s) > 6 else s
