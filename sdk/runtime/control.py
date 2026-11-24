"""Run control: settings, logging, parameters, result, waits, retries and steps."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from .context import ImageTimeout, MacroError, Settings, _context, _number


def configurar(
    *,
    confianza: float | None = 0.9,
    tiempo_maximo: float = 20,
    intervalo: float = 0.4,
    escala_grises: bool = False,
    region: tuple[int, int, int, int] | None = None,
    pausa: float = 0.15,
    captura_en_error: bool = True,
) -> None:
    """Ajusta la búsqueda de imágenes y la pausa entre acciones de escritorio."""
    if confianza is not None:
        _number(confianza, "confianza", 0.5, 0.999)
    if region is not None and (
        len(region) != 4
        or any(isinstance(v, bool) or not isinstance(v, int) for v in region)
        or min(region[:2]) < 0
        or min(region[2:]) < 1
    ):
        raise ValueError("region: (izquierda>=0, arriba>=0, ancho>0, alto>0), enteros")
    _context().settings = Settings(
        confianza,
        _number(tiempo_maximo, "tiempo_maximo", 0, 3600),
        _number(intervalo, "intervalo", 0.05, 10),
        escala_grises,
        region,
        _number(pausa, "pausa", 0, 5),
        captura_en_error,
    )


def registrar(mensaje: object) -> None:
    """Escribe una línea en el log de la ejecución (los secretos se ocultan)."""
    _context().log(mensaje)


def parametro(nombre: str, defecto: Any = None) -> Any:
    """Valor de un parámetro del manifiesto."""
    return _context().parameters.get(nombre, defecto)


def secreto(nombre: str, defecto: str = "") -> str:
    """Lee una variable de entorno/.env y la oculta en los logs."""
    return _context().secret(nombre, defecto)


def resultado(valor: Any) -> None:
    """Guarda el resultado JSON de la ejecución."""
    _context().set_result(valor)


def esperar(segundos: float = 1) -> None:
    """Pausa la macro (también funciona en macros sin escritorio)."""
    seconds = _number(segundos, "segundos", 0, 3600)
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def repetir(
    intentos: int,
    accion: Callable[[], Any],
    *,
    espera: float = 1,
    errores: tuple[type[Exception], ...] = (ImageTimeout,),
) -> Any:
    """Reintenta una acción solo ante los errores indicados."""
    if isinstance(intentos, bool) or not isinstance(intentos, int) or not 1 <= intentos <= 20:
        raise ValueError("intentos debe estar entre 1 y 20")
    _number(espera, "espera", 0, 60)
    for index in range(intentos):
        try:
            return accion()
        except errores:
            if index == intentos - 1:
                raise
            registrar(f"Reintento {index + 2}/{intentos}")
            esperar(espera)
    raise MacroError("Sin intentos")


@contextmanager
def paso(nombre: str):
    """Agrupa acciones en el log con su duración; guarda diagnóstico si fallan."""
    registrar(f"INICIO: {nombre}")
    started = time.monotonic()
    try:
        yield
    except Exception:
        from ..desktop.screen import _diagnostic

        _diagnostic("paso-error")
        registrar(f"ERROR: {nombre}")
        raise
    else:
        registrar(f"FIN: {nombre} ({time.monotonic() - started:.2f}s)")
