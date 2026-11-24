"""OCR (Tesseract) for macros: read text and numbers from the screen or from image files.

Every function takes the same source arguments:
- `imagen=None` captures the screen (whole, `region` or the region from `configurar`);
  a file name is looked up like `recurso()` (package assets or an absolute path) and a PIL
  image is used as is.
- `region=(x, y, ancho, alto)` or a `Match` (from `esperar_imagen`) crops the source.
Word boxes and matches come back in screen coordinates (or image coordinates for files),
so they work with `clic_en(m.x, m.y)`.
"""

from __future__ import annotations

import re
import time
from typing import Any

from ..desktop import screen
from ..runtime.context import ImageTimeout, Match, _context, _number
from ..runtime.control import esperar, registrar
from . import engine

# Digits with optional ./,/no-break-space separators; a minus only counts when it does not follow a word ("A-5").
_NUMBER = re.compile(r"(?:(?<![\w])[-\u2212])?\d(?:[\d.,\u00a0\u202f]*\d)?")
_NUMERIC_CHARS = "0123456789.,-"


def _words(imagen: Any, region: Any, idioma: str | None, modo: int, escala: float, binarizar: bool):
    image, origin = engine.load(imagen, region)
    prepared = engine.prepare(image, escala, binarizar)
    output = engine.recognize(prepared, idioma=idioma, modo=modo, tsv=True)
    return engine.parse_tsv(output, origin, escala)


def _text(imagen: Any, region: Any, idioma: str | None, modo: int, escala: float, binarizar: bool,
          lista_blanca: str | None = None) -> str:
    image, _ = engine.load(imagen, region)
    prepared = engine.prepare(image, escala, binarizar)
    output = engine.recognize(prepared, idioma=idioma, modo=modo, tsv=False, lista_blanca=lista_blanca)
    lines = [" ".join(line.split()) for line in output.replace("\f", "").splitlines()]
    # Keep paragraph breaks (one empty line) but drop runs of blank lines.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _parse_number(raw: str, decimal: str) -> int | float | None:
    text = raw.replace("\u2212", "-").replace("\u00a0", "").replace("\u202f", "")
    negative = text.startswith("-")
    digits = text.lstrip("-")
    separator = decimal
    if decimal == "auto":
        positions = {sep: digits.rfind(sep) for sep in ".," if sep in digits}
        if len(positions) == 2:
            separator = max(positions, key=positions.get)  # type: ignore[arg-type]
        elif len(positions) == 1:
            sep = next(iter(positions))
            tail = digits.rsplit(sep, 1)[1]
            # "1.234.567" or "1,234" -> thousands; "12,5" or "3.14" -> decimals.
            separator = "" if digits.count(sep) > 1 or len(tail) == 3 else sep
        else:
            separator = ""
    thousands = {".": ",", ",": "."}.get(separator, "")
    if separator == "" and decimal == "auto":
        digits = digits.replace(".", "").replace(",", "")
    else:
        digits = digits.replace(thousands, "") if thousands else digits
        if separator and digits.count(separator) > 1:
            return None
        digits = digits.replace(separator, ".") if separator else digits
    if not re.fullmatch(r"\d+(\.\d+)?", digits):
        return None
    value: int | float = float(digits) if "." in digits else int(digits)
    return -value if negative else value


def extraer_numeros(texto: str, *, decimal: str = "auto") -> list[int | float]:
    """Extrae todos los números de un texto (sirve para cualquier texto, no solo OCR).

    Entiende separadores de miles y decimales en formato español e inglés ("1.234,56", "1,234.56"),
    signo negativo y espacios de miles. En modo "auto", un único separador seguido de exactamente
    3 cifras ("1.234") se toma como miles; usa `decimal="."` o `decimal=","` para forzarlo.

    Args:
        texto: Texto del que sacar los números.
        decimal: "auto", "," o "." (separador decimal).

    Returns:
        Lista de números en orden de aparición: int si no tienen decimales, float si los tienen.

    Example:
        from macro_sdk import extraer_numeros, registrar
        def run(context):
            registrar(extraer_numeros("Total: 1.234,56 EUR (IVA 21%)"))  # [1234.56, 21]
    """
    if not isinstance(texto, str):
        raise ValueError("texto debe ser un str")
    if decimal not in ("auto", ",", "."):
        raise ValueError('decimal debe ser "auto", "," o "."')
    numbers = []
    for found in _NUMBER.finditer(texto):
        value = _parse_number(found.group().strip(), decimal)
        if value is not None:
            numbers.append(value)
    return numbers


