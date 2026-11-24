"""Mouse actions anchored to images (or explicit coordinates as a fallback)."""

from __future__ import annotations

from ..runtime.context import ImageTimeout, _number
from ..runtime.control import esperar, registrar
from . import screen, vision


def _click_options(boton: str, veces: int, despues: float) -> None:
    if (
        boton not in ("left", "right", "middle")
        or isinstance(veces, bool)
        or not isinstance(veces, int)
        or not 1 <= veces <= 5
    ):
        raise ValueError("boton invalido o veces fuera de 1..5")
    _number(despues, "despues", 0, 3600)


def clic(
    imagen: str,
    *,
    tiempo_maximo: float | None = None,
    boton: str = "left",
    veces: int = 1,
    desplazamiento: tuple[int, int] = (0, 0),
    despues: float = 0,
):
    """Espera la imagen y hace clic en su centro (mas el desplazamiento)."""
    _click_options(boton, veces, despues)
    if len(desplazamiento) != 2 or any(not isinstance(v, int) for v in desplazamiento):
        raise ValueError("desplazamiento requiere dos enteros")
    match = vision.esperar_imagen(imagen, tiempo_maximo=tiempo_maximo)
    gui = screen._gui()
    x, y = match.x + desplazamiento[0], match.y + desplazamiento[1]
    if not gui.onScreen(x, y):
        raise ValueError("El punto de clic esta fuera de la pantalla principal")
    gui.moveTo(x, y)
    gui.click(clicks=veces, interval=0, button=boton)
    registrar(f"Clic {boton}: {imagen} en ({x}, {y})")
    esperar(despues)
    return match


def clic_en(x: int, y: int, *, boton: str = "left", veces: int = 1, despues: float = 0) -> None:
    """Clic en coordenadas absolutas de la pantalla principal (sin imagen)."""
    _click_options(boton, veces, despues)
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (x, y)):
        raise ValueError("x e y deben ser enteros")
    gui = screen._gui()
    if not gui.onScreen(x, y):
        raise ValueError("El punto de clic esta fuera de la pantalla principal")
    gui.moveTo(x, y, duration=0)
    gui.click(clicks=veces, interval=0, button=boton)
    registrar(f"Clic {boton} en ({x}, {y})")
    esperar(despues)


def doble_clic(imagen: str, **opciones):
    return clic(imagen, veces=2, **opciones)


def clic_derecho(imagen: str, **opciones):
    return clic(imagen, boton="right", **opciones)


def clic_si_aparece(imagen: str, *, tiempo_maximo: float = 2, despues: float = 0) -> bool:
    """Clic opcional: devuelve False si la imagen no aparece a tiempo."""
    try:
        clic(imagen, tiempo_maximo=tiempo_maximo, despues=despues)
        return True
    except ImageTimeout:
        return False


def mover_a_imagen(imagen: str, *, tiempo_maximo: float | None = None):
    match = vision.esperar_imagen(imagen, tiempo_maximo=tiempo_maximo)
    screen._gui().moveTo(match.x, match.y, duration=0)
    return match


def arrastrar_imagen(origen: str, destino: str, *, duracion: float = 1) -> None:
    _number(duracion, "duracion", 0.2, 30)
    source = vision.esperar_imagen(origen)
    target = vision.esperar_imagen(destino)
    gui = screen._gui()
    gui.moveTo(source.x, source.y, duration=0)
    gui.dragTo(target.x, target.y, duration=duracion, button="left")


def desplazar(pasos: int, *, imagen: str | None = None) -> None:
    """Rueda del raton; con imagen, primero se situa encima de ella."""
    if isinstance(pasos, bool) or not isinstance(pasos, int) or not -1000 <= pasos <= 1000:
        raise ValueError("pasos debe ser entero entre -1000 y 1000")
    if imagen:
        mover_a_imagen(imagen)
    screen._gui().scroll(pasos)
