"""Macro Studio SDK 2 facade.

The SDK is grouped by feature; each subpackage owns one concern:

- runtime/   context kernel (MacroContext, Settings, errors), run control
             (configurar, registrar, esperar, repetir, paso...) and parameters
- data/      package resources, run artifacts, JSON/CSV/text, Excel and disk
             files/folders (latest file, wait for downloads, copy/move, ZIP)
- desktop/   PyAutoGUI: screen, image vision, mouse, keyboard and windows;
             `ui` = hybrid UI Automation (pywinauto) + image + coordinates
- web/       Playwright sessions (named persistent profiles + login fallback) and links
- system/    clipboard, shell commands, notifications, dates, downloads and e-mail
- compose/   calls to other macros snapshotted inside the package
- ocr/       Tesseract OCR: text, numbers, regex and text-anchored clicks

Contributors: add a helper in the module of its feature and re-export the
public name here so macros keep using `from macro_sdk import ...` unchanged.
"""

from __future__ import annotations

from .data.excel import columnas_excel, guardar_excel, leer_excel, tabla
from .data.files import archivo_salida, guardar_json, guardar_texto, leer_csv, leer_json, recurso
from .data.folders import (
    carpeta,
    comprimir,
    copiar,
    crear_carpeta,
    descomprimir,
    eliminar,
    escribir_archivo,
    esperar_archivo,
    existe,
    leer_archivo,
    listar_archivos,
    listar_carpetas,
    mover,
    nombre_libre,
    renombrar,
    ruta,
    ultima_carpeta,
    ultimo_archivo,
)
from .desktop import ui
from .desktop.keyboard import atajo, escribir, pulsar, rellenar
from .desktop.mouse import (
    arrastrar_imagen,
    clic,
    clic_derecho,
    clic_en,
    clic_si_aparece,
    desplazar,
    doble_clic,
    mover_a_imagen,
)
from .desktop.screen import captura
from .desktop.ui import ElementoNoEncontrado
from .desktop.vision import (
    asegurar_imagen,
    buscar_imagen,
    esperar_cualquiera,
    esperar_desaparecer,
    esperar_imagen,
    existe_imagen,
)
from .desktop.windows import VentanaNoEncontrada, enfocar_ventana, listar_ventanas
from .runtime.context import (
    ImageTimeout,
    MacroContext,
    MacroError,
    Match,
    Settings,
    __version__,  # noqa: F401  (re-exported via * import in the macro_sdk shim)
    _current,  # noqa: F401  (contextvar used by tests and advanced tooling)
    activar_contexto,
)
from .runtime.control import configurar, esperar, parametro, paso, registrar, repetir, resultado, secreto
from .runtime.params import validate_parameters  # noqa: F401  (re-export for advanced workflows)
from .system.network import descargar, enviar_correo
from .system.tools import (
    abrir_archivo,
    abrir_programa,
    copiar_portapapeles,
    ejecutar_comando,
    es_dia_habil,
    fecha,
    fin_de_mes,
    leer_portapapeles,
    notificar,
)
from .runtime.parallel import en_paralelo
from .web.browser import en_login, expect, navegador, web_captura, web_clic, web_escribir, web_esperar
from .web.links import abrir_link
from .web.profiles import InicioSesion, inicio_sesion

# Alias naturales y compatibilidad con macros previas.
click_imagen = clic
esperar_foto = esperar_imagen
click_foto = clic
abrir_url = abrir_link
escribir_texto = escribir
presionar = pulsar

__all__ = [
    "MacroContext", "MacroError", "ImageTimeout", "VentanaNoEncontrada", "ElementoNoEncontrado", "Match", "Settings",
    "activar_contexto", "configurar", "registrar", "parametro", "secreto",
    "recurso", "archivo_salida", "resultado", "esperar", "abrir_link",
    "leer_json", "leer_csv", "guardar_json", "guardar_texto", "paso", "repetir",
    "captura", "buscar_imagen", "existe_imagen", "esperar_imagen",
    "esperar_desaparecer", "esperar_cualquiera", "clic", "clic_en", "doble_clic",
    "clic_derecho", "clic_si_aparece", "mover_a_imagen", "arrastrar_imagen",
    "pulsar", "atajo", "escribir", "rellenar", "desplazar", "asegurar_imagen",
    "enfocar_ventana", "listar_ventanas", "ui",
    "navegador", "web_clic", "web_escribir", "web_esperar", "web_captura",
    "InicioSesion", "inicio_sesion", "en_login", "expect",
    "carpeta", "ruta", "listar_archivos", "listar_carpetas", "ultimo_archivo", "ultima_carpeta",
    "esperar_archivo", "crear_carpeta", "nombre_libre", "copiar", "mover", "renombrar", "eliminar",
    "comprimir", "descomprimir", "leer_archivo", "escribir_archivo", "existe",
    "copiar_portapapeles", "leer_portapapeles", "ejecutar_comando", "abrir_archivo", "abrir_programa", "notificar",
    "fecha", "es_dia_habil", "fin_de_mes", "descargar", "enviar_correo",
    "leer_excel", "guardar_excel", "columnas_excel", "tabla",
    "ejecutar_macro","en_paralelo",
    "click_imagen", "esperar_foto", "click_foto", "abrir_url", "escribir_texto", "presionar",
]

from .outlook import (
    enviar_correo_outlook, leer_correos, ultimo_correo, obtener_correo,
    marcar_correo_leido, listar_carpetas_outlook, descargar_adjuntos,
)
from .data.excel_com import (
    abrir_excel_com, leer_excel_com, actualizar_excel, actualizar_y_leer_excel,ultima_fila,leer_hasta_ultima_fila
)
__all__ += [
    "enviar_correo_outlook", "leer_correos", "ultimo_correo", "obtener_correo",
    "marcar_correo_leido", "listar_carpetas_outlook", "descargar_adjuntos",
    "abrir_excel_com", "leer_excel_com", "actualizar_excel", "actualizar_y_leer_excel",'ultima_fila','leer_hasta_ultima_fila'
]
from .sap import abrir_sap, mostrar_sap
__all__ += ["abrir_sap", "mostrar_sap"]
from .ml import modelo_entrenar, modelo_info, modelo_predecir, modelos_listar
__all__ += ["modelo_predecir", "modelo_entrenar", "modelo_info", "modelos_listar"]
from .ocr import (  # noqa: E402, I001  (optional integrations are appended after __all__)
    extraer_numeros, ocr_buscar, ocr_clic, ocr_disponible, ocr_esperar_texto, ocr_extraer, ocr_lineas,
    ocr_numero, ocr_numeros, ocr_palabras, ocr_texto,
)
__all__ += [
    "ocr_texto", "ocr_lineas", "ocr_numeros", "ocr_numero", "ocr_extraer", "ocr_palabras",
    "ocr_buscar", "ocr_esperar_texto", "ocr_clic", "ocr_disponible", "extraer_numeros",
]


def ejecutar_macro(macro_id: str, revision: str | None = None, *, parametros=None):
    """Ejecuta una macro importada y devuelve su resultado JSON.

    Sin `revision` usa la mas reciente incluida en el paquete (se actualiza al guardar).
    """
    from .compose.runner import ejecutar_macro as execute

    return execute(macro_id, revision, parametros=parametros)  # type: ignore[misc]

from .app import app_puerto, app_url, en_macro_studio, servir_app

__all__ += ["app_puerto", "app_url", "en_macro_studio", "servir_app"]
