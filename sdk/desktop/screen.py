"""Screen access: the PyAutoGUI handle, screenshots and error diagnostics.

Other desktop modules call `screen._gui()` through the module attribute so a
single patch of `app.sdk.desktop.screen._gui` replaces PyAutoGUI everywhere.
"""

from __future__ import annotations

import time
from typing import Any

from ..runtime.context import MacroContext, _context
from ..runtime.control import registrar


def _gui():
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = _context().settings.pause
    return pyautogui


def _capture(context: MacroContext, name: str, region=None) -> Any:
    import pyautogui

    path = context.artifact(name)
    if path.suffix.lower() != ".png":
        raise ValueError("Las capturas deben usar extension .png")
    pyautogui.screenshot(region=region).save(path)
    context.log(f"Captura: {path.relative_to(context.output_dir)}")
    return path


def captura(nombre: str = "captura.png", *, region=None) -> Any:
    """Guarda una captura PNG (pantalla principal o region) como artefacto."""
    return _capture(_context(), nombre, region)


def _diagnostic(name: str) -> None:
    if _context().settings.screenshot_on_error:
        try:
            captura(f"diagnostico/{name}-{time.time_ns()}.png")
        except Exception as error:
            registrar(f"No se pudo guardar diagnostico: {error}")
