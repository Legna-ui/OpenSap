"""Package resources (inputs) and run artifacts (outputs): JSON, CSV and text."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..runtime.context import _context


def recurso(nombre: str) -> Path:
    """Ruta de un recurso del paquete; los nombres sueltos se buscan en assets/."""
    return _context().asset(nombre)


def archivo_salida(nombre: str) -> Path:
    """Ruta de un artefacto descargable de la ejecución."""
    return _context().artifact(nombre)


def leer_json(nombre: str) -> Any:
    return json.loads(recurso(nombre).read_text(encoding="utf-8-sig"))


def leer_csv(nombre: str, *, separador: str = ",") -> list[dict[str, str]]:
    with recurso(nombre).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream, delimiter=separador))


def guardar_json(nombre: str, datos: Any) -> Path:
    path = archivo_salida(nombre)
    path.write_text(json.dumps(datos, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    return path


def guardar_texto(nombre: str, texto: str) -> Path:
    path = archivo_salida(nombre)
    path.write_text(texto, encoding="utf-8")
    return path
