"""Hybrid desktop automation: UI Automation first, then image, then coordinates.

This is what the desktop recorder generates (Power Automate style selectors that
read like Playwright locators)::

    from macro_sdk import ui

    def run(context):
        bloc = ui.ventana("Sin título: Bloc de notas", proceso="Notepad.exe", clase="Notepad")
        bloc.elemento("Editor de texto", rol="Document", imagen="assets/grabacion/01-editor.png").clic()
        ui.escribir("Hola mundo")
        ui.atajo("ctrl", "s")

Every action resolves its target with a fallback chain, retried until
`tiempo_maximo`:

1. **UIA** (pywinauto): Name / rol (ControlType) / AutomationId / clase inside the
   window, with `indice` when several controls match.
2. **Imagen**: the recorded crop of the control, searched with OpenCV on every
   monitor (inside the window first).
3. **Coordenadas** (`desde_ventana=(x, y)`): offset from the window's top-left
   corner, only when given and the window exists.

The log says which strategy found each element, so a recording that only works
by image or coordinates is easy to spot and improve.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..runtime.context import MacroError, Settings, _current, _number
from . import screen, uia

__all__ = [
    "ElementoNoEncontrado", "Ventana", "Elemento", "ventana", "imagen", "abrir_app",
    "escribir", "tecla", "atajo", "scroll", "esperar_ventana", "ventanas",
]

_BUTTONS = {"izquierdo": "left", "derecho": "right", "central": "middle",
            "left": "left", "right": "right", "middle": "middle"}
_SEND_KEYS_SPECIAL = re.compile(r"([{}+^%~()])")


class ElementoNoEncontrado(MacroError, TimeoutError):
    pass


# --------------------------------------------------------------- plumbing


def _settings() -> Settings:
    context = _current.get()
    return context.settings if context is not None else Settings()


def _log(message: str) -> None:
    context = _current.get()
    if context is not None:
        context.log(message)
    else:
        print(message, flush=True)


def _timeout(value: float | None) -> float:
    return _settings().timeout if value is None else _number(value, "tiempo_maximo", 0, 3600)


def _gui():
    uia.dpi_aware()
    context = _current.get()
    if context is not None:
        return screen._gui()
    import pyautogui

    pyautogui.FAILSAFE = True
    return pyautogui


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _asset(path: str) -> Path:
    context = _current.get()
    if context is not None:
        return context.asset(path)
    candidate = Path(path)
    if not candidate.is_file():
        raise FileNotFoundError(f"Recurso no encontrado: {path}")
    return candidate


def _virtual_origin() -> tuple[int, int]:
    import ctypes

    uia.dpi_aware()
    metrics = ctypes.windll.user32.GetSystemMetrics
    return int(metrics(76)), int(metrics(77))


def _find_image(path: str, region: tuple[int, int, int, int] | None, confidence: float | None,
                grayscale: bool) -> tuple[int, int, int, int] | None:
    """OpenCV template match over every monitor; returns (left, top, right, bottom) in screen px."""
    try:
        import cv2
        import numpy
        from PIL import Image, ImageGrab
    except ImportError as error:  # pragma: no cover - dependency missing
        raise MacroError("Instala opencv-python y Pillow para buscar imágenes") from error
    uia.dpi_aware()
    with Image.open(_asset(path)) as needle_image:
        needle = numpy.asarray(needle_image.convert("RGB"))
    try:
        shot = ImageGrab.grab(all_screens=True)
    except TypeError:  # pragma: no cover - old Pillow
        shot = ImageGrab.grab()
    origin_x, origin_y = _virtual_origin()
    haystack = numpy.asarray(shot.convert("RGB"))
    offset_x, offset_y = origin_x, origin_y
    if region:
        left = max(0, region[0] - origin_x)
        top = max(0, region[1] - origin_y)
        right = min(haystack.shape[1], region[2] - origin_x)
        bottom = min(haystack.shape[0], region[3] - origin_y)
        if right - left >= needle.shape[1] and bottom - top >= needle.shape[0]:
            haystack = haystack[top:bottom, left:right]
            offset_x, offset_y = origin_x + left, origin_y + top
    if needle.shape[0] > haystack.shape[0] or needle.shape[1] > haystack.shape[1]:
        return None
    if grayscale:
        haystack = cv2.cvtColor(haystack, cv2.COLOR_RGB2GRAY)
        needle = cv2.cvtColor(needle, cv2.COLOR_RGB2GRAY)
    scores = cv2.matchTemplate(haystack, needle, cv2.TM_CCOEFF_NORMED)
    _min, best, _min_at, (x, y) = cv2.minMaxLoc(scores)
    threshold = 0.999 if confidence is None else confidence
    if best < threshold:
        return None
    left, top = offset_x + int(x), offset_y + int(y)
    return (left, top, left + needle.shape[1], top + needle.shape[0])


def _point_in(rect: tuple[int, int, int, int], point: tuple[float, float] | None) -> tuple[int, int]:
    left, top, right, bottom = rect
    fx, fy = point if point else (0.5, 0.5)
    return (round(left + (right - left) * fx), round(top + (bottom - top) * fy))


def _send_keys(text: str) -> None:
    from pywinauto import keyboard

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    escaped = _SEND_KEYS_SPECIAL.sub(r"{\1}", normalized)
    keyboard.send_keys(escaped, with_spaces=True, with_tabs=True, with_newlines=True, pause=0.01)


def _press_key(key: str, times: int = 1) -> None:
    gui = _gui()
    if key not in gui.KEYBOARD_KEYS:
        raise ValueError(f"Tecla no válida: {key}")
    gui.press(key, presses=times, interval=0.05)


# ----------------------------------------------------------------- window


class Ventana:
    """A top-level window found by title, process and/or class.

    The handle is cached after the first match, so the same object keeps
    working when the title changes (e.g. «Sin título» → «informe.txt»).
    """

    def __init__(self, titulo: str | None = None, *, proceso: str | None = None, clase: str | None = None,
                 exacto: bool = False) -> None:
        if titulo is None and not proceso and not clase:
            raise ValueError("Indica el título, el proceso o la clase de la ventana")
        if titulo is not None and (not isinstance(titulo, str) or len(titulo) > 500):
            raise ValueError("titulo debe ser un texto de hasta 500 caracteres")
        self.titulo, self.proceso, self.clase, self.exacto = titulo, proceso, clase, exacto
        self._hwnd = 0

    def __repr__(self) -> str:
        return f"Ventana({self.titulo!r}, proceso={self.proceso!r})"

    @property
    def descripcion(self) -> str:
        return f"«{self.titulo}»" if self.titulo else (self.proceso or self.clase or "ventana")

    def _find(self) -> uia.WindowInfo | None:
        if self._hwnd and uia.is_window(self._hwnd):
            return uia.window_info(self._hwnd)
        found = uia.match_window(uia.top_windows(), self.titulo, self.proceso, self.clase, self.exacto)
        self._hwnd = found.hwnd if found else 0
        return found

    def esperar(self, tiempo_maximo: float | None = None) -> Ventana:
        """Espera a que exista la ventana y devuelve la propia ventana."""
        timeout = _timeout(tiempo_maximo)
        deadline = time.monotonic() + timeout
        while True:
            if self._find():
                return self
            if time.monotonic() >= deadline:
                if _current.get() is not None:
                    screen._diagnostic("ventana-no-encontrada")
                raise ElementoNoEncontrado(f"No apareció la ventana {self.descripcion} en {timeout:g}s")
            _sleep(min(0.3, max(0.0, deadline - time.monotonic())))

    def existe(self) -> bool:
        return self._find() is not None

    @property
    def info(self) -> uia.WindowInfo:
        found = self._find()
        if not found:
            raise ElementoNoEncontrado(f"La ventana {self.descripcion} ya no existe")
        return found

    @property
    def titulo_actual(self) -> str:
        return self.info.title

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.info.rect

    def enfocar(self, *, maximizar: bool = False) -> Ventana:
        """Trae la ventana al frente (la restaura si está minimizada)."""
        info = self.esperar()._find() or self.info
        uia.activate(info.hwnd)
        if maximizar:
            import ctypes

            ctypes.windll.user32.ShowWindow(info.hwnd, 3)
        _sleep(0.15)
        return self

    def maximizar(self) -> Ventana:
        return self.enfocar(maximizar=True)

    def minimizar(self) -> None:
        import ctypes

        ctypes.windll.user32.ShowWindow(self.info.hwnd, 6)

    def cerrar(self) -> None:
        import ctypes

        ctypes.windll.user32.PostMessageW(self.info.hwnd, 0x0010, 0, 0)  # WM_CLOSE
        _log(f"ui: cerrar ventana {self.descripcion}")

    def _ensure_front(self) -> uia.WindowInfo:
        info = self.info
        if uia.foreground() != info.hwnd:
            uia.activate(info.hwnd)
            _sleep(0.1)
        return info

    def elemento(self, nombre: str | None = None, *, rol: str | None = None, id: str | None = None,
                 clase: str | None = None, indice: int | None = None, imagen: str | None = None,
                 punto: tuple[float, float] | None = None, desde_ventana: tuple[int, int] | None = None,
                 confianza: float | None = None) -> Elemento:
        """Localizador de un control de esta ventana (UIA → imagen → coordenadas)."""
        return Elemento(self, nombre=nombre, rol=rol, id=id, clase=clase, indice=indice, imagen=imagen,
                        punto=punto, desde_ventana=desde_ventana, confianza=confianza)

    def imagen(self, ruta: str, *, confianza: float | None = None, punto: tuple[float, float] | None = None,
               desde_ventana: tuple[int, int] | None = None) -> Elemento:
        """Localizador solo por imagen, buscado primero dentro de esta ventana."""
        return Elemento(self, imagen=ruta, confianza=confianza, punto=punto, desde_ventana=desde_ventana)

    def escribir(self, texto: object) -> None:
        self.enfocar()
        escribir(texto)

    def atajo(self, *teclas: str) -> None:
        self.enfocar()
        atajo(*teclas)


# ---------------------------------------------------------------- element


@dataclass
class _Hit:
    rect: tuple[int, int, int, int]
    via: str
    element: Any = None

    def point(self, punto: tuple[float, float] | None) -> tuple[int, int]:
        return _point_in(self.rect, punto if self.via == "uia" else None)


class Elemento:
    """A control located by UIA selector, recorded image and/or window offset."""

    def __init__(self, ventana: Ventana | None, *, nombre: str | None = None, rol: str | None = None,
                 id: str | None = None, clase: str | None = None, indice: int | None = None,
                 imagen: str | None = None, punto: tuple[float, float] | None = None,
                 desde_ventana: tuple[int, int] | None = None, confianza: float | None = None) -> None:
        if not (nombre or id or (rol and clase) or imagen or desde_ventana):
            raise ValueError("El elemento necesita nombre, id, imagen o desde_ventana")
        if indice is not None and (isinstance(indice, bool) or not isinstance(indice, int) or indice < 0):
            raise ValueError("indice debe ser un entero >= 0")
        if punto is not None and not all(0 <= float(v) <= 1 for v in punto):
            raise ValueError("punto son fracciones (0..1) del ancho y alto del elemento")
        if confianza is not None:
            _number(confianza, "confianza", 0.5, 0.999)
        self.ventana, self.nombre, self.rol, self.id, self.clase = ventana, nombre, rol, id, clase
        self.indice, self.imagen, self.punto, self.desde_ventana = indice, imagen, punto, desde_ventana
        self.confianza = confianza

    def __repr__(self) -> str:
        return f"Elemento({self.descripcion})"

    @property
    def descripcion(self) -> str:
        if self.nombre:
            return f"«{self.nombre}»"
        if self.id:
            return f"#{self.id}"
        if self.imagen:
            return os.path.basename(self.imagen)
        return self.rol or "elemento"

    # -- resolution
    def _uia(self, window: uia.WindowInfo):
        if not (self.nombre or self.id or (self.rol and self.clase)):
            return None
        root = uia.element_from_handle(window.hwnd)
        matches = uia.find_all(root, name=self.nombre, role=self.rol, auto_id=self.id, class_name=self.clase)
        index = self.indice or 0
        if len(matches) <= index:
            return None
        element = matches[index]
        left, top, right, bottom = uia.rect_of(element)
        if right - left < 1 or bottom - top < 1:
            return None
        return element

    def _locate_once(self) -> _Hit | None:
        window = self.ventana._find() if self.ventana else None
        if window is not None:
            try:
                element = self._uia(window)
            except uia.UiaUnavailable:
                element = None
            if element is not None:
                return _Hit(uia.rect_of(element), "uia", element)
        if self.imagen:
            settings = _settings()
            confidence = self.confianza if self.confianza is not None else settings.confidence
            rect = None
            if window is not None:
                rect = _find_image(self.imagen, window.rect, confidence, settings.grayscale)
            if rect is None:
                rect = _find_image(self.imagen, None, confidence, settings.grayscale)
            if rect is not None:
                return _Hit(rect, "imagen")
        return None

    def localizar(self, tiempo_maximo: float | None = None) -> _Hit:
        timeout = _timeout(tiempo_maximo)
        poll = max(0.1, _settings().poll)
        deadline = time.monotonic() + timeout
        if self.ventana is not None:
            try:
                self.ventana.esperar(timeout)
            except ElementoNoEncontrado:
                if not self.imagen:
                    raise
        while True:
            hit = self._locate_once()
            if hit is not None:
                return hit
            if time.monotonic() >= deadline:
                break
            _sleep(min(poll, max(0.0, deadline - time.monotonic())))
        if self.desde_ventana and self.ventana is not None and self.ventana.existe():
            left, top, _right, _bottom = self.ventana.rect
            x, y = left + int(self.desde_ventana[0]), top + int(self.desde_ventana[1])
            return _Hit((x, y, x + 1, y + 1), "coordenadas")
        if _current.get() is not None:
            screen._diagnostic("elemento-no-encontrado")
        where = f" en la ventana {self.ventana.descripcion}" if self.ventana else ""
        raise ElementoNoEncontrado(f"No se encontró {self.descripcion}{where} en {timeout:g}s")

    def _act(self, verb: str, tiempo_maximo: float | None) -> _Hit:
        hit = self.localizar(tiempo_maximo)
        if self.ventana is not None and self.ventana.existe():
            self.ventana._ensure_front()
        note = "" if hit.via == "uia" else f" (por {hit.via})"
        _log(f"ui: {verb} {self.descripcion}{note}")
        return hit

    # -- queries
    def existe(self, tiempo_maximo: float = 0) -> bool:
        try:
            self.localizar(tiempo_maximo)
            return True
        except ElementoNoEncontrado:
            return False

    def esperar(self, tiempo_maximo: float | None = None) -> Elemento:
        """Espera a que el elemento aparezca y devuelve el propio elemento."""
        hit = self.localizar(tiempo_maximo)
        _log(f"ui: visible {self.descripcion}" + ("" if hit.via == "uia" else f" (por {hit.via})"))
        return self

    def esperar_desaparecer(self, tiempo_maximo: float | None = None) -> None:
        timeout = _timeout(tiempo_maximo)
        deadline = time.monotonic() + timeout
        while self._locate_once() is not None:
            if time.monotonic() >= deadline:
                raise ElementoNoEncontrado(f"{self.descripcion} seguía visible tras {timeout:g}s")
            _sleep(_settings().poll)

    def texto(self, tiempo_maximo: float | None = None) -> str:
        """Texto del control (valor de un campo o su nombre); vacío si se localizó por imagen."""
        hit = self.localizar(tiempo_maximo)
        return uia.element_value(hit.element) if hit.element is not None else ""

    def rectangulo(self, tiempo_maximo: float | None = None) -> tuple[int, int, int, int]:
        return self.localizar(tiempo_maximo).rect

    # -- actions
    def clic(self, *, boton: str = "izquierdo", veces: int = 1, con: list[str] | tuple[str, ...] = (),
             tiempo_maximo: float | None = None) -> Elemento:
        """Clic (o `veces` clics) en el elemento; `con` mantiene teclas pulsadas (p. ej. ["ctrl"])."""
        if boton not in _BUTTONS:
            raise ValueError("boton debe ser izquierdo, derecho o central")
        if isinstance(veces, bool) or not isinstance(veces, int) or not 1 <= veces <= 3:
            raise ValueError("veces debe ser 1, 2 o 3")
        verb = {1: "clic", 2: "doble clic", 3: "triple clic"}[veces] + ("" if boton == "izquierdo" else f" {boton}")
        hit = self._act(verb, tiempo_maximo)
        x, y = hit.point(self.punto)
        gui = _gui()
        for key in con:
            gui.keyDown(key)
        try:
            gui.click(x, y, clicks=veces, interval=0.08, button=_BUTTONS[boton])
        finally:
            for key in reversed(list(con)):
                gui.keyUp(key)
        return self

    def doble_clic(self, *, tiempo_maximo: float | None = None) -> Elemento:
        return self.clic(veces=2, tiempo_maximo=tiempo_maximo)

    def clic_derecho(self, *, tiempo_maximo: float | None = None) -> Elemento:
        return self.clic(boton="derecho", tiempo_maximo=tiempo_maximo)

    def mover(self, *, tiempo_maximo: float | None = None) -> Elemento:
        hit = self._act("mover a", tiempo_maximo)
        _gui().moveTo(*hit.point(self.punto), duration=0.15)
        return self

    def escribir(self, texto: object, *, limpiar: bool = False, tiempo_maximo: float | None = None) -> Elemento:
        """Enfoca el control (clic) y escribe; `limpiar` borra antes su contenido."""
        hit = self._act("escribir en", tiempo_maximo)
        focused = False
        if hit.element is not None:
            try:
                hit.element.element.SetFocus()
                focused = True
            except Exception:
                focused = False
        if not focused:
            _gui().click(*hit.point(self.punto))
        _sleep(0.05)
        if limpiar:
            atajo("ctrl", "a")
            _press_key("delete")
        escribir(texto)
        return self

    def scroll(self, pasos: int, *, tiempo_maximo: float | None = None) -> Elemento:
        """Rueda del ratón sobre el elemento (negativo = hacia abajo)."""
        if isinstance(pasos, bool) or not isinstance(pasos, int) or not -500 <= pasos <= 500:
            raise ValueError("pasos debe ser un entero entre -500 y 500")
        hit = self._act("scroll en", tiempo_maximo)
        x, y = hit.point(self.punto)
        scroll(pasos, en=(x, y))
        return self

    def arrastrar_a(self, destino: Elemento | tuple[int, int], *, duracion: float = 0.5,
                    tiempo_maximo: float | None = None) -> Elemento:
        """Arrastra desde este elemento hasta otro elemento o un punto (x, y) de pantalla."""
        start = self._act("arrastrar", tiempo_maximo).point(self.punto)
        if isinstance(destino, Elemento):
            end = destino.localizar(tiempo_maximo).point(destino.punto)
        else:
            end = (int(destino[0]), int(destino[1]))
        gui = _gui()
        gui.moveTo(*start)
        gui.mouseDown()
        try:
            gui.moveTo(*end, duration=_number(duracion, "duracion", 0, 10))
        finally:
            gui.mouseUp()
        return self


# ------------------------------------------------------------- functions


def ventana(titulo: str | None = None, *, proceso: str | None = None, clase: str | None = None,
            exacto: bool = False, tiempo_maximo: float | None = None) -> Ventana:
    """Espera a que exista una ventana y la devuelve (no la enfoca: lo hacen las acciones)."""
    return Ventana(titulo, proceso=proceso, clase=clase, exacto=exacto).esperar(tiempo_maximo)


esperar_ventana = ventana


def ventanas() -> list[dict[str, Any]]:
    """Ventanas visibles (título, proceso, clase y rectángulo), la de delante primero."""
    return [
        {"titulo": w.title, "proceso": w.process, "clase": w.class_name, "rect": list(w.rect)}
        for w in uia.top_windows() if w.title
    ]


def imagen(ruta: str, *, confianza: float | None = None, desde_ventana: tuple[int, int] | None = None) -> Elemento:
    """Localizador solo por imagen en cualquier monitor: ui.imagen("assets/boton.png").clic()."""
    return Elemento(None, imagen=ruta, confianza=confianza, desde_ventana=desde_ventana)


def abrir_app(ruta: str, *argumentos: str, carpeta: str | None = None) -> None:
    """Lanza un programa (ruta al .exe, comando del PATH o documento con su app asociada)."""
    if not isinstance(ruta, str) or not ruta.strip():
        raise ValueError("ruta debe ser un texto")
    _log(f"ui: abrir {ruta}")
    target = os.path.expandvars(ruta)
    if not argumentos and not target.lower().endswith(".exe") and os.path.exists(target):
        os.startfile(target)  # type: ignore[attr-defined]  # documents open with their app
        return
    subprocess.Popen([target, *argumentos], cwd=carpeta, close_fds=True)


def escribir(texto: object) -> None:
    """Escribe en el control con el foco (acentos, ñ y saltos de línea incluidos)."""
    text = str(texto)
    if len(text) > 100000:
        raise ValueError("Texto demasiado largo")
    uia.dpi_aware()
    try:
        _send_keys(text)
    except ImportError:  # pragma: no cover - pywinauto missing
        from .keyboard import escribir as pegar

        pegar(text)


def tecla(nombre: str, *, veces: int = 1) -> None:
    """Pulsa una tecla (enter, tab, esc, f5, flechas…) `veces` veces."""
    if isinstance(veces, bool) or not isinstance(veces, int) or not 1 <= veces <= 200:
        raise ValueError("veces debe estar entre 1 y 200")
    _press_key(nombre, veces)


def atajo(*teclas: str) -> None:
    """Combinación de teclas, p. ej. ui.atajo("ctrl", "s")."""
    gui = _gui()
    if not teclas or len(teclas) > 6 or any(key not in gui.KEYBOARD_KEYS for key in teclas):
        raise ValueError("Atajo de teclado no válido")
    gui.hotkey(*teclas)


def scroll(pasos: int, *, en: tuple[int, int] | None = None) -> None:
    """Rueda del ratón en `pasos` muescas (negativo = abajo), opcionalmente sobre (x, y)."""
    if isinstance(pasos, bool) or not isinstance(pasos, int) or not -500 <= pasos <= 500:
        raise ValueError("pasos debe ser un entero entre -500 y 500")
    gui = _gui()
    if en is not None:
        gui.moveTo(int(en[0]), int(en[1]))
    # PyAutoGUI on Windows sends the raw wheel delta; one notch is 120.
    gui.scroll(pasos * 120)
