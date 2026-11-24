"""Files and folders anywhere on disk: latest file/folder, waits for downloads, copy/move, ZIP.

Paths accept `~`, environment variables (`%USERPROFILE%`) and the names of
`carpeta()` ("descargas", "escritorio", "documentos"...). Low-code friendly:
every function returns a `Path` (or a list of them) and raises a clear error.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import sys
import time
import zipfile
from collections.abc import Iterable
from pathlib import Path

from ..runtime.context import MacroError, _current, _number

# Files a browser or an editor is still writing.
_PARTIAL_SUFFIXES = (".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload")
_ORDERS = ("reciente", "antiguo", "nombre", "tamano")
_KNOWN = {
    "descargas": "Downloads", "escritorio": "Desktop", "documentos": "Documents",
    "imagenes": "Pictures", "musica": "Music", "videos": "Videos",
}


def carpeta(nombre: str) -> Path:
    """Ruta de una carpeta conocida: descargas, escritorio, documentos, imagenes, musica,
    videos, usuario, temporal, salida (artefactos de la ejecución) o paquete."""
    key = nombre.strip().lower().replace("á", "a").replace("í", "i").replace("ú", "u")
    home = Path.home()
    if key in _KNOWN:
        candidate = home / _KNOWN[key]
        if key in ("documentos", "escritorio", "imagenes") and sys.platform == "win32":
            onedrive = os.environ.get("OneDrive")
            if onedrive and not candidate.is_dir() and (Path(onedrive) / _KNOWN[key]).is_dir():
                candidate = Path(onedrive) / _KNOWN[key]
        return candidate
    if key == "usuario":
        return home
    if key == "temporal":
        return Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
    context = _current.get()
    if key == "salida" and context is not None:
        return context.output_dir
    if key == "paquete" and context is not None:
        return context.package_dir
    raise ValueError(f"Carpeta desconocida: {nombre}")


def ruta(valor: str | os.PathLike) -> Path:
    """Normaliza una ruta: expande ~, variables de entorno y nombres de carpeta()."""
    text = os.path.expandvars(os.fspath(valor)).strip()
    first, _, rest = text.replace("\\", "/").partition("/")
    lowered = first.lower()
    if lowered in _KNOWN or lowered in ("usuario", "temporal", "salida", "paquete"):
        try:
            return carpeta(first) / rest if rest else carpeta(first)
        except ValueError:
            pass
    return Path(text).expanduser()


def _partial(path: Path) -> bool:
    return path.name.startswith("~$") or path.suffix.lower() in _PARTIAL_SUFFIXES


def _entries(folder: str | os.PathLike, pattern: str, *, files: bool, recursive: bool,
             hidden: bool) -> list[Path]:
    base = ruta(folder)
    if not base.is_dir():
        raise FileNotFoundError(f"La carpeta no existe: {base}")
    candidates: Iterable[Path] = base.rglob("*") if recursive else base.iterdir()
    found = []
    for item in candidates:
        try:
            if (item.is_file() if files else item.is_dir()) and fnmatch.fnmatch(item.name.lower(), pattern.lower()):
                if not hidden and item.name.startswith("."):
                    continue
                if files and _partial(item):
                    continue
                found.append(item)
        except OSError:
            continue
    return found


def _sort(items: list[Path], order: str, by: str) -> list[Path]:
    if order not in _ORDERS:
        raise ValueError(f"orden debe ser uno de {', '.join(_ORDERS)}")
    if by not in ("modificado", "creado"):
        raise ValueError("por debe ser 'modificado' o 'creado'")

    def stamp(path: Path) -> float:
        info = path.stat()
        return info.st_ctime if by == "creado" and sys.platform == "win32" else info.st_mtime

    if order == "nombre":
        return sorted(items, key=lambda p: p.name.lower())
    if order == "tamano":
        return sorted(items, key=lambda p: p.stat().st_size, reverse=True)
    return sorted(items, key=stamp, reverse=order == "reciente")


def listar_archivos(carpeta: str | os.PathLike, patron: str = "*", *, recursivo: bool = False,
                    orden: str = "reciente", por: str = "modificado", ocultos: bool = False) -> list[Path]:
    """Archivos de una carpeta que cumplen el patrón (p. ej. "*.xlsx", "factura_*.pdf").

    Ignora descargas a medias (.crdownload, .part, .tmp) y archivos ~$ de Office.
    `orden`: reciente (por defecto), antiguo, nombre o tamano.
    """
    return _sort(_entries(carpeta, patron, files=True, recursive=recursivo, hidden=ocultos), orden, por)


def listar_carpetas(carpeta: str | os.PathLike, patron: str = "*", *, orden: str = "reciente",
                    por: str = "modificado", ocultos: bool = False) -> list[Path]:
    """Subcarpetas (solo el primer nivel) que cumplen el patrón."""
    return _sort(_entries(carpeta, patron, files=False, recursive=False, hidden=ocultos), orden, por)


def ultimo_archivo(carpeta: str | os.PathLike, patron: str = "*", *, recursivo: bool = False,
                   por: str = "modificado", tiempo_maximo: float = 0) -> Path:
    
    """El archivo más reciente de la carpeta que cumple el patrón.

        reporte = ultimo_archivo("descargas", "*.xlsx")

    Con `tiempo_maximo` espera a que exista alguno. `por="creado"` usa la fecha de creación.
    """
    timeout = _number(tiempo_maximo, "tiempo_maximo", 0, 3600)
    deadline = time.monotonic() + timeout
    while True:
        items = listar_archivos(carpeta, patron, recursivo=recursivo, por=por)
        if items:
            return items[0]
        if time.monotonic() >= deadline:
            raise FileNotFoundError(f"No hay archivos '{patron}' en {ruta(carpeta)}")
        time.sleep(0.5)


def ultima_carpeta(carpeta: str | os.PathLike, patron: str = "*", *, por: str = "modificado") -> Path:
    """La subcarpeta más reciente que cumple el patrón (p. ej. la última exportación)."""
    items = listar_carpetas(carpeta, patron, por=por)
    if not items:
        raise FileNotFoundError(f"No hay carpetas '{patron}' en {ruta(carpeta)}")
    return items[0]


def esperar_archivo(carpeta: str | os.PathLike, patron: str = "*", *, nuevo: bool = True,
                    tiempo_maximo: float = 120, estable: float = 1.0) -> Path:
    """Espera a que aparezca un archivo (típico: una descarga) y a que termine de escribirse.

        excel = esperar_archivo("descargas", "*.xlsx")

    `nuevo=True` solo acepta archivos modificados después de llamar a la función.
    `estable`: segundos que el tamaño debe quedarse quieto para darlo por terminado.
    """
    timeout = _number(tiempo_maximo, "tiempo_maximo", 0, 86400)
    quiet = _number(estable, "estable", 0, 60)
    since = time.time() - 1
    deadline = time.monotonic() + timeout
    while True:
        for candidate in listar_archivos(carpeta, patron):
            try:
                info = candidate.stat()
            except OSError:
                continue
            if nuevo and info.st_mtime < since:
                break  # sorted newest first: nothing newer below
            if _settled(candidate, quiet):
                return candidate
        if time.monotonic() >= deadline:
            raise MacroError(f"No apareció ningún archivo '{patron}' en {ruta(carpeta)} en {timeout:g}s")
        time.sleep(0.5)


def _settled(path: Path, seconds: float) -> bool:
    try:
        size = path.stat().st_size
        time.sleep(seconds)
        if path.stat().st_size != size:
            return False
        with path.open("rb"):
            return True
    except OSError:
        return False


def crear_carpeta(destino: str | os.PathLike) -> Path:
    """Crea la carpeta (y las intermedias) si no existe y la devuelve."""
    path = ruta(destino)
    path.mkdir(parents=True, exist_ok=True)
    return path


def nombre_libre(destino: str | os.PathLike) -> Path:
    """La misma ruta, o `nombre (2).ext`, `nombre (3).ext`... si ya existe."""
    path = ruta(destino)
    if not path.exists():
        return path
    for n in range(2, 10000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise MacroError(f"Demasiadas copias de {path.name}")


def _target(source: Path, destination: str | os.PathLike, overwrite: bool) -> Path:
    target = ruta(destination)
    if target.is_dir():
        target = target / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        target = nombre_libre(target)
    return target


def copiar(origen: str | os.PathLike, destino: str | os.PathLike, *, sobrescribir: bool = False) -> Path:
    """Copia un archivo o carpeta. Si `destino` es una carpeta, conserva el nombre.
    Sin `sobrescribir`, no pisa nada: usa `nombre (2).ext`."""
    source = ruta(origen)
    if not source.exists():
        raise FileNotFoundError(f"No existe: {source}")
    target = _target(source, destino, sobrescribir)
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=sobrescribir)
    else:
        shutil.copy2(source, target)
    return target


def mover(origen: str | os.PathLike, destino: str | os.PathLike, *, sobrescribir: bool = False) -> Path:
    """Mueve un archivo o carpeta (mismas reglas que copiar)."""
    source = ruta(origen)
    if not source.exists():
        raise FileNotFoundError(f"No existe: {source}")
    target = _target(source, destino, sobrescribir)
    if target.exists() and sobrescribir and target.is_file():
        target.unlink()
    return Path(shutil.move(str(source), str(target)))


def renombrar(origen: str | os.PathLike, nuevo_nombre: str) -> Path:
    """Cambia el nombre manteniendo la carpeta. `nuevo_nombre` no puede incluir rutas."""
    if any(sep in nuevo_nombre for sep in ("/", "\\")) or not nuevo_nombre.strip():
        raise ValueError("nuevo_nombre debe ser solo un nombre de archivo")
    source = ruta(origen)
    target = source.with_name(nuevo_nombre)
    if target.exists():
        raise FileExistsError(f"Ya existe {target}")
    return source.rename(target)


def eliminar(objetivo: str | os.PathLike, *, papelera: bool = True) -> None:
    """Elimina un archivo o carpeta; por defecto lo envía a la Papelera de reciclaje."""
    path = ruta(objetivo)
    if not path.exists():
        return
    if path.resolve() in (Path.home().resolve(), Path(path.anchor).resolve()):
        raise MacroError("Por seguridad no se puede eliminar la carpeta de usuario ni una unidad")
    if papelera and sys.platform == "win32" and _recycle(path):
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _recycle(path: Path) -> bool:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 3, 0x40, 0x10, 0x4, 0x400
    operation = SHFILEOPSTRUCTW(
        None, FO_DELETE, str(path.resolve()) + "\0", None,
        FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI, False, None, None,
    )
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation)) == 0 and not path.exists()


def comprimir(origen: str | os.PathLike | list, destino: str | os.PathLike) -> Path:
    """Crea un ZIP con un archivo, una carpeta o una lista de ellos."""
    sources = [ruta(item) for item in (origen if isinstance(origen, list) else [origen])]
    target = ruta(destino)
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in sources:
            if not source.exists():
                raise FileNotFoundError(f"No existe: {source}")
            if source.is_dir():
                for item in source.rglob("*"):
                    if item.is_file():
                        archive.write(item, Path(source.name) / item.relative_to(source))
            else:
                archive.write(source, source.name)
    return target


def descomprimir(archivo_zip: str | os.PathLike, destino: str | os.PathLike | None = None) -> Path:
    """Extrae un ZIP (por defecto en una carpeta con su nombre) y devuelve la carpeta."""
    source = ruta(archivo_zip)
    target = ruta(destino) if destino else source.with_suffix("")
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    with zipfile.ZipFile(source) as archive:
        for member in archive.namelist():
            if not (root / member).resolve().is_relative_to(root):
                raise MacroError(f"El ZIP intenta escribir fuera del destino: {member}")
        archive.extractall(target)
    return target


def leer_archivo(origen: str | os.PathLike, *, codificacion: str = "utf-8-sig") -> str:
    """Texto completo de un archivo de disco (no del paquete: para eso, recurso())."""
    return ruta(origen).read_text(encoding=codificacion)


def escribir_archivo(destino: str | os.PathLike, texto: object, *, agregar: bool = False,
                     codificacion: str = "utf-8") -> Path:
    """Escribe (o con `agregar=True` añade al final) texto en un archivo de disco."""
    path = ruta(destino)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if agregar else "w", encoding=codificacion, newline="") as stream:
        stream.write(str(texto))
    return path


def existe(objetivo: str | os.PathLike) -> bool:
    return ruta(objetivo).exists()
