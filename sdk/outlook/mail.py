"""Mail filters: AND between fields, OR within each list of people."""
from datetime import date, datetime, time
from ..data.folders import ruta
from .core import session, folder, smtp, get_item, integer

def _people(value):
    values = [] if value is None else ([value] if isinstance(value, str) else list(value))
    if any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError("Personas debe contener nombres o emails no vacíos")
    return [v.strip().casefold() for v in values]

def _matches(filters, persons):
    return not filters or any((f == email.casefold() if "@" in f else f in name.casefold())
                              for f in filters for name, email in persons)

def _date(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        return value.astimezone().replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    raise ValueError("Usa fecha ISO, date o datetime")

def _recipients(mail):
    return [{"nombre": str(r.Name or ""), "email": smtp(r.AddressEntry), "tipo": int(r.Type)}
            for r in mail.Recipients]

def _serialize(mail, cuerpo=True, html=False):
    data = {"id": str(mail.EntryID), "almacen_id": str(mail.Parent.StoreID),
            "asunto": str(mail.Subject or ""), "remitente": str(mail.SenderName or ""),
            "email_remitente": smtp(mail.Sender), "destinatarios": _recipients(mail),
            "recibido": mail.ReceivedTime.isoformat(), "enviado": mail.SentOn.isoformat(),
            "no_leido": bool(mail.UnRead), "cantidad_adjuntos": int(mail.Attachments.Count),
            "adjuntos": [{"indice": i, "nombre": str(mail.Attachments.Item(i).FileName),
                          "tamano": int(mail.Attachments.Item(i).Size)}
                         for i in range(1, mail.Attachments.Count + 1)]}
    if cuerpo:
        data["cuerpo"] = str(mail.Body or "")
    if html:
        data["html"] = str(mail.HTMLBody or "")
    return data

def leer_correos(*, carpeta="entrada", buzon=None, personas=None, destinatarios=None,
                 asunto=None, texto=None, desde=None, hasta=None, no_leidos=None,
                 con_adjuntos=None, limite=100, max_examinar=5000, cuerpo=True, html=False,
                 fecha_por="recibido"):
    """Return correos/examinados/completo. Does not mark messages as read.

    personas: senders; destinatarios: To/CC/BCC. Email exact, name substring.
    desde inclusive, hasta exclusive; naive dates use local Windows time.
    A bounded scan, not a server-side search. completo=False signals truncation.
    """
    integer(limite, "limite", 10000)
    integer(max_examinar, "max_examinar", 1000000)
    if fecha_por not in ("recibido", "enviado"):
        raise ValueError("fecha_por debe ser recibido o enviado")
    for value in (no_leidos, con_adjuntos):
        if value is not None and not isinstance(value, bool):
            raise ValueError("no_leidos y con_adjuntos deben ser bool o None")
    senders, recipients = _people(personas), _people(destinatarios)
    start, end = _date(desde), _date(hasta)
    if start and end and start >= end:
        raise ValueError("desde debe ser anterior a hasta")
    found, examined = [], 0
    with session() as (_, ns):
        items = folder(ns, carpeta, buzon).Items
        mail = None
        try:
            items.Sort("[SentOn]" if fecha_por == "enviado" else "[ReceivedTime]", True)
            total = int(items.Count)
            for index in range(1, min(total, max_examinar) + 1):
                mail = items.Item(index)
                examined += 1
                if mail.Class != 43:
                    continue
                stamp = _date(mail.SentOn if fecha_por == "enviado" else mail.ReceivedTime)
                if (start and stamp < start) or (end and stamp >= end):
                    continue
                if no_leidos is not None and bool(mail.UnRead) != no_leidos:
                    continue
                if con_adjuntos is not None and bool(mail.Attachments.Count) != con_adjuntos:
                    continue
                if asunto is not None and asunto.casefold() not in str(mail.Subject or "").casefold():
                    continue
                if not _matches(senders, [(str(mail.SenderName or ""), smtp(mail.Sender))]):
                    continue
                if recipients and not _matches(recipients, [(r["nombre"], r["email"]) for r in _recipients(mail)]):
                    continue
                if texto is not None and texto.casefold() not in str(mail.Body or "").casefold():
                    continue
                found.append(_serialize(mail, cuerpo, html))
                if len(found) >= limite:
                    break
        finally:
            mail = items = None
    return {"correos": found, "examinados": examined, "completo": examined >= total}

def ultimo_correo(persona, **opciones):
    opciones.pop("limite", None)
    opciones.pop("personas", None)
    result = leer_correos(personas=persona, limite=1, **opciones)
    if not result["correos"] and not result["completo"]:
        raise RuntimeError("Sin coincidencia en el rango examinado; aumenta max_examinar")
    return result["correos"][0] if result["correos"] else None

def obtener_correo(correo_id, *, almacen_id=None, cuerpo=True, html=False):
    with session() as (_, ns):
        mail = get_item(ns, correo_id, almacen_id)
        try:
            return _serialize(mail, cuerpo, html)
        finally:
            mail = None

def enviar_correo_outlook(para, asunto, cuerpo="", *, cc=None, cco=None, adjuntos=(),
                          html=False, cuenta=None, modo="borrador"):
    """modo: borrador (default), mostrar or enviar. No implicit signature.

    cuenta: exact SMTP address of a configured Outlook Account.
    Sending hands the mail to Outlook; it does not confirm delivery.
    """
    if modo not in ("borrador", "mostrar", "enviar"):
        raise ValueError("modo debe ser borrador, mostrar o enviar")
    def addresses(value):
        return [] if value is None else ([value] if isinstance(value, str) else list(value))
    groups = [(1, addresses(para)), (2, addresses(cc)), (3, addresses(cco))]
    if not groups[0][1]:
        raise ValueError("Falta el destinatario")
    paths = [ruta(p).resolve() for p in adjuntos]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    with session() as (app, ns):
        mail = app.CreateItem(0)
        recipient = accounts = None
        try:
            if cuenta:
                accounts = [a for a in ns.Accounts if str(a.SmtpAddress).casefold() == cuenta.casefold()]
                if len(accounts) != 1:
                    raise ValueError("Cuenta SMTP no encontrada o ambigua")
                mail.SendUsingAccount = accounts[0]
            for kind, values in groups:
                for value in values:
                    recipient = mail.Recipients.Add(str(value))
                    recipient.Type = kind
            if not mail.Recipients.ResolveAll():
                raise ValueError("Hay destinatarios sin resolver")
            mail.Subject = str(asunto)
            if html:
                mail.HTMLBody = str(cuerpo)
            else:
                mail.Body = str(cuerpo)
            for path in paths:
                mail.Attachments.Add(str(path))
            mail.Save()
            result = {"id": str(mail.EntryID), "almacen_id": str(mail.Parent.StoreID), "estado": modo}
            if modo == "enviar":
                mail.Send()
                result = {"id": None, "almacen_id": None, "estado": "entregado_a_outlook"}
            elif modo == "mostrar":
                mail.Display(False)
            return result
        finally:
            recipient = accounts = mail = None

def marcar_correo_leido(correo_id, *, almacen_id=None, leido=True):
    if not isinstance(leido, bool):
        raise ValueError("leido debe ser bool")
    with session() as (_, ns):
        mail = get_item(ns, correo_id, almacen_id)
        try:
            mail.UnRead = not leido
            mail.Save()
        finally:
            mail = None

def listar_carpetas_outlook(*, profundidad=3):
    integer(profundidad, "profundidad", 10)
    result = []
    def walk(current, level):
        result.append({"nombre": str(current.Name), "ruta": str(current.FolderPath),
                       "id": str(current.EntryID), "almacen_id": str(current.StoreID)})
        if level < profundidad:
            for child in current.Folders:
                walk(child, level + 1)
    with session() as (_, ns):
        for store in ns.Folders:
            walk(store, 0)
        store = None
    return result
