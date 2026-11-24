"""Open links in the user's default browser (no Playwright involved)."""

from __future__ import annotations

import webbrowser
from urllib.parse import urlparse

from ..runtime.context import MacroError
from ..runtime.control import esperar, registrar


def abrir_link(url: str, *, esperar_segundos: float = 0) -> bool:
    """Abre una URL HTTP/HTTPS en el navegador predeterminado."""
    if not isinstance(url, str):
        raise ValueError("url debe ser texto")
    parsed = urlparse(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or any(ord(c) < 32 for c in url)
    ):
        raise ValueError("Solo se permiten URLs HTTP/HTTPS sin credenciales embebidas")
    registrar(f"Abriendo enlace en el navegador predeterminado: {parsed.scheme}://{parsed.hostname}")
    opened = webbrowser.open(url, new=2)
    if not opened:
        raise MacroError("El sistema no pudo solicitar la apertura del navegador")
    esperar(esperar_segundos)
    return opened
