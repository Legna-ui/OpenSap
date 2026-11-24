"""Machine learning for macros (optional module): train, publish and predict with scikit-learn models.

Models live in the local registry (`store.py`); a macro package carries a snapshot of the versions it
uses (`.workflow/models/`), so exported ZIPs keep predicting the same way.
"""

from __future__ import annotations

from typing import Any

from ..runtime.context import MacroError, _current
from . import engine, store


def _package_dir():
    context = _current.get()
    if context is None:
        return None
    return getattr(context, "_workflow_root", None) or getattr(context, "package_dir", None)


def _log(message: str) -> None:
    context = _current.get()
    if context is not None:
        context.log(message)
    else:
        print(message, flush=True)


def modelo_predecir(modelo: str, datos: Any, *, version: int | None = None, probabilidades: bool = False):
    """Predice con un modelo entrenado en la sección Modelos (o con `modelo_entrenar`).

    Usa la versión en producción (o la incluida en el paquete al guardar la macro); `version` fija otra.
    Las columnas que el modelo no conoce se ignoran y las que falten se tratan como vacías.

    Args:
        modelo: Id del modelo (por ejemplo "clasificador-facturas").
        datos: Un diccionario (una fila), una lista de diccionarios, un diccionario de listas o un DataFrame.
        version: Número de versión concreto. Por defecto, la que está en producción.
        probabilidades: En clasificación devuelve también la probabilidad de cada clase.

    Returns:
        Un valor si `datos` es un diccionario de una fila; una lista de valores en otro caso. Con
        `probabilidades=True` cada valor es {"prediccion": ..., "probabilidades": {clase: p}}.

    Example:
        from macro_sdk import modelo_predecir, registrar
        def run(context):
            categoria = modelo_predecir("clasificador-facturas", {"proveedor": "ACME", "importe": 1250})
            registrar(categoria)
    """
    try:
        engine.require()
        folder, meta = store.resolve(modelo, version, _package_dir())
        return engine.predict(engine.load(folder, meta), meta, datos, probabilidades)
    except store.ModelError as error:
        raise MacroError(str(error)) from error


def modelo_entrenar(
    datos: Any, objetivo: str, *, nombre: str, algoritmo: str = "auto", columnas: list[str] | None = None,
    prueba: float = 0.2, publicar: bool = True,
) -> dict[str, Any]:
    """Entrena un modelo dentro de la macro y lo guarda como una versión nueva (útil para reentrenar a diario).

    Args:
        datos: DataFrame, lista de diccionarios o diccionario de listas con la columna objetivo.
        objetivo: Nombre de la columna que se quiere predecir.
        nombre: Id del modelo (minúsculas, números y guiones, por ejemplo "prediccion-ventas").
        algoritmo: "auto" o uno del catálogo (random_forest, logistic, ridge, ...).
        columnas: Variables de entrada. Por defecto todas menos el objetivo.
        prueba: Fracción de filas reservada para evaluar (0.05 a 0.5).
        publicar: Si es True la nueva versión pasa a producción.

    Returns:
        {"modelo": id, "version": n, "tarea": ..., "metricas": {...}}.

    Example:
        from macro_sdk import leer_excel, modelo_entrenar, registrar
        def run(context):
            filas = leer_excel("ventas.xlsx")
            r = modelo_entrenar(filas, "importe", nombre="prediccion-ventas")
            registrar(r["metricas"])
    """
    try:
        engine.require()
        store.check_id(nombre)
        if not 0.05 <= float(prueba) <= 0.5:
            raise store.ModelError("prueba debe estar entre 0.05 y 0.5")
        frame = engine.to_frame(datos)
        pipeline, metrics, fragment = engine.train(
            frame, objetivo, columnas, algorithm=algoritmo, test_size=float(prueba), log=_log)
        number, meta = engine.save_version(nombre, nombre, pipeline, metrics, fragment, source="macro")
        if publicar:
            store.set_current(nombre, number)
        _log(f"Modelo «{nombre}» guardado como versión {number}")
        return {"modelo": nombre, "version": number, "tarea": meta["task"], "metricas": metrics}
    except store.ModelError as error:
        raise MacroError(str(error)) from error


def modelo_info(modelo: str, *, version: int | None = None) -> dict[str, Any]:
    """Describe un modelo: tarea, columnas de entrada, métricas, versiones y cuál está en producción.

    Args:
        modelo: Id del modelo.
        version: Versión concreta; por defecto la de producción.

    Returns:
        Diccionario con id, nombre, tarea, objetivo, entradas, metricas, version, versiones y produccion.

    Example:
        from macro_sdk import modelo_info, registrar
        def run(context):
            info = modelo_info("clasificador-facturas")
            registrar([c["name"] for c in info["entradas"]])
    """
    try:
        _, meta = store.resolve(modelo, version, _package_dir())
        return {
            "id": modelo, "nombre": meta.get("name", modelo), "tarea": meta.get("task"), "objetivo": meta.get("target"),
            "entradas": meta.get("inputs", []), "metricas": meta.get("metrics", {}), "version": meta.get("version"),
            "versiones": store.versions(modelo), "produccion": store.current_version(modelo),
        }
    except store.ModelError as error:
        raise MacroError(str(error)) from error


def modelos_listar() -> list[dict[str, Any]]:
    """Lista los modelos del registro local con su versión en producción y métricas.

    Returns:
        Lista de diccionarios {id, name, task, versions, current, metrics, target, created_at}.

    Example:
        from macro_sdk import modelos_listar, registrar
        def run(context):
            for m in modelos_listar():
                registrar(m["id"])
    """
    return store.list_models()


__all__ = ["modelo_predecir", "modelo_entrenar", "modelo_info", "modelos_listar"]