def ocr_texto(
    imagen: Any = None,
    *,
    region: Any = None,
    idioma: str | None = None,
    modo: int = 3,
    escala: float = 2,
    binarizar: bool = False,
) -> str:
    """Lee todo el texto de la pantalla, de una región o de un archivo de imagen con Tesseract OCR.

    La imagen se pasa a escala de grises, se amplía (`escala`) y se invierte si el fondo es oscuro
    antes de reconocerla. No mueve el ratón ni toca ventanas.

    Args:
        imagen: None para capturar la pantalla, nombre de recurso/ruta de archivo o imagen PIL.
        region: (x, y, ancho, alto) o un Match (de esperar_imagen) para leer solo esa zona.
        idioma: Códigos de Tesseract unidos por "+" ("spa", "eng", "spa+eng"). Por defecto spa+eng.
        modo: Segmentación de página (PSM) de Tesseract: 3 automática, 6 bloque, 7 una línea, 11 texto disperso.
        escala: Factor de ampliación (1 a 6). 2 suele ir bien para texto de pantalla.
        binarizar: Convierte a blanco y negro puro (útil con fondos con ruido).

    Returns:
        El texto reconocido, con saltos de línea y sin espacios sobrantes.

    Example:
        from macro_sdk import ocr_texto, registrar
        def run(context):
            registrar(ocr_texto(region=(100, 200, 400, 60)))
    """
    text = _text(imagen, region, idioma, modo, escala, binarizar)
    registrar(f"OCR: {len(text)} caracteres leidos")
    return text


def ocr_lineas(
    imagen: Any = None,
    *,
    region: Any = None,
    idioma: str | None = None,
    modo: int = 3,
    escala: float = 2,
    binarizar: bool = False,
) -> list[str]:
    """Lee el texto con OCR y lo devuelve como lista de líneas no vacías.

    Args:
        imagen: None para capturar la pantalla, nombre de recurso/ruta de archivo o imagen PIL.
        region: (x, y, ancho, alto) o un Match para leer solo esa zona.
        idioma: Códigos de Tesseract ("spa", "eng", "spa+eng"). Por defecto spa+eng.
        modo: PSM de Tesseract (3 automática, 6 bloque, 11 texto disperso).
        escala: Factor de ampliación (1 a 6).
        binarizar: Convierte a blanco y negro puro antes de leer.

    Returns:
        Lista de líneas en orden de lectura.

    Example:
        from macro_sdk import ocr_lineas, registrar
        def run(context):
            for linea in ocr_lineas("factura.png"):
                registrar(linea)
    """
    return [line for line in _text(imagen, region, idioma, modo, escala, binarizar).splitlines() if line.strip()]


