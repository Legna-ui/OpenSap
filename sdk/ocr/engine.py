"""Tesseract OCR engine: binary discovery, image sources and the `tesseract` subprocess.

Tesseract is a native program, not a pip package. Search order for `tesseract.exe`:
1. `MACRO_TESSERACT` (path to the exe or to its folder).
2. The copy bundled with the VS Code kit (`.sdk/tesseract`), with the portable/installer
   (`<install root>/tesseract`) or the one vendored in the repository (`tools/tesseract`).
3. `PATH` and the usual install folders (Program Files, %LOCALAPPDATA%\\Programs).

Only stdlib + Pillow (already required by PyAutoGUI); Pillow is imported lazily.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..runtime.context import MacroError, Match, _context, _number

_HERE = Path(__file__).resolve()
_PROCESS_TIMEOUT = 120
DEFAULT_LANGUAGES = ("spa", "eng")


class OcrNoDisponible(MacroError):
    """Tesseract no esta instalado o no arranca."""


@dataclass(frozen=True)
class Word:
    text: str
    confidence: float
    left: int
    top: int
    width: int
    height: int
    line: tuple[int, int, int, int]

    def to_dict(self) -> dict[str, Any]:
        match = Match(self.left, self.top, self.width, self.height)
        return {"texto": self.text, "confianza": self.confidence, **match.to_dict()}


def _exe_in(folder: Path) -> Path | None:
    exe = folder / ("tesseract.exe" if os.name == "nt" else "tesseract")
    return exe if exe.is_file() else None


def _candidates() -> list[Path]:
    found: list[Path | None] = []
    configured = os.environ.get("MACRO_TESSERACT", "").strip()
    if configured:
        path = Path(os.path.expandvars(configured)).expanduser()
        found.append(path if path.is_file() else _exe_in(path))
    # app/sdk/ocr/engine.py -> parents[3] = the `.sdk` folder of the VS Code kit (bundled copy);
    # parents[4] = install root (portable/installer) or the repository.
    if len(_HERE.parents) > 4:
        found += [
            _exe_in(_HERE.parents[3] / "tesseract"),
            _exe_in(_HERE.parents[4] / "tesseract"),
            _exe_in(_HERE.parents[4] / "tools" / "tesseract"),
        ]
    which = shutil.which("tesseract")
    found.append(Path(which) if which else None)
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            found += [_exe_in(Path(base) / "Tesseract-OCR"), _exe_in(Path(base) / "Programs" / "Tesseract-OCR")]
    return [path for path in found if path is not None]


def binary() -> Path:
    candidates = _candidates()
    if not candidates:
        raise OcrNoDisponible(
            "No se encontro Tesseract OCR. Reinstala Macro Studio (lo incluye), comprueba tools\\tesseract "
            "en desarrollo o define MACRO_TESSERACT con la ruta de tesseract.exe."
        )
    return candidates[0]


def _tessdata(exe: Path) -> Path | None:
    folder = exe.parent / "tessdata"
    return folder if folder.is_dir() else None


def _run(exe: Path, arguments: list[str], *, timeout: float = _PROCESS_TIMEOUT) -> str:
    command = [str(exe)]
    tessdata = _tessdata(exe)
    if tessdata is not None:
        command += ["--tessdata-dir", str(tessdata)]
    command += arguments
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    # OMP_THREAD_LIMIT=1: Tesseract's OpenMP threads make small images slower, not faster.
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=timeout, creationflags=flags, env=env, stdin=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired as error:
        raise MacroError(f"Tesseract no respondio en {timeout}s") from error
    except OSError as error:
        raise OcrNoDisponible(f"No se pudo ejecutar Tesseract ({exe}): {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise MacroError(f"Tesseract fallo: {detail[-1] if detail else completed.returncode}")
    return completed.stdout.decode("utf-8", "replace")


@lru_cache(maxsize=8)
def _languages(exe: Path) -> tuple[str, ...]:
    lines = _run(exe, ["--list-langs"], timeout=30).splitlines()
    return tuple(sorted(line.strip() for line in lines[1:] if line.strip()))


@lru_cache(maxsize=8)
def _version(exe: Path) -> str:
    output = _run(exe, ["--version"], timeout=30).strip().splitlines()
    return output[0].replace("tesseract", "").strip() if output else ""


def info() -> dict[str, Any]:
    try:
        exe = binary()
        return {"disponible": True, "ruta": str(exe), "version": _version(exe), "idiomas": list(_languages(exe))}
    except MacroError as error:
        return {"disponible": False, "ruta": None, "version": None, "idiomas": [], "error": str(error)}


def language_arg(exe: Path, idioma: str | None) -> str:
    """Return the -l value: the default keeps only installed languages; an explicit one must exist."""
    installed = _languages(exe)
    if idioma is None:
        chosen = [code for code in DEFAULT_LANGUAGES if code in installed]
        if not chosen:
            raise OcrNoDisponible(f"Tesseract no tiene idiomas spa ni eng instalados (tiene: {', '.join(installed)})")
        return "+".join(chosen)
    if not isinstance(idioma, str) or not idioma.strip():
        raise ValueError("idioma debe ser un codigo de Tesseract, por ejemplo 'spa', 'eng' o 'spa+eng'")
    codes = [code.strip() for code in idioma.split("+")]
    missing = [code for code in codes if code not in installed]
    if missing:
        raise MacroError(
            f"Idioma OCR no instalado: {', '.join(missing)}. Disponibles: {', '.join(installed)}. "
            "Copia <idioma>.traineddata (tessdata_fast) en la carpeta tessdata de Tesseract."
        )
    return "+".join(codes)


# ------------------------------------------------------------------ image sources


def _region(region: Any) -> tuple[int, int, int, int]:
    if isinstance(region, Match):
        region = (region.left, region.top, region.width, region.height)
    if (
        not isinstance(region, (tuple, list))
        or len(region) != 4
        or any(isinstance(v, bool) or not isinstance(v, int) for v in region)
        or region[2] <= 0
        or region[3] <= 0
    ):
        raise ValueError("region debe ser (x, y, ancho, alto) con enteros y tamano positivo, o un Match")
    return tuple(region)  # type: ignore[return-value]


def load(imagen: Any, region: Any) -> tuple[Any, tuple[int, int]]:
    """Return (PIL image, origin) where origin is added to word boxes to give screen/image coordinates."""
    from PIL import Image

    box = _region(region) if region is not None else None
    if imagen is None:
        from ..desktop import screen

        gui = screen._gui()
        if box is None and _context().settings.region is not None:
            box = _context().settings.region
        image = gui.screenshot(region=box) if box else gui.screenshot()
        return image, (box[0], box[1]) if box else (0, 0)
    if isinstance(imagen, Image.Image):
        image = imagen
    elif isinstance(imagen, (str, os.PathLike)):
        path = _context().asset(imagen)
        with Image.open(path) as opened:
            image = opened.copy()
    else:
        raise ValueError("imagen debe ser None (pantalla), un nombre/ruta de archivo o una imagen PIL")
    if box is not None:
        left, top, width, height = box
        if left < 0 or top < 0 or left + width > image.width or top + height > image.height:
            raise ValueError(f"region fuera de la imagen ({image.width}x{image.height})")
        image = image.crop((left, top, left + width, top + height))
        return image, (left, top)
    return image, (0, 0)


def prepare(image: Any, escala: float, binarizar: bool) -> Any:
    """Grayscale + upscale (screen text is ~10px; Tesseract prefers ~30px glyphs) + optional threshold."""
    from PIL import Image, ImageOps

    _number(escala, "escala", 1, 6)
    result = ImageOps.grayscale(image.convert("RGB"))
    if escala != 1:
        result = result.resize((round(result.width * escala), round(result.height * escala)), Image.Resampling.LANCZOS)
    result = ImageOps.autocontrast(result)
    if binarizar:
        result = result.point(lambda value: 255 if value > 128 else 0)
    # Tesseract reads dark text on a light background best: invert dark themes.
    histogram = result.histogram()
    dark = sum(histogram[:128])
    if dark > sum(histogram[128:]):
        result = ImageOps.invert(result)
    return result


def _psm(modo: int) -> str:
    if isinstance(modo, bool) or not isinstance(modo, int) or modo not in (*range(3, 14),):
        raise ValueError("modo (PSM de Tesseract) debe ser un entero entre 3 y 13")
    return str(modo)


def recognize(image: Any, *, idioma: str | None, modo: int, tsv: bool, lista_blanca: str | None = None) -> str:
    exe = binary()
    arguments = ["-l", language_arg(exe, idioma), "--psm", _psm(modo)]
    if lista_blanca:
        arguments += ["-c", f"tessedit_char_whitelist={lista_blanca}"]
    handle, name = tempfile.mkstemp(prefix="macro-ocr-", suffix=".png")
    os.close(handle)
    try:
        image.save(name, format="PNG")
        return _run(exe, [name, "stdout", *arguments, *(["tsv"] if tsv else [])])
    finally:
        Path(name).unlink(missing_ok=True)


def parse_tsv(text: str, origin: tuple[int, int], escala: float) -> list[Word]:
    words = []
    for row in csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE):
        content = (row.get("text") or "").strip()
        if row.get("level") != "5" or not content:
            continue
        try:
            confidence = float(row["conf"])
            left, top, width, height = (int(row[key]) for key in ("left", "top", "width", "height"))
            line = tuple(int(row[key]) for key in ("page_num", "block_num", "par_num", "line_num"))
        except (KeyError, TypeError, ValueError):
            continue
        words.append(
            Word(
                content,
                round(confidence, 1),
                origin[0] + round(left / escala),
                origin[1] + round(top / escala),
                max(1, round(width / escala)),
                max(1, round(height / escala)),
                line,  # type: ignore[arg-type]
            )
        )
    return words


def normalize(text: str) -> str:
    """Case/accent-insensitive comparison key with collapsed whitespace."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def find(words: list[Word], texto: str, exacto: bool) -> Match | None:
    """First run of consecutive words in one line whose text matches `texto`."""
    target = normalize(texto)
    if not target:
        raise ValueError("texto no puede estar vacio")
    size = len(target.split())
    lines: dict[tuple[int, int, int, int], list[Word]] = {}
    for word in words:
        lines.setdefault(word.line, []).append(word)
    for line in lines.values():
        for start in range(len(line)):
            for end in range(start, min(len(line), start + size + 2)):
                joined = normalize(" ".join(word.text for word in line[start : end + 1]))
                if joined == target or (not exacto and target in joined):
                    chosen = line[start : end + 1]
                    left = min(word.left for word in chosen)
                    top = min(word.top for word in chosen)
                    right = max(word.left + word.width for word in chosen)
                    bottom = max(word.top + word.height for word in chosen)
                    return Match(left, top, right - left, bottom - top)
    return None
