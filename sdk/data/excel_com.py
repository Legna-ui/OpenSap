"""Excel desktop COM. Dedicated instance, explicit save, lazy pywin32 imports."""
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from ..runtime.context import MacroError, _number
from .folders import ruta

def _value(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value

def _records(values, headers):
    if values is None:
        return []
    rows = [list(r) for r in values] if isinstance(values, tuple) else [[values]]
    if not headers:
        return [[_value(v) for v in row] for row in rows]
    names, used = [], set()
    for i, value in enumerate(rows[0], 1):
        base = (str(value).strip() if value is not None else "") or f"columna_{i}"
        name, count = base, 2
        while name in used:
            name = f"{base}_{count}"
            count += 1
        names.append(name)
        used.add(name)
    return [dict(zip(names, [_value(v) for v in row])) for row in rows[1:]]

class LibroExcel:
    def __init__(self, app, book):
        self._app, self._book = app, book

    def _hoja(self, hoja):
        return self._book.Worksheets.Item(hoja + 1 if isinstance(hoja, int) else hoja)

    def hojas(self):
        return [str(s.Name) for s in self._book.Worksheets]

    def consultas(self):
        return [str(q.Name) for q in self._book.Queries]

    def conexiones(self):
        return [str(c.Name) for c in self._book.Connections]

    def tablas(self, hoja=0):
        return [str(t.Name) for t in self._hoja(hoja).ListObjects]

    def leer(self, hoja=0, *, rango=None, tabla=None, encabezados=True, max_celdas=1000000):
        if isinstance(max_celdas, bool) or not isinstance(max_celdas, int) or max_celdas < 1:
            raise ValueError("max_celdas debe ser entero positivo")
        if rango and tabla:
            raise ValueError("Usa rango o tabla, no ambos")
        sheet, cells, table = self._hoja(hoja), None, None
        try:
            if tabla:
                table = sheet.ListObjects.Item(tabla)
                # Exclude totals row; empty tables still have a header.
                cells = table.HeaderRowRange.Resize(1 + table.ListRows.Count, table.ListColumns.Count)
            else:
                cells = sheet.Range(rango) if rango else sheet.UsedRange
            if int(cells.CountLarge) > max_celdas:
                raise ValueError("Rango demasiado grande; indica rango o tabla")
            return _records(cells.Value, encabezados)
        finally:
            cells = table = sheet = None

    def actualizar(self, *, consulta=None, tiempo_maximo=300):
        """consulta is an exact WorkbookConnection name, not a bare Power Query name.

        timeout bounds polling only. Office COM calls can block; hard cancellation
        requires a dedicated worker process. Validate refreshed content separately.
        """
        import pythoncom
        timeout = _number(tiempo_maximo, "tiempo_maximo", 1, 86400)
        deadline = time.monotonic() + timeout
        connection = conn = sheet = query = None
        try:
            if consulta is None:
                self._book.RefreshAll()
            else:
                connection = self._book.Connections.Item(consulta)
                connection.Refresh()
            self._app.CalculateUntilAsyncQueriesDone()
            while True:
                active = self._app.CalculationState != 0
                for conn in self._book.Connections:
                    for kind in ("OLEDBConnection", "ODBCConnection"):
                        try:
                            active = bool(getattr(conn, kind).Refreshing) or active
                        except Exception:
                            pass
                for sheet in self._book.Worksheets:
                    for query in sheet.QueryTables:
                        active = bool(query.Refreshing) or active
                if not active:
                    break
                if time.monotonic() >= deadline:
                    raise MacroError("Tiempo de espera de actualización superado")
                pythoncom.PumpWaitingMessages()
                time.sleep(0.2)
            self._app.CalculateFull()
            return {"estado": "actualizacion_finalizada", "validar_datos": True}
        finally:
            connection = conn = sheet = query = None

    def guardar(self, destino=None):
        source = Path(self._book.FullName).resolve()
        if destino is None or ruta(destino).resolve() == source:
            if self._book.ReadOnly:
                raise MacroError("Libro de solo lectura; guarda una copia en otro destino")
            self._book.Save()
            return source
        target = ruta(destino).resolve()
        if target.suffix.lower() != source.suffix.lower():
            raise ValueError("Conserva la extensión original")
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._book.SaveCopyAs(str(target))
        return target

@contextmanager
def abrir_excel_com(archivo, *, visible=False, solo_lectura=True):
    """Do not retain/share the wrapper outside this block or across threads.

    VBA disabled, external link updates disabled, explicit query refresh allowed.
    No implicit saving on exit. Only the dedicated Excel instance is closed.
    """
    if sys.platform != "win32":
        raise MacroError("Se requiere Windows y Excel de escritorio instalado")
    path = ruta(archivo).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    app = book = wrapper = None
    try:
        app = win32com.client.DispatchEx("Excel.Application")
        app.Visible = bool(visible)
        app.DisplayAlerts = False
        app.EnableEvents = False
        app.AutomationSecurity = 3
        book = app.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=bool(solo_lectura),
                                  IgnoreReadOnlyRecommended=True, AddToMru=False)
        wrapper = LibroExcel(app, book)
        yield wrapper
    finally:
        if wrapper is not None:
            wrapper._app = wrapper._book = None
        try:
            if book is not None:
                book.Close(SaveChanges=False)
        finally:
            book = None
            try:
                if app is not None:
                    app.Quit()
            finally:
                app = None
                pythoncom.CoUninitialize()

def leer_excel_com(archivo, *, hoja=0, rango=None, tabla=None, encabezados=True, max_celdas=1000000):
    with abrir_excel_com(archivo) as libro:
        return libro.leer(hoja, rango=rango, tabla=tabla, encabezados=encabezados, max_celdas=max_celdas)

def actualizar_excel(archivo, *, consulta=None, tiempo_maximo=300, destino=None, visible=False):
    with abrir_excel_com(archivo, visible=visible, solo_lectura=destino is not None) as libro:
        libro.actualizar(consulta=consulta, tiempo_maximo=tiempo_maximo)
        return libro.guardar(destino)

def actualizar_y_leer_excel(
    archivo,
    *,
    hoja=0,
    rango=None,
    tabla=None,
    consulta=None,
    tiempo_maximo=300,
    guardar=False,
    destino=None,
    visible=False,
    max_celdas=1_000_000,
):
    with abrir_excel_com(
        archivo,
        visible=visible,
        solo_lectura=not guardar or destino is not None,
    ) as libro:
        libro.actualizar(
            consulta=consulta,
            tiempo_maximo=tiempo_maximo,
        )

        data = libro.leer(
            hoja,
            rango=rango,
            tabla=tabla,
            max_celdas=max_celdas,
        )

        if guardar or destino is not None:
            libro.guardar(destino)

        return data

def ultima_fila(self, hoja=0, columna="A"):
    sheet = self._hoja(hoja)

    try:
        xlUp = -4162

        return int(
            sheet.Cells(
                sheet.Rows.Count,
                columna,
            )
            .End(xlUp)
            .Row
        )
    finally:
        sheet = None

def leer_hasta_ultima_fila(
    self,
    hoja=0,
    *,
    columna_final="BD",
    columna_referencia="A",
    encabezados=True,
    max_celdas=5_000_000,
):
    ultima_fila = self.ultima_fila(
        hoja,
        columna_referencia,
    )

    rango = f"A1:{columna_final}{ultima_fila}"

    return self.leer(
        hoja,
        rango=rango,
        encabezados=encabezados,
        max_celdas=max_celdas,
    )