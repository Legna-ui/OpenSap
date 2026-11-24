"""Download attachments without opening or executing them."""
import fnmatch
import re
from ..data.files import archivo_salida
from ..data.folders import ruta, nombre_libre
from .core import session, get_item

def _safe_name(value):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")[:180] or "adjunto"
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    return "_" + name if name.split(".")[0].upper() in reserved else name

def descargar_adjuntos(correo_id, *, almacen_id=None, destino=None, patron="*",
                       indices=None, sobrescribir=False, incluir_inline=False):
    """One-based indices. Inline detection uses hidden/content-ID MAPI properties."""
    selected = None if indices is None else set(indices)
    if selected is not None and any(isinstance(i, bool) or not isinstance(i, int) or i < 1 for i in selected):
        raise ValueError("indices debe contener enteros desde 1")
    base = ruta(destino).resolve() if destino is not None else archivo_salida("outlook/.keep").parent.resolve()
    base.mkdir(parents=True, exist_ok=True)
    saved = []
    with session() as (_, ns):
        mail = get_item(ns, correo_id, almacen_id)
        attachment = None
        try:
            if selected and max(selected) > mail.Attachments.Count:
                raise ValueError("Índice de adjunto fuera de rango")
            for index in range(1, mail.Attachments.Count + 1):
                if selected is not None and index not in selected:
                    continue
                attachment = mail.Attachments.Item(index)
                if not fnmatch.fnmatch(str(attachment.FileName).casefold(), patron.casefold()):
                    continue
                inline = False
                for prop in ("0x7FFE000B", "0x3712001F"):
                    try:
                        inline = inline or bool(attachment.PropertyAccessor.GetProperty(
                            "http://schemas.microsoft.com/mapi/proptag/" + prop))
                    except Exception:
                        pass
                if inline and not incluir_inline:
                    continue
                target = base / _safe_name(attachment.FileName)
                if not sobrescribir:
                    target = nombre_libre(target)
                if target.is_symlink() or not target.resolve().is_relative_to(base):
                    raise ValueError("Destino inseguro")
                attachment.SaveAsFile(str(target))
                saved.append(target)
        finally:
            attachment = mail = None
    return saved