def ocr_numeros(
    imagen: Any = None,
    *,
    region: Any = None,
    decimal: str = "auto",
    solo_digitos: bool = True,
    modo: int = 6,
    escala: float = 3,
    binarizar: bool = False,
) -> list[int | float]:
    """Lee solo los números de la pantalla, de una región o de una imagen.

    Con `solo_digitos=True` Tesseract solo puede reconocer cifras, puntos, comas y el signo menos,
    lo que evita confusiones típicas (O/0, l/1, S/5). Desactívalo si los números van junto a texto
    que también quieres que se lea bien.

    Args:
        imagen: None para capturar la pantalla, nombre de recurso/ruta de archivo o imagen PIL.
        region: (x, y, ancho, alto) o un Match. Recomendado: una zona pequeña alrededor del número.
        decimal: "auto", "," o "." (ver extraer_numeros).
        solo_digitos: Restringe el reconocimiento a 0-9 . , -
        modo: PSM de Tesseract (6 bloque, 7 una sola línea, 8 una palabra).
        escala: Factor de ampliación (1 a 6). Por defecto 3: los números pequeños mejoran mucho.
        binarizar: Convierte a blanco y negro puro antes de leer.

    Returns:
        Lista de números (int o float) en orden de lectura. Vacía si no hay ninguno.

    Example:
        from macro_sdk import ocr_numeros, registrar
        def run(context):
            importes = ocr_numeros(region=(800, 300, 200, 40))
            registrar(sum(importes))
    """
    whitelist = _NUMERIC_CHARS if solo_digitos else None
    text = _text(imagen, region, "eng" if solo_digitos else None, modo, escala, binarizar, whitelist)
    numbers = extraer_numeros(text, decimal=decimal)
    registrar(f"OCR numeros: {numbers}")
    return numbers


def ocr_numero(
    imagen: Any = None,
    *,
    region: Any = None,
    decimal: str = "auto",
    solo_digitos: bool = True,
    modo: int = 7,
    escala: float = 3,
    binarizar: bool = False,
) -> int | float | None:
    """Lee un único número (el primero que aparezca), por ejemplo un total o un contador.

    Args:
        imagen: None para capturar la pantalla, nombre de recurso/ruta de archivo o imagen PIL.
        region: (x, y, ancho, alto) o un Match con la zona del número.
        decimal: "auto", "," o "." (ver extraer_numeros).
        solo_digitos: Restringe el reconocimiento a 0-9 . , -
        modo: PSM de Tesseract (7 una línea, 8 una palabra, 6 bloque).
        escala: Factor de ampliación (1 a 6).
        binarizar: Convierte a blanco y negro puro antes de leer.

    Returns:
        El número (int o float) o None si no se reconoce ninguno.

    Example:
        from macro_sdk import ocr_numero, registrar
        def run(context):
            total = ocr_numero(region=(900, 640, 160, 30))
            if total is None:
                raise ValueError("No se pudo leer el total")
            registrar(f"Total: {total}")
    """
    numbers = ocr_numeros(
        imagen, region=region, decimal=decimal, solo_digitos=solo_digitos, modo=modo, escala=escala,
        binarizar=binarizar,
    )
    return numbers[0] if numbers else None


def ocr_extraer(
    patron: str,
    imagen: Any = None,
    *,
    region: Any = None,
    idioma: str | None = None,
    modo: int = 3,
    escala: float = 2,
) -> list[str]:
    """Lee el texto con OCR y devuelve lo que coincide con una expresión regular.

    Útil para sacar fechas, correos, referencias o códigos de pedido. Si el patrón tiene un grupo
    devuelve el grupo; con varios grupos, el primero.

    Args:
        patron: Expresión regular de Python (se aplica sin distinguir mayúsculas).
        imagen: None para capturar la pantalla, nombre de recurso/ruta de archivo o imagen PIL.
        region: (x, y, ancho, alto) o un Match.
        idioma: Códigos de Tesseract ("spa", "eng", "spa+eng"). Por defecto spa+eng.
        modo: PSM de Tesseract.
        escala: Factor de ampliación (1 a 6).

    Returns:
        Lista de coincidencias en orden de lectura (vacía si no hay).

    Example:
        from macro_sdk import ocr_extraer, registrar
        def run(context):
            fechas = ocr_extraer(r"\\d{2}/\\d{2}/\\d{4}", "albaran.png")
            pedido = ocr_extraer(r"Pedido[:\\s]+(\\w+)", region=(0, 0, 600, 120))
            registrar(f"{fechas} {pedido}")
    """
    if not isinstance(patron, str) or not patron:
        raise ValueError("patron debe ser una expresion regular no vacia")
    try:
        compiled = re.compile(patron, re.IGNORECASE)
    except re.error as error:
        raise ValueError(f"patron invalido: {error}") from error
    text = _text(imagen, region, idioma, modo, escala, False)
    results = []
    for found in compiled.finditer(text):
        groups = [group for group in found.groups() if group is not None]
        results.append(groups[0] if groups else found.group())
    return results


