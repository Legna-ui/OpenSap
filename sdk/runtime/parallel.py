"""Ejecución concurrente de funciones conservando el contexto del SDK."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from typing import TypeVar


T = TypeVar("T")


def en_paralelo(
    *funciones: Callable[[], T],
    trabajadores: int = 4,
) -> list[T]:
    """Ejecuta funciones sin argumentos y espera sus resultados.

    Los resultados conservan el orden de las funciones recibidas.

    Para pasar argumentos, utiliza lambda o functools.partial.

    Si una tarea falla:
    - Se intenta cancelar las tareas que todavía no comienzan.
    - Se espera a las tareas que ya están ejecutándose.
    - Se propaga la excepción original.

    No cancela ni detiene por la fuerza funciones que ya comenzaron.
    No revierte sus efectos.
    """
    if (
        isinstance(trabajadores, bool)
        or not isinstance(trabajadores, int)
        or not 1 <= trabajadores <= 32
    ):
        raise ValueError(
            "trabajadores debe ser un entero entre 1 y 32"
        )

    if not all(callable(funcion) for funcion in funciones):
        raise TypeError(
            "Todas las tareas deben ser funciones ejecutables"
        )

    if not funciones:
        return []

    resultados: dict[int, T] = {}

    with ThreadPoolExecutor(
        max_workers=min(trabajadores, len(funciones)),
        thread_name_prefix="macro-paralelo",
    ) as executor:
        pendientes = {}

        try:
            for indice, funcion in enumerate(funciones):
                # Cada tarea necesita su propia copia de Context.
                # No se puede ejecutar un mismo Context simultáneamente.
                contexto = copy_context()

                futuro = executor.submit(
                    contexto.run,
                    funcion,
                )

                pendientes[futuro] = indice

            for futuro in as_completed(pendientes):
                indice = pendientes[futuro]
                resultados[indice] = futuro.result()

        except BaseException:
            for futuro in pendientes:
                futuro.cancel()
            raise

    return [
        resultados[indice]
        for indice in range(len(funciones))
    ]