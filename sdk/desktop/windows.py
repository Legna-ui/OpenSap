"""Top-level window helpers (Windows only): list and bring a window to front."""

from __future__ import annotations

import sys
import time

from ..runtime.context import MacroError, _number
from ..runtime.control import esperar, registrar
from . import screen

_SW_RESTORE = 9
_SW_MAXIMIZE = 3
_VK_MENU = 0x12
_KEYEVENTF_KEYUP = 0x2


class VentanaNoEncontrada(MacroError, TimeoutError):
    pass


def _user32():
    if sys.platform != "win32":
        raise MacroError("Las funciones de ventanas solo estan disponibles en Windows")
    import ctypes

    return ctypes, ctypes.windll.user32


def _visible_windows() -> list[tuple[int, str]]:
    ctypes, user32 = _user32()
    from ctypes import wintypes

    found: list[tuple[int, str]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _data):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if 0 < length <= 500:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = buffer.value.strip()
                if title:
                    found.append((int(hwnd), title))
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return found


def listar_ventanas() -> list[str]:
    """Titulos de las ventanas visibles, en orden Z (la de delante primero)."""
    return [title for _hwnd, title in _visible_windows()]


def enfocar_ventana(
    titulo: str,
    *,
    tiempo_maximo: float = 10,
    exacto: bool = False,
    maximizar: bool = False,
) -> str:
    """Trae al frente la primera ventana cuyo titulo contiene (o es) `titulo`.

    Espera hasta `tiempo_maximo` segundos a que aparezca y devuelve el titulo real.
    """
    if not isinstance(titulo, str) or not titulo.strip() or len(titulo) > 300:
        raise ValueError("titulo debe ser un texto de 1 a 300 caracteres")
    timeout = _number(tiempo_maximo, "tiempo_maximo", 0, 3600)
    wanted = titulo.strip().casefold()
    _ctypes, user32 = _user32()
    deadline = time.monotonic() + timeout
    while True:
        for hwnd, title in _visible_windows():
            current = title.casefold()
            if (current == wanted) if exacto else (wanted in current):
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, _SW_RESTORE)
                if maximizar:
                    user32.ShowWindow(hwnd, _SW_MAXIMIZE)
                # Windows only lets the foreground process change focus; a synthetic
                # Alt press makes this process eligible (documented workaround).
                user32.keybd_event(_VK_MENU, 0, 0, 0)
                user32.SetForegroundWindow(hwnd)
                user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
                registrar(f"Ventana enfocada: {title}")
                esperar(0.3)
                return title
        if time.monotonic() >= deadline:
            screen._diagnostic("ventana-no-encontrada")
            raise VentanaNoEncontrada(f"No aparecio una ventana con titulo '{titulo}' en {timeout}s")
        esperar(min(0.5, max(0, deadline - time.monotonic())))
