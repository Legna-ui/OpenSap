"""Named persistent browser profiles and the login fallback.

A profile is a Chromium user-data folder under
`<MACRO_DATA_DIR or %LOCALAPPDATA%\\MacroStudio>/browser-profiles/<nombre>`:
cookies, local storage and saved sessions survive between runs, so several
macros (and the recorder) share one login.

`InicioSesion` describes how to recognise the login page and, optionally, the
credentials to try. When the page still asks for a login after trying them (or
none were given), the browser is shown and the user logs in by hand; the macro
waits and then continues on its own.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def data_dir() -> Path:
    explicit = os.environ.get("MACRO_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MacroStudio"


def profiles_root() -> Path:
    return data_dir() / "browser-profiles"


def profile_dir(nombre: str) -> Path:
    if not isinstance(nombre, str) or not PROFILE_NAME.match(nombre):
        raise ValueError("El perfil debe tener 1-64 letras, números, '.', '_' o '-' (sin espacios)")
    return profiles_root() / nombre


@dataclass
class InicioSesion:
    """Cómo detectar la pantalla de login y cómo intentar entrar.

    - `url_contiene`: texto de la URL de login (p. ej. "/login" o "accounts.google").
    - `selector`: elemento que solo existe en el login (por defecto un campo de contraseña).
    - `usuario` / `contrasena`: credenciales a probar (usa parámetros secretos).
    - `campo_usuario`, `campo_contrasena`, `boton`: selectores; si faltan se adivinan.
    - `exito`: selector que confirma que ya hay sesión (opcional).
    - `tiempo_maximo`: segundos para que la persona inicie sesión a mano (por defecto 5 min).
    """

    url_contiene: str | None = None
    selector: str | None = "input[type=password]"
    usuario: str | None = None
    contrasena: str | None = None
    campo_usuario: str | None = None
    campo_contrasena: str | None = None
    boton: str | None = None
    exito: str | None = None
    tiempo_maximo: float = 300


def inicio_sesion(**opciones) -> InicioSesion:
    """Atajo de `InicioSesion(...)`: navegador(url, perfil="erp", login=inicio_sesion(url_contiene="/login"))."""
    return InicioSesion(**opciones)
