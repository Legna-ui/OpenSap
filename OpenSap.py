import os
from pathlib import Path

from dotenv import load_dotenv

from sdk import abrir_sap
from sdk.runtime.context import MacroContext, activar_contexto

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

usuario = os.getenv("SAP_USUARIO")
contrasena = os.getenv("SAP_CONTRASENA")
if not usuario or not contrasena:
    raise SystemExit("Define SAP_USUARIO y SAP_CONTRASENA en el archivo .env (ver .env.example)")

contexto = MacroContext(BASE / "runs", {})

with activar_contexto(contexto):
    abrir_sap(usuario, contrasena)
