"""HTTP downloads and e-mail (SMTP) without extra dependencies."""

from __future__ import annotations

import mimetypes
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path

from ..data.folders import nombre_libre, ruta
from ..runtime.context import MacroError, _current, _number


def _log(message: str) -> None:
    context = _current.get()
    if context is not None:
        context.log(message)


def descargar(url: str, nombre: str | None = None, *, carpeta: str | None = None,
              tiempo_maximo: float = 120) -> Path:
    """Descarga un archivo por HTTP(S). Sin `carpeta` va a los artefactos de la ejecución
    (o a Descargas fuera de Macro Studio). Devuelve la ruta guardada."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Solo se pueden descargar URLs http(s)")
    filename = nombre or Path(urllib.parse.unquote(parsed.path)).name or "descarga"
    if any(sep in filename for sep in ("/", "\\")):
        raise ValueError("nombre debe ser solo un nombre de archivo")
    if carpeta:
        target = nombre_libre(ruta(carpeta) / filename)
    else:
        context = _current.get()
        target = context.artifact(filename) if context is not None else nombre_libre(ruta("descargas") / filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "MacroStudio/2"})
    timeout = _number(tiempo_maximo, "tiempo_maximo", 1, 3600)
    with urllib.request.urlopen(request, timeout=timeout) as response, target.open("wb") as stream:  # noqa: S310
        while chunk := response.read(1024 * 256):
            stream.write(chunk)
    _log(f"Descargado: {url} → {target.name}")
    return target


def enviar_correo(para: str | list[str], asunto: str, cuerpo: str = "", *, adjuntos: list | tuple = (),
                  html: bool = False, cc: str | list[str] | None = None, remitente: str | None = None,
                  servidor: str | None = None, puerto: int | None = None, usuario: str | None = None,
                  contrasena: str | None = None) -> None:
    """Envía un correo por SMTP (Office 365, Gmail con contraseña de aplicación, servidor propio).

    La configuración se toma de los argumentos o de las variables / .env:
    SMTP_SERVIDOR, SMTP_PUERTO (587 STARTTLS / 465 SSL), SMTP_USUARIO, SMTP_CONTRASENA, SMTP_REMITENTE.
    """
    def secret(name: str) -> str:
        context = _current.get()
        return context.secret(name) if context is not None else os.getenv(name, "")

    host = servidor or secret("SMTP_SERVIDOR")
    if not host:
        raise MacroError("Falta el servidor SMTP: pasa servidor= o define SMTP_SERVIDOR en el .env")
    port = int(puerto or secret("SMTP_PUERTO") or 587)
    user = usuario or secret("SMTP_USUARIO")
    password = contrasena or secret("SMTP_CONTRASENA")
    sender = remitente or secret("SMTP_REMITENTE") or user
    recipients = [para] if isinstance(para, str) else list(para)
    copies = [cc] if isinstance(cc, str) else list(cc or [])
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, ", ".join(recipients), asunto
    if copies:
        message["Cc"] = ", ".join(copies)
    if html:
        message.set_content("Este correo requiere un cliente con HTML.")
        message.add_alternative(cuerpo, subtype="html")
    else:
        message.set_content(cuerpo)
    for attachment in adjuntos:
        path = ruta(attachment)
        kind, _ = mimetypes.guess_type(path.name)
        main, sub = (kind or "application/octet-stream").split("/", 1)
        message.add_attachment(path.read_bytes(), maintype=main, subtype=sub, filename=path.name)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as smtp:
            if user:
                smtp.login(user, password)
            smtp.send_message(message, to_addrs=recipients + copies)
    else:
        with smtplib.SMTP(host, port, timeout=60) as smtp:
            smtp.starttls(context=context)
            if user:
                smtp.login(user, password)
            smtp.send_message(message, to_addrs=recipients + copies)
    _log(f"Correo enviado a {', '.join(recipients)}: {asunto}")