def ocr_palabras(
    imagen: Any = None,
    *,
    region: Any = None,
    idioma: str | None = None,
    modo: int = 11,
    escala: float = 2,
    confianza_minima: float = 0,
) -> list[dict[str, Any]]:
    """Lee cada palabra con su posición y confianza (para decidir dónde hacer clic o filtrar).

    Args:
        imagen: None para capturar la pantalla, nombre de recurso/ruta de archivo o imagen PIL.
        region: (x, y, ancho, alto) o un Match.
        idioma: Códigos de Tesseract ("spa", "eng", "spa+eng"). Por defecto spa+eng.
        modo: PSM de Tesseract (11 texto disperso, ideal para pantallas; 3 automática).
        escala: Factor de ampliación (1 a 6).
        confianza_minima: Descarta palabras con confianza menor (0 a 100).

    Returns:
        Lista de {"texto", "confianza", "left", "top", "width", "height", "x", "y"} con coordenadas
        de pantalla (o de la imagen si se leyó un archivo); x/y es el centro.

    Example:
        from macro_sdk import ocr_palabras, registrar
        def run(context):
            for palabra in ocr_palabras(confianza_minima=70):
                registrar(f'{palabra["texto"]} en ({palabra["x"]}, {palabra["y"]})')
    """
    _number(confianza_minima, "confianza_minima", 0, 100)
    words = _words(imagen, region, idioma, modo, escala, False)
    return [word.to_dict() for word in words if word.confidence >= confianza_minima]


def ocr_buscar(
    texto: str,
    imagen: Any = None,
    *,
    region: Any = None,
    idioma: str | None = None,
    exacto: bool = False,
    modo: int = 11,
    escala: float = 2,
) -> Match | None:
    """Busca un texto en pantalla (o en una imagen) una sola vez y devuelve su posición.

    La comparación ignora mayúsculas, tildes y espacios repetidos; admite varias palabras seguidas
    de la misma línea.

    Args:
        texto: Texto a buscar ("Guardar", "Aceptar cambios").
        imagen: None para capturar la pantalla, nombre de recurso/ruta de archivo o imagen PIL.
        region: (x, y, ancho, alto) o un Match para buscar solo en esa zona.
        idioma: Códigos de Tesseract ("spa", "eng", "spa+eng"). Por defecto spa+eng.
        exacto: True exige que las palabras coincidan completas; False acepta que el texto esté contenido.
        modo: PSM de Tesseract (11 texto disperso, 3 automática).
        escala: Factor de ampliación (1 a 6).

    Returns:
        Match con la caja del texto (x/y es el centro) o None si no aparece.

    Example:
        from macro_sdk import clic_en, ocr_buscar
        def run(context):
            m = ocr_buscar("Exportar")
            if m:
                clic_en(m.x, m.y)
    """
    return engine.find(_words(imagen, region, idioma, modo, escala, False), texto, exacto)


