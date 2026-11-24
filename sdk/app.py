"""Apps: web UIs that Macro Studio hosts (see `app` in manifest.json).

An app is a local web server. Macro Studio starts it on a free port, waits until it answers and
shows it inside the studio, without time limit. The same code also runs on its own
(`python main.py` / run_local.py): there the port falls back to a free one and the browser opens.
"""

from __future__ import annotations

import os
import socket
import threading
import webbrowser

HOST = "127.0.0.1"
_standalone_port: int | None = None


def en_macro_studio() -> bool:
    """True cuando Macro Studio es quien ejecuta la app (puerto asignado, sin abrir el navegador)."""
    return bool(os.environ.get("MACRO_APP_PORT"))


def app_puerto(por_defecto: int | None = None) -> int:
    """Puerto en el que la app debe escuchar: el que asigna Macro Studio o, fuera de él, `por_defecto`/uno libre."""
    global _standalone_port
    assigned = os.environ.get("MACRO_APP_PORT")
    if assigned:
        return int(assigned)
    if por_defecto:
        return int(por_defecto)
    if _standalone_port is None:
        with socket.socket() as probe:
            probe.bind((HOST, 0))
            _standalone_port = probe.getsockname()[1]
    return _standalone_port


def app_url() -> str:
    """URL local de la app (http://127.0.0.1:<puerto>).

    Returns:
        La dirección en la que Macro Studio (o tú, fuera de él) abre la app.
    """
    return f"http://{HOST}:{app_puerto()}"


def servir_app(asgi_app, *, abrir_navegador: bool = True, **opciones) -> None:
    """Sirve una app ASGI (FastAPI, Starlette...) en el puerto de la app y bloquea hasta que se detenga.

    Dentro de Macro Studio no abre el navegador (la vista está en el estudio). Fuera, lo abre.
    """
    import uvicorn

    port = app_puerto()
    if abrir_navegador and not en_macro_studio():
        threading.Timer(1.0, lambda: webbrowser.open(app_url())).start()
    opciones.setdefault("log_config", None)
    opciones.setdefault("access_log", False)
    uvicorn.run(asgi_app, host=HOST, port=port, **opciones)
