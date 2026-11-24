"""Excel helpers built on pandas/openpyxl."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .files import archivo_salida, recurso


def leer_excel(nombre: str, *, hoja: str | int = 0, indice: int = 0) -> list[dict[str, Any]]:
    """Lee un archivo Excel y devuelve los datos como lista de diccionarios.

    Args:
        nombre: Nombre del archivo en la sección de recursos.
        hoja: Nombre o índice de la hoja (default: 0).
        indice: Índice de la fila a iniciar (default: 0, usa encabezados).

    Returns:
        Lista de diccionarios con los datos de cada fila.

    Example:
        from macro_sdk import leer_excel
        def run(context):
            datos = leer_excel('datos.xlsx')
            for fila in datos:
                registrar(f"{fila['Nombre']} - {fila['Edad']}")
    """
    path = recurso(nombre)
    import pandas as pd

    df = pd.read_excel(path, sheet_name=hoja, index_col=indice)
    return df.to_dict(orient="records")


def guardar_excel(nombre: str, datos: list[dict[str, Any]] | dict[str, list[Any]], *, hoja: str = "Sheet1") -> Path:
    """Guarda datos en un archivo Excel.

    Args:
        nombre: Nombre del archivo de salida.
        datos: Datos para guardar, puede ser lista de diccionarios o dict con listas.
        hoja: Nombre de la hoja (default: 'Sheet1').

    Returns:
        Path del archivo creado.

    Example:
        from macro_sdk import guardar_excel
        def run(context):
            datos = [{'Nombre': 'Juan', 'Edad': 30}, {'Nombre': 'Maria', 'Edad': 25}]
            guardar_excel('resultados.xlsx', datos)
    """
    path = archivo_salida(nombre)
    import pandas as pd

    df = pd.DataFrame(datos)
    df.to_excel(path, sheet_name=hoja, index=False)
    return path


def columnas_excel(nombre: str, *, hoja: str | int = 0) -> list[str]:
    """Obtiene los nombres de las columnas de un archivo Excel.

    Args:
        nombre: Nombre del archivo en recursos.
        hoja: Nombre o índice de la hoja (default: 0).

    Returns:
        Lista de nombres de columnas.

    Example:
        from macro_sdk import columnas_excel
        def run(context):
            cols = columnas_excel('datos.xlsx')
            registrar(f"Columnas: {cols}")
    """
    path = recurso(nombre)
    import pandas as pd

    df = pd.read_excel(path, sheet_name=hoja)
    return list(df.columns)


def tabla(filas: Any, columnas: list[str] | None = None):
    """Convierte las filas de un parametro de tipo dataframe en un DataFrame de pandas.

    Args:
        filas: Lista de diccionarios (como la que llega en `context.parameters`, o la que
            devuelve `leer_excel`) o un diccionario de listas.
        columnas: Orden/seleccion de columnas. Si falta, se usan las de las filas.

    Returns:
        pandas.DataFrame con las columnas pedidas (vacio pero con columnas si no hay filas).

    Example:
        from macro_sdk import parametro, tabla
        def run(context):
            df = tabla(parametro("pedidos"))
            registrar(df["importe"].astype(float).sum())
    """
    import pandas as pd

    if hasattr(filas, "to_dict") and not isinstance(filas, dict):
        return filas if columnas is None else filas[columnas]
    return pd.DataFrame(filas or [], columns=columnas)
