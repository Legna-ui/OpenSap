"""Lazy COM access for Outlook classic; never quit the user's Outlook."""
import sys
from contextlib import contextmanager
from ..runtime.context import MacroError

@contextmanager
def session():
    if sys.platform != "win32":
        raise MacroError("Se requiere Windows y Outlook clásico configurado")
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    app = ns = None
    try:
        app = win32com.client.Dispatch("Outlook.Application")
        ns = app.GetNamespace("MAPI")
        yield app, ns
    finally:
        app = ns = None
        pythoncom.CoUninitialize()

FOLDERS = {"entrada": 6, "enviados": 5, "borradores": 16, "eliminados": 3,
           "salida": 4, "correo_no_deseado": 23}

def folder(ns, name="entrada", mailbox=None):
    root = ns.Folders.Item(mailbox) if mailbox else ns.GetDefaultFolder(6).Parent
    if name in FOLDERS:
        return root.Store.GetDefaultFolder(FOLDERS[name])
    for part in name.replace(chr(92), "/").split("/"):
        if part:
            root = root.Folders.Item(part)
    return root

def smtp(entry):
    if entry is None:
        return ""
    if str(entry.Type).upper() != "EX":
        return str(entry.Address or "")
    for method in ("GetExchangeUser", "GetExchangeDistributionList"):
        try:
            obj = getattr(entry, method)()
            if obj and obj.PrimarySmtpAddress:
                return str(obj.PrimarySmtpAddress)
        except Exception:
            pass
    try:
        return str(entry.PropertyAccessor.GetProperty("http://schemas.microsoft.com/mapi/proptag/0x39FE001E"))
    except Exception:
        return ""

def get_item(ns, id, store=None):
    mail = ns.GetItemFromID(id, store) if store else ns.GetItemFromID(id)
    if mail.Class != 43:
        raise ValueError("El elemento no es un correo MailItem")
    return mail

def integer(value, name, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} debe ser entero entre 1 y {maximum}")
