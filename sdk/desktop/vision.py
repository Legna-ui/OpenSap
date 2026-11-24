"""Image matching on screen: find, wait, wait-until-gone and wait-for-any."""

from __future__ import annotations

import time
from typing import Any

from ..data.files import recurso
from ..runtime.context import ImageTimeout, MacroError, Match, _context, _number
from ..runtime.control import esperar, registrar
from . import screen


def _locate(image: str):
    context = _context()
    path = context.asset(image)
    settings = context.settings
    options: dict[str, Any] = {"grayscale": settings.grayscale}
    if settings.region is not None:
        options["region"] = settings.region
    if settings.confidence is not None:
        try:
            import cv2  # noqa: F401
        except ImportError as error:
            raise MacroError("Instala opencv-python en el backend o usa configurar(confianza=None).") from error
        options["confidence"] = settings.confidence
    gui = screen._gui()
    gui.failSafeCheck()
    try:
        box = gui.locateOnScreen(str(path), **options)
    except gui.ImageNotFoundException:  # type: ignore[attr-defined]
        return None
    return Match(int(box.left), int(box.top), int(box.width), int(box.height)) if box else None


def _timeout(value: float | None) -> float:
    if value is None:
        return _context().settings.timeout
    return _number(value, "tiempo_maximo", 0, 3600)


def buscar_imagen(imagen: str) -> Any:
    """Una sola busqueda; devuelve None si no aparece, no oculta otros errores."""
    return _locate(imagen)


def existe_imagen(imagen: str) -> bool:
    return buscar_imagen(imagen) is not None


def esperar_imagen(imagen: str, *, tiempo_maximo: float | None = None):
    """Espera hasta que la imagen aparezca y devuelve su posicion (Match)."""
    context = _context()
    timeout = _timeout(tiempo_maximo)
    deadline = time.monotonic() + timeout
    registrar(f"Esperando imagen: {imagen} ({timeout}s)")
    last_report = time.monotonic()
    while True:
        match = _locate(imagen)
        if match:
            registrar(f"Imagen encontrada: {imagen} en ({match.x}, {match.y})")
            return match
        now = time.monotonic()
        if now >= deadline:
            screen._diagnostic("imagen-no-encontrada")
            raise ImageTimeout(f"No aparecio {imagen} en {timeout}s")
        if now - last_report >= 3:
            registrar(f"Sigue esperando: {imagen}")
            last_report = now
        esperar(min(context.settings.poll, deadline - now))


def esperar_desaparecer(imagen: str, *, tiempo_maximo: float | None = None) -> bool:
    settings = _context().settings
    timeout = _timeout(tiempo_maximo)
    deadline = time.monotonic() + timeout
    while True:
        if _locate(imagen) is None:
            registrar(f"Imagen ausente: {imagen}")
            return True
        if time.monotonic() >= deadline:
            screen._diagnostic("imagen-no-desaparece")
            raise ImageTimeout(f"La imagen {imagen} no desaparecio en {timeout}s")
        esperar(min(settings.poll, max(0, deadline - time.monotonic())))


def esperar_cualquiera(imagenes: list[str], *, tiempo_maximo: float | None = None):
    """Espera la primera de varias imagenes; devuelve (nombre, Match)."""
    if not imagenes or len(imagenes) > 30:
        raise ValueError("Proporciona entre 1 y 30 imagenes")
    for image in imagenes:
        recurso(image)
    settings = _context().settings
    timeout = _timeout(tiempo_maximo)
    deadline = time.monotonic() + timeout
    while True:
        for image in imagenes:
            match = _locate(image)
            if match:
                return image, match
        if time.monotonic() >= deadline:
            screen._diagnostic("ninguna-imagen")
            raise ImageTimeout("No aparecio ninguna de las imagenes esperadas")
        esperar(min(settings.poll, max(0, deadline - time.monotonic())))


def asegurar_imagen(imagen: str, *, tiempo_maximo: float | None = None):
    """Comprueba que la pantalla esta en el estado esperado (falla si no aparece)."""
    return esperar_imagen(imagen, tiempo_maximo=tiempo_maximo)
