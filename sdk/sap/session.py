"""Apertura de SAP sin clases, estado global ni configuración externa."""
from pathlib import Path
from uuid import uuid4
import os
import time

from ..runtime.context import _context, MacroError
from ..runtime.control import registrar
from ..web.browser import navegador
from ..desktop import ui

PORTAL_URL = (
    "https://erpportal.gulfstream.com/irj/portal/custom?NavigationTarget="
    "pcd:portal_content/com.gulfaero.fl.gulfstream_content/com.gulfaero.gac.fl.sap_launcher/"
    "com.gulfaero.gac.fl.iviews/com.gulfaero.gac.iv.session_mgr_menu_win&System=ECP100"
)


# Login del IdP de Gulfstream (NetIQ) y login clásico del portal SAP.
_CAMPO_USUARIO = "#Ecom_User_ID, #logonuidfield"
_CAMPO_CONTRASENA = "#Ecom_Password, #logonpassfield"
_BOTON_ENTRAR = "#loginButton2, input[type='submit']"


def _visible(page, selector: str) -> bool:
    try:
        loc = page.locator(selector).first
        return loc.count() > 0 and loc.is_visible()
    except Exception:  # la página puede estar navegando
        return False


def _llenar_login(page, usuario: str, contrasena: str) -> None:
    page.locator(_CAMPO_USUARIO).first.fill(usuario)
    page.locator(_CAMPO_CONTRASENA).first.fill(contrasena)
    page.locator(_BOTON_ENTRAR).first.click()


def abrir_sap(usuario: str, contrasena: str, *, url: str = PORTAL_URL, perfil: str = "sap",
              tiempo_maximo: float = 300):
    """Inicia sesión en el portal y abre la conexión descargada. Devuelve Path.

    Abre un navegador visible con un perfil persistente: si el portal pide MFA, complétalo en
    esa ventana (la sesión queda guardada en el perfil para las siguientes ejecuciones).
    """
    if os.name != "nt":
        raise MacroError("SAP GUI requiere Windows")
    if not usuario or not contrasena:
        raise ValueError("Falta usuario o contraseña SAP")
    context = _context()
    context._redactions.add(contrasena)
    destino = context.run_dir / "sap-private" / f"{uuid4().hex}.sap"
    destino.parent.mkdir(parents=True, exist_ok=True)
    registrar("SAP: iniciando sesión en el portal")
    with navegador(url=url, visible=True, traza=False, perfil=perfil) as page:
        descargas = []
        page.on("download", lambda d: descargas.append(d))
        limite = time.monotonic() + tiempo_maximo
        intentos = 0
        aviso_mfa = False
        while not descargas:
            if time.monotonic() > limite:
                raise MacroError(f"El portal no descargó la conexión .sap (página actual: {page.url})")
            # Login del IdP o clásico del portal (puede aparecer más de uno seguido).
            if _visible(page, _CAMPO_USUARIO) and _visible(page, _CAMPO_CONTRASENA):
                if intentos >= 3:
                    raise MacroError("El portal sigue pidiendo usuario y contraseña: revisa las credenciales")
                intentos += 1
                _llenar_login(page, usuario, contrasena)
            elif not aviso_mfa and "login.gulfstream.com" in page.url:
                aviso_mfa = True
                registrar("SAP: el portal pide verificación (MFA). Complétala en la ventana del navegador...")
            page.wait_for_timeout(1000)
        download = descargas[0]
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
