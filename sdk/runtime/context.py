"""Runtime kernel of the SDK: active context, settings, errors and guards.

Every other SDK feature (control, data, desktop, web, compose) depends on this
module and never the other way around. Imports are headless-safe: no
PyAutoGUI, Playwright or pandas here.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

__version__ = "2.0.0"
_current: ContextVar[MacroContext | None] = ContextVar("macro_context", default=None)


class MacroError(RuntimeError):
    pass


class ImageTimeout(MacroError, TimeoutError):
    pass


@dataclass(frozen=True)
class Match:
    left: int
    top: int
    width: int
    height: int

    @property
    def x(self) -> int:
        return self.left + self.width // 2

    @property
    def y(self) -> int:
        return self.top + self.height // 2

    def to_dict(self) -> dict[str, int]:
        return {**asdict(self), "x": self.x, "y": self.y}


@dataclass
class Settings:
    confidence: float | None = 0.9
    timeout: float = 20
    poll: float = 0.4
    grayscale: bool = False
    region: tuple[int, int, int, int] | None = None
    pause: float = 0.15
    screenshot_on_error: bool = True


class MacroContext:
    def __init__(self, run_dir: Path, parameters: dict[str, Any]):
        self.run_dir = Path(run_dir).resolve()
        self.parameters = parameters
        self.package_dir = self.run_dir / "package"
        self.output_dir = self.run_dir / "artifacts"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings()
        self._redactions: set[str] = set()

    def log(self, message: object):
        text = str(message)
        for secret_value in sorted(self._redactions, key=len, reverse=True):
            text = text.replace(secret_value, "***")
        print(text, flush=True)

    def artifact(self, name: str) -> Path:
        path = _inside(self.output_dir, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def asset(self, name: str | Path) -> Path:
        raw = str(name).strip()

        if not raw:
            raise ValueError("La ruta del recurso no puede estar vacía")

        path = Path(raw).expanduser()

        # Ruta absoluta externa:
        # C:\folder\archivo.png
        # G:\Share\folder\archivo.png
        # \\servidor\share\folder\archivo.png
        if path.is_absolute():
            path = path.resolve()

            if not path.is_file():
                raise FileNotFoundError(
                    f"Recurso externo no encontrado: {path}"
                )

            return path

        # Ruta relativa dentro del paquete
        relative = raw if "/" in raw.replace("\\", "/") else f"assets/{raw}"
        path = _inside(self.package_dir, relative)

        if not path.is_file():
            raise FileNotFoundError(
                f"Recurso no encontrado: {relative}. "
                "Subelo en la pestana Archivos o proporciona una ruta absoluta."
            )

        return path

    def set_result(self, value: Any):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
        if len(raw.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("Resultado mayor a 2 MB")
        temp = self.run_dir / "result.tmp"
        temp.write_text(raw, encoding="utf-8")
        temp.replace(self.run_dir / "result.json")

    def secret(self, key: str, default: str = "") -> str:
        value = os.getenv(key, default)
        if value:
            self._redactions.add(value)
        return value

    def screenshot(self, name: str = "screenshot.png") -> Path:
        from ..desktop.screen import captura

        return captura(name)


def _inside(base: Path, name: str) -> Path:
    if (
        not name
        or "\\" in name
        or ":" in name
        or Path(name).is_absolute()
        or ".." in Path(name).parts
    ):
        raise ValueError("Se requiere una ruta relativa segura")

    target = (base / name).resolve()

    if not target.is_relative_to(base.resolve()):
        raise ValueError("Ruta fuera del directorio permitido")

    return target


def _number(value: float, name: str, minimum: float, maximum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{name} debe estar entre {minimum} y {maximum}")
    return float(value)


def _context() -> MacroContext:
    context = _current.get()
    if context is None:
        raise MacroError("No hay contexto activo. Ejecuta la macro desde Macro Studio o usa activar_contexto(context).")
    return context


@contextmanager
def activar_contexto(context: MacroContext) -> Iterator[MacroContext]:
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
