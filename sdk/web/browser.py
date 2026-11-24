"""Browser automation with Playwright. Runs without a desktop session.

`navegador(perfil="erp")` keeps cookies and sessions in a named persistent
profile (see profiles.py); `login=InicioSesion(...)` signs in automatically and,
if the credentials fail, shows the browser so a person can log in by hand.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..data.files import archivo_salida
from ..runtime.context import MacroError, _current, _number
from .profiles import InicioSesion, profile_dir

_USER_FIELDS = (
    "input[autocomplete=username]", "input[type=email]", "input[name*=user i]", "input[id*=user i]",
    "input[name*=email i]", "input[id*=email i]", "input[name*=login i]", "input[type=text]",
)
_BANNER = """
(() => {
  const show = () => {
    if (document.getElementById('macro-studio-login')) return;
    const bar = document.createElement('div');
    bar.id = 'macro-studio-login';
    bar.textContent = 'Macro Studio: inicia sesión en esta ventana. La macro continuará sola cuando entres.';
    Object.assign(bar.style, {position: 'fixed', zIndex: 2147483647, left: '50%', top: '12px',
      transform: 'translateX(-50%)', background: '#111827', color: '#fff', padding: '10px 16px',
      borderRadius: '10px', font: '600 14px system-ui, sans-serif', boxShadow: '0 8px 24px rgba(0,0,0,.25)'});
    document.documentElement.appendChild(bar);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', show); else show();
})();
"""


def _log(message: str) -> None:
    context = _current.get()
    if context is not None:
        context.log(message)
    else:
        print(message, flush=True)


def _artifact(name: str) -> Path | None:
    return archivo_salida(name) if _current.get() is not None else None


class _Session:
    """One Playwright browser context (persistent when a profile is given)."""

    def __init__(self, playwright, *, perfil: str | None, visible: bool, video: bool) -> None:
        options: dict[str, Any] = {}
        if video and _current.get() is not None:
            options["record_video_dir"] = str(archivo_salida("videos/.keep").parent)
        self.browser = None
        if perfil:
            folder = profile_dir(perfil)
            folder.mkdir(parents=True, exist_ok=True)
            try:
                self.context = playwright.chromium.launch_persistent_context(
                    str(folder), headless=not visible, no_viewport=visible, **options)
            except Exception as error:
                if "user data directory is already in use" in str(error).lower() or "lock" in str(error).lower():
                    raise MacroError(f"El perfil '{perfil}' está abierto en otro navegador (¿el grabador?). "
                                     "Ciérralo y vuelve a ejecutar.") from error
                raise
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        else:
            self.browser = playwright.chromium.launch(headless=not visible)
            self.context = self.browser.new_context(**options)
            self.page = self.context.new_page()

    def close(self) -> None:
        try:
            self.context.close()
        finally:
            if self.browser is not None:
                self.browser.close()


import time
from contextlib import contextmanager
from typing import Any, Iterator

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)


def _goto_inteligente(
    page,
    url: str,
    *,
    timeout: float,
    intentos: int = 4,
    espera_base: float = 1.5,
) -> None:
    import time

    from playwright.sync_api import (
        Error as PlaywrightError,
        TimeoutError as PlaywrightTimeoutError,
    )

    ultimo_error = None

    for intento in range(1, intentos + 1):
        try:
            print(
                f"[NAVEGADOR] Cargando página "
                f"({intento}/{intentos})..."
            )

            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout * 1000,
            )

            # Importante para páginas SharePoint antiguas/dinámicas.
            try:
                page.wait_for_load_state(
                    "load",
                    timeout=timeout * 1000,
                )
            except PlaywrightTimeoutError:
                # No es fatal.
                # Algunos SharePoint mantienen requests activos.
                print(
                    "[NAVEGADOR] 'load' tardó demasiado, "
                    "continuando con DOM cargado..."
                )

            print("[NAVEGADOR] Página cargada.")
            return

        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            ultimo_error = exc

            if intento >= intentos:
                raise

            espera = min(
                espera_base * (2 ** (intento - 1)),
                8,
            )

            print(
                f"[NAVEGADOR] Error temporal: {exc}"
            )
            print(
                f"[NAVEGADOR] Reintentando en "
                f"{espera:.1f}s..."
            )

            try:
                page.evaluate("window.stop()")
            except Exception:
                pass

            time.sleep(espera)

    if ultimo_error:
        raise ultimo_error

@contextmanager
def navegador(
    *,
    url: str | None = None,
    visible: bool = True,
    video: bool = False,
    traza: bool = True,
    tiempo_maximo: float = 20,
    perfil: str | None = None,
    login: InicioSesion | None = None,
    reintentos: int = 4,
) -> Iterator[Any]:
    """
    Abre Chromium con Playwright y devuelve Page.

    - perfil:
      Nombre de perfil persistente.

    - login:
      InicioSesion(...).

    - reintentos:
      Número máximo de intentos para cargar la página.
    """
    from playwright.sync_api import sync_playwright

    timeout = _number(
        tiempo_maximo,
        "tiempo_maximo",
        0.1,
        3600,
    )

    reintentos = max(1, min(int(reintentos), 10))

    with sync_playwright() as playwright:
        session = _Session(
            playwright,
            perfil=perfil,
            visible=visible,
            video=video,
        )

        try:
            session.page.set_default_timeout(timeout * 1000)

            if url:
                _goto_inteligente(
                    session.page,
                    url,
                    timeout=timeout,
                    intentos=reintentos,
                )

            if login is not None and url:
                session = _ensure_login(
                    playwright,
                    session,
                    url,
                    login,
                    perfil=perfil,
                    visible=visible,
                    video=video,
                    timeout=timeout,
                )

        except BaseException:
            session.close()
            raise

        page = session.page

        if traza:
            session.context.tracing.start(
                screenshots=True,
                snapshots=True,
                sources=True,
            )

        try:
            yield page

        except Exception:
            shot = _artifact("error-navegador.png")

            if shot is not None:
                try:
                    page.screenshot(
                        path=str(shot),
                        full_page=True,
                    )
                except Exception:
                    pass

            raise

        finally:
            try:
                trace = _artifact("trace.zip")

                if traza and trace is not None:
                    session.context.tracing.stop(
                        path=str(trace)
                    )
            finally:
                session.close()
# ------------------------------------------------------------------- login


def _settle(page: Any) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass


def _visible(page: Any, selector: str | None) -> bool:
    if not selector:
        return False
    try:
        return page.locator(selector).first.is_visible()
    except Exception:
        return False


def en_login(pagina: Any, login: InicioSesion) -> bool:
    """True si la página parece la pantalla de inicio de sesión descrita por `login`."""
    if login.exito and _visible(pagina, login.exito):
        return False
    if login.url_contiene and login.url_contiene.lower() in (pagina.url or "").lower():
        return True
    return _visible(pagina, login.selector)


def _first_visible(page: Any, selectors: tuple[str, ...] | list[str]) -> Any:
    for selector in selectors:
        locator = page.locator(selector)
        try:
            for index in range(min(locator.count(), 5)):
                candidate = locator.nth(index)
                if candidate.is_visible() and candidate.is_editable():
                    return candidate
        except Exception:
            continue
    return None


def _try_credentials(page: Any, login: InicioSesion) -> bool:
    if not (login.usuario and login.contrasena):
        return False
    context = _current.get()
    if context is not None:
        context._redactions.add(str(login.contrasena))
    _log("Inicio de sesión: probando las credenciales")
    password_selector = login.campo_contrasena or "input[type=password]"
    user = page.locator(login.campo_usuario).first if login.campo_usuario else _first_visible(page, _USER_FIELDS)
    if user is not None:
        user.fill(str(login.usuario))
        if not _visible(page, password_selector):  # two-step logins (e-mail → next → password)
            user.press("Enter")
            try:
                page.locator(password_selector).first.wait_for(state="visible", timeout=10000)
            except Exception:
                return False
    password = page.locator(password_selector).first
    try:
        password.fill(str(login.contrasena))
        if login.boton:
            page.locator(login.boton).first.click()
        else:
            password.press("Enter")
    except Exception:
        return False
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        _settle(page)
        if not en_login(page, login):
            return True
        page.wait_for_timeout(500)
    return False


def _wait_for_person(page: Any, login: InicioSesion, url: str) -> None:
    from ..system.tools import notificar

    limit = _number(login.tiempo_maximo, "tiempo_maximo", 10, 86400)
    _log(f"Inicio de sesión manual: inicia sesión en la ventana del navegador (espera hasta {limit:g}s)")
    notificar("Macro Studio necesita que inicies sesión",
              "Entra en la ventana del navegador; la macro continuará sola.")
    try:
        page.context.add_init_script(_BANNER)
        page.evaluate(_BANNER)
    except Exception:
        pass
    try:
        page.bring_to_front()
    except Exception:
        pass
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        page.wait_for_timeout(1000)
        if not en_login(page, login):
            _settle(page)
            if not en_login(page, login):
                _log("Inicio de sesión manual completado")
                return
    raise MacroError(f"No se inició sesión en {limit:g}s")


def _ensure_login(playwright, session: _Session, url: str, login: InicioSesion, *, perfil: str | None,
                  visible: bool, video: bool, timeout: float) -> _Session:
    page = session.page
    _settle(page)
    if not en_login(page, login):
        _log("Sesión activa" + (f" (perfil {perfil})" if perfil else ""))
        return session
    if _try_credentials(page, login):
        _log("Sesión iniciada con las credenciales")
        return session
    if login.usuario and login.contrasena:
        _log("Las credenciales no funcionaron (¿caducaron o piden verificación?): se pide inicio manual")
    if visible:
        _wait_for_person(page, login, url)
        return session
    # Headless: show a real window for the person, then continue headless with the
    # same profile (the session is stored in it). Without a profile, keep the window.
    session.close()
    shown = _Session(playwright, perfil=perfil, visible=True, video=video)
    shown.page.set_default_timeout(timeout * 1000)
    shown.page.goto(url)
    _settle(shown.page)
    if en_login(shown.page, login):
        _wait_for_person(shown.page, login, url)
    if not perfil:
        return shown
    shown.close()
    hidden = _Session(playwright, perfil=perfil, visible=False, video=video)
    hidden.page.set_default_timeout(timeout * 1000)
    hidden.page.goto(url)
    _settle(hidden.page)
    if en_login(hidden.page, login):
        hidden.close()
        raise MacroError("La sesión no se conservó en el perfil tras iniciar sesión a mano")
    return hidden


def expect(objetivo: Any, mensaje: str | None = None) -> Any:
    """`expect` de Playwright (aserciones que esperan): expect(pagina.get_by_text("Hola")).to_be_visible()."""
    from playwright.sync_api import expect as playwright_expect

    return playwright_expect(objetivo, mensaje)


def web_clic(pagina: Any, selector: str) -> None:
    pagina.locator(selector).click()


def web_escribir(pagina: Any, selector: str, texto: object) -> None:
    pagina.locator(selector).fill(str(texto))


def web_esperar(pagina: Any, selector: str, *, estado: str = "visible") -> None:
    if estado not in ("visible", "hidden", "attached", "detached"):
        raise ValueError("Estado web invalido")
    pagina.locator(selector).wait_for(state=estado)


def web_captura(pagina: Any, nombre: str = "navegador.png") -> Path:
    path = archivo_salida(nombre)
    pagina.screenshot(path=str(path), full_page=True)
    return path
