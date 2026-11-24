"""Clipboard, shell commands, opening files, desktop notifications and dates."""

from __future__ import annotations

import calendar
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from ..data.folders import ruta
from ..runtime.context import MacroError, _current, _number

_SPANISH_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_SPANISH_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                   "septiembre", "octubre", "noviembre", "diciembre"]


def _log(message: str) -> None:
    context = _current.get()
    if context is not None:
        context.log(message)


# ------------------------------------------------------------- clipboard


def copiar_portapapeles(texto: object) -> None:
    """Pone texto en el portapapeles de Windows."""
    import pyperclip

    pyperclip.copy(str(texto))


def leer_portapapeles() -> str:
    """Texto actual del portapapeles (útil tras un Ctrl+C en otra app)."""
    import pyperclip

    return pyperclip.paste() or ""


# --------------------------------------------------------------- commands


def ejecutar_comando(comando: str | list[str], *, carpeta: str | None = None, tiempo_maximo: float = 300,
                     comprobar: bool = True, shell: bool | None = None) -> str:
    """Ejecuta un comando (cmd, .bat, PowerShell, un .exe...) y devuelve su salida.

    Con `comprobar=True` lanza MacroError si termina con código distinto de 0.
    Un texto se ejecuta en cmd; una lista se ejecuta sin shell.
    """
    timeout = _number(tiempo_maximo, "tiempo_maximo", 1, 86400)
    use_shell = isinstance(comando, str) if shell is None else shell
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        done = subprocess.run(
            comando, shell=use_shell, cwd=str(ruta(carpeta)) if carpeta else None, capture_output=True,
            timeout=timeout, text=True, encoding="utf-8", errors="replace", **options,
        )
    except subprocess.TimeoutExpired as error:
        raise MacroError(f"El comando superó {timeout:g}s: {comando}") from error
    output = (done.stdout or "") + (done.stderr or "")
    _log(f"Comando ({done.returncode}): {comando if isinstance(comando, str) else ' '.join(comando)}")
    if comprobar and done.returncode != 0:
        raise MacroError(f"El comando terminó con código {done.returncode}: {output.strip()[-800:]}")
    return output.strip()


def abrir_archivo(objetivo: str | os.PathLike) -> Path:
    """Abre un archivo o carpeta con su programa predeterminado (Excel, Explorador...)."""
    path = ruta(objetivo)
    if not path.exists():
        raise FileNotFoundError(f"No existe: {path}")
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:  # pragma: no cover - the product targets Windows
        subprocess.Popen(["xdg-open", str(path)])
    return path


def abrir_programa(programa: str | os.PathLike, *argumentos: str, carpeta: str | None = None) -> int:
    """Lanza un programa (.exe) que sigue abierto aunque la macro termine.

    A diferencia de `ejecutar_comando`, no espera ni captura la salida: sirve para apps de escritorio
    (por ejemplo un .exe hecho en Go) que abren su propia ventana.

    Args:
        programa: Ruta del .exe (relativa al paquete, p. ej. `recurso("miapp.exe")`, o absoluta).
        *argumentos: Argumentos de línea de comandos para el programa.
        carpeta: Carpeta de trabajo; por defecto la del programa.

    Returns:
        El PID del proceso lanzado.

    Example:
        abrir_programa(recurso("miapp.exe"), "--modo", "oscuro")
    """
    path = ruta(programa)
    if not path.is_file():
        raise FileNotFoundError(f"No existe el programa: {path}")
    workdir = str(ruta(carpeta)) if carpeta else str(path.parent)
    options: dict = {}
    if sys.platform == "win32":
        breakaway = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB: no muere al cerrar el job de la ejecución
        options["creationflags"] = breakaway | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:  # pragma: no cover - the product targets Windows
        options["start_new_session"] = True
    process = subprocess.Popen(
        [str(path), *map(str, argumentos)], cwd=workdir, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options,
    )
    _log(f"Programa abierto: {path.name} (PID {process.pid})")
    return process.pid


def notificar(titulo: str, mensaje: str = "") -> None:
    """Notificación de Windows (esquina inferior derecha). Si no se puede, solo registra en el log."""
    _log(f"Notificación: {titulo} {mensaje}".strip())
    if sys.platform != "win32":
        return
    def xml(text: str) -> str:
        return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&apos;"))

    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{xml(titulo)}</text>"
        f"<text>{xml(mensaje)}</text></binding></visual></toast>');"
        "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app)"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         creationflags=subprocess.CREATE_NO_WINDOW, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ------------------------------------------------------------------ dates


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def fecha(formato: str = "%Y-%m-%d", *, dias: int = 0, meses: int = 0, habiles: int = 0,
          base: date | datetime | str | None = None) -> str:
    """Fecha formateada con desplazamientos: fecha("%d/%m/%Y", dias=-1) = ayer.

    `habiles` salta sábados y domingos. Además de los códigos de strftime acepta
    `{dia}` y `{mes}` (nombres en español), p. ej. fecha("{dia} %d de {mes}").
    """
    if isinstance(base, str):
        current = datetime.fromisoformat(base)
    elif isinstance(base, datetime):
        current = base
    elif isinstance(base, date):
        current = datetime(base.year, base.month, base.day)
    else:
        current = datetime.now()
    current = current + timedelta(days=dias)
    if meses:
        moved = _add_months(current.date(), meses)
        current = current.replace(year=moved.year, month=moved.month, day=moved.day)
    step = 1 if habiles > 0 else -1
    remaining = abs(habiles)
    while remaining:
        current += timedelta(days=step)
        if current.weekday() < 5:
            remaining -= 1
    text = formato.replace("{dia}", _SPANISH_DAYS[current.weekday()])
    text = text.replace("{mes}", _SPANISH_MONTHS[current.month - 1])
    return current.strftime(text)


def es_dia_habil(dia: date | datetime | str | None = None, *, festivos: list[str] | None = None) -> bool:
    """True de lunes a viernes, salvo las fechas ISO de `festivos` (["2026-12-25", ...])."""
    value = datetime.fromisoformat(dia).date() if isinstance(dia, str) else (
        dia.date() if isinstance(dia, datetime) else (dia or date.today()))
    return value.weekday() < 5 and value.isoformat() not in set(festivos or [])


def fin_de_mes(dia: date | datetime | str | None = None, formato: str = "%Y-%m-%d") -> str:
    """Último día del mes de `dia` (por defecto, el mes actual)."""
    value = datetime.fromisoformat(dia) if isinstance(dia, str) else (dia or datetime.now())
    last = calendar.monthrange(value.year, value.month)[1]
    return value.replace(day=last).strftime(formato)
