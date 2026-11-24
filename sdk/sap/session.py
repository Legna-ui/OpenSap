"""Apertura de SAP sin clases, estado global ni configuración externa."""
from pathlib import Path
from uuid import uuid4
import os

from ..runtime.context import _context, MacroError
from ..runtime.control import registrar
from ..web.browser import navegador
from ..desktop import ui

PORTAL_URL = (
    "https://erpportal.gulfstream.com/irj/portal/custom?NavigationTarget="
    "pcd:portal_content/com.gulfaero.fl.gulfstream_content/com.gulfaero.gac.fl.sap_launcher/"
    "com.gulfaero.gac.fl.iviews/com.gulfaero.gac.iv.session_mgr_menu_win&System=ECP100"
)


def abrir_sap(usuario: str, contrasena: str, *, url: str = PORTAL_URL):
    """Inicia sesión en el portal y abre la conexión descargada. Devuelve Path."""
    if os.name != "nt":
        raise MacroError("SAP GUI requiere Windows")
    if not usuario or not contrasena:
        raise ValueError("Falta usuario o contraseña SAP")
    context = _context()
    context._redactions.add(contrasena)
    destino = context.run_dir / "sap-private" / f"{uuid4().hex}.sap"
    destino.parent.mkdir(parents=True, exist_ok=True)
    registrar("SAP: iniciando sesión en el portal")
    with navegador(url=url, visible=False, traza=False) as page:
        page.locator("#logonuidfield").fill(usuario)
        page.locator("#logonpassfield").fill(contrasena)
        with page.expect_download(timeout=120000) as pending:
            page.locator("input[type='submit']").click()
        download = pending.value
        if Path(download.suggested_filename).suffix.lower() != ".sap":
            raise MacroError("El portal no descargó una conexión .sap")
        download.save_as(str(destino))
    os.startfile(str(destino))
    registrar("SAP: conexión abierta")
    return destino


def mostrar_sap(titulo: str = "SAP Easy Access", *, tiempo_maximo: float = 30):
    """Espera SAP, mueve al monitor principal y maximiza. Devuelve ui.Ventana."""
    if os.name != "nt":
        raise MacroError("SAP GUI requiere Windows")
    import ctypes
    from ctypes import wintypes
    ventana = ui.ventana(titulo, tiempo_maximo=tiempo_maximo)
    ventana.enfocar()
    user32 = ctypes.windll.user32
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.ShowWindow(ventana.info.hwnd, 9)
    user32.SetWindowPos(ventana.info.hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0004)
    ventana.maximizar()
    registrar("SAP: ventana enfocada y maximizada")
    return ventana
