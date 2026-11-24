"""Keyboard actions: keys, shortcuts, Unicode typing and image-anchored fills."""

from __future__ import annotations

from ..runtime.context import _number
from ..runtime.control import esperar
from . import mouse, screen


def pulsar(tecla: str, *, veces: int = 1, intervalo: float = 0) -> None:
    gui = screen._gui()
    if (
        tecla not in gui.KEYBOARD_KEYS
        or isinstance(veces, bool)
        or not isinstance(veces, int)
        or not 1 <= veces <= 1000
    ):
        raise ValueError("Tecla invalida o repeticiones fuera de 1..1000")
    _number(intervalo, "intervalo", 0, 10)
    gui.press(tecla, presses=veces, interval=intervalo)


def atajo(*teclas: str) -> None:
    gui = screen._gui()
    if not teclas or len(teclas) > 6 or any(key not in gui.KEYBOARD_KEYS for key in teclas):
        raise ValueError("Atajo de teclado invalido")
    gui.hotkey(*teclas)


def escribir(texto: object, *, intervalo: float = 0, unicode: bool = True) -> None:
    """Escribe texto; con unicode=True lo pega por portapapeles (acentos incluidos)."""
    text = str(texto)
    if len(text) > 100000:
        raise ValueError("Texto demasiado largo")
    _number(intervalo, "intervalo", 0, 5)
    if unicode:
        import pyperclip

        old = pyperclip.paste()
        try:
            pyperclip.copy(text)
            atajo("ctrl", "v")
            esperar(0.3)
        finally:
            pyperclip.copy(old)
    else:
        if not text.isascii():
            raise ValueError("Usa unicode=True para acentos y caracteres no ASCII")
        screen._gui().write(text, interval=intervalo)


def rellenar(imagen: str, texto: object, *, limpiar: bool = True, tiempo_maximo: float | None = None) -> None:
    """Clic en el campo de la imagen, opcionalmente lo vacia y escribe el texto."""
    mouse.clic(imagen, tiempo_maximo=tiempo_maximo)
    if limpiar:
        atajo("ctrl", "a")
    escribir(texto)