def ocr_esperar_texto(
    texto: str,
    *,
    region: Any = None,
    tiempo_maximo: float | None = None,
    idioma: str | None = None,
    exacto: bool = False,
    modo: int = 11,
    escala: float = 2,
) -> Match:
    """Espera hasta que un texto aparezca en pantalla y devuelve su posición.

    Args:
        texto: Texto a esperar (sin distinguir mayúsculas ni tildes).
        region: (x, y, ancho, alto) o un Match para vigilar solo esa zona (más rápido y fiable).
        tiempo_maximo: Segundos antes de fallar con ImageTimeout. Por defecto el de configurar().
        idioma: Códigos de Tesseract ("spa", "eng", "spa+eng"). Por defecto spa+eng.
        exacto: True exige palabras completas.
        modo: PSM de Tesseract (11 texto disperso, 3 automática).
        escala: Factor de ampliación (1 a 6).

    Returns:
        Match con la caja del texto encontrado.

    Example:
        from macro_sdk import ocr_esperar_texto, registrar
        def run(context):
            ocr_esperar_texto("Proceso completado", tiempo_maximo=60)
            registrar("Listo")
    """
    settings = _context().settings
    timeout = settings.timeout if tiempo_maximo is None else _number(tiempo_maximo, "tiempo_maximo", 0, 3600)
    deadline = time.monotonic() + timeout
    registrar(f"Esperando texto: {texto!r} ({timeout}s)")
    while True:
        match = ocr_buscar(texto, region=region, idioma=idioma, exacto=exacto, modo=modo, escala=escala)
        if match:
            registrar(f"Texto encontrado: {texto!r} en ({match.x}, {match.y})")
            return match
        if time.monotonic() >= deadline:
            screen._diagnostic("texto-no-encontrado")
            raise ImageTimeout(f"No aparecio el texto {texto!r} en {timeout}s")
        esperar(min(settings.poll, max(0, deadline - time.monotonic())))


def ocr_clic(
    texto: str,
    *,
    region: Any = None,
    tiempo_maximo: float | None = None,
    idioma: str | None = None,
    exacto: bool = False,
    boton: str = "left",
    veces: int = 1,
    desplazamiento: tuple[int, int] = (0, 0),
    despues: float = 0,
) -> Match:
    """Espera un texto en pantalla y hace clic en su centro (botones o enlaces sin imagen).

    Args:
        texto: Texto visible del botón o enlace.
        region: (x, y, ancho, alto) o un Match para buscar solo en esa zona.
        tiempo_maximo: Segundos antes de fallar con ImageTimeout. Por defecto el de configurar().
        idioma: Códigos de Tesseract ("spa", "eng", "spa+eng"). Por defecto spa+eng.
        exacto: True exige palabras completas.
        boton: "left", "right" o "middle".
        veces: Número de clics (1 a 5).
        desplazamiento: (dx, dy) en píxeles respecto al centro del texto.
        despues: Segundos de espera tras el clic.

    Returns:
        Match del texto pulsado.

    Example:
        from macro_sdk import ocr_clic
        def run(context):
            ocr_clic("Aceptar", tiempo_maximo=15, despues=1)
    """
    from ..desktop.mouse import _click_options

    _click_options(boton, veces, despues)
    if (
        not isinstance(desplazamiento, (tuple, list))
        or len(desplazamiento) != 2
        or any(isinstance(v, bool) or not isinstance(v, int) for v in desplazamiento)
    ):
        raise ValueError("desplazamiento requiere dos enteros")
    match = ocr_esperar_texto(texto, region=region, tiempo_maximo=tiempo_maximo, idioma=idioma, exacto=exacto)
    gui = screen._gui()
    x, y = match.x + desplazamiento[0], match.y + desplazamiento[1]
    if not gui.onScreen(x, y):
        raise ValueError("El punto de clic esta fuera de la pantalla principal")
    gui.moveTo(x, y, duration=0)
    gui.click(clicks=veces, interval=0, button=boton)
    registrar(f"Clic {boton} en texto {texto!r} ({x}, {y})")
    esperar(despues)
    return match


def ocr_disponible() -> dict[str, Any]:
    """Indica si Tesseract OCR está disponible y qué idiomas tiene (no lanza errores).

    Returns:
        {"disponible": bool, "ruta": str | None, "version": str | None, "idiomas": [...]} y,
        si no está disponible, "error" con el motivo.

    Example:
        from macro_sdk import ocr_disponible, registrar
        def run(context):
            registrar(ocr_disponible())
    """
    return engine.info()
