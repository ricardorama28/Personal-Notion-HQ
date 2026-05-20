"""Auth para /admin/*.

Acepta el token via:
  - header `X-Admin-Token` (HTMX / API);
  - cookie `admin_token` (set por /admin/login?token=...);
  - query string `?token=` (solo para el primer login).

Sin token o con token incorrecto → 404 (HTTPException) para no revelar
la existencia de la UI a scanners.
"""
import hmac
from typing import Optional

from fastapi import Cookie, Header, HTTPException, Request

import config


def check_token(provided: str) -> bool:
    expected = config.ADMIN_TOKEN
    if not expected:
        return False
    return hmac.compare_digest(provided or "", expected)


async def require_admin(
    request: Request,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
    admin_token: Optional[str] = Cookie(default=None),
) -> bool:
    """Dependency que ratifica acceso a /admin/. 404 si falla.

    Tambien permite ?token=... como fallback para el primer GET cuando
    no hay cookie todavia (util para abrir desde un bookmark).
    """
    # `?token=` solo se acepta si el flag esta on (default true por compat).
    # En self-hosted/tunel conviene apagarlo para forzar el form POST.
    token_q = (request.query_params.get("token", "")
               if config.ADMIN_LOGIN_QUERY_ENABLED else "")
    candidates = [x_admin_token, admin_token or "", token_q]
    if not config.ADMIN_TOKEN:
        raise HTTPException(status_code=404, detail="not found")
    for c in candidates:
        if c and check_token(c):
            return True
    raise HTTPException(status_code=404, detail="not found")
