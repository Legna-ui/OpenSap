"""Parameter validation shared by API, executor, editor and composition.

Keep this module dependency-free so the SDK and backend share the same rules.
Accepts both dictionary definitions and Pydantic models.
"""

from __future__ import annotations

import json
import math
from typing import Any


MAX_TABLE_ROWS = 20000


def _field(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"Constante JSON no permitida: {value}")


def _check_json_value(value: Any, active: set[int]) -> None:
    """Validate native JSON values without silently converting Python types."""
    if value is None or isinstance(value, (str, bool, int)):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON no permite NaN ni infinitos")
        return

    if isinstance(value, (list, dict)):
        identity = id(value)
        if identity in active:
            raise ValueError("JSON no permite referencias circulares")

        active.add(identity)
        try:
            if isinstance(value, dict):
                for key, child in value.items():
                    if not isinstance(key, str):
                        raise ValueError(
                            "Las claves de los objetos JSON deben ser texto"
                        )
                    _check_json_value(child, active)
            else:
                for child in value:
                    _check_json_value(child, active)
        finally:
            active.remove(identity)
        return

    raise ValueError(
        f"Tipo no compatible con JSON: {type(value).__name__}"
    )


def json_value(value: Any, label: str) -> Any:
    """Parse JSON text or validate an already decoded JSON value.

    Strings are interpreted as JSON text. To supply a JSON string scalar,
    include its JSON quotes, for example: '"texto"'.
    """
    if isinstance(value, str):
        try:
            value = json.loads(
                value,
                parse_constant=_reject_json_constant,
            )
        except json.JSONDecodeError as error:
            raise ValueError(
                f"{label}: JSON invalido en linea {error.lineno}, "
                f"columna {error.colno}: {error.msg}"
            ) from error
        except (ValueError, RecursionError) as error:
            raise ValueError(f"{label}: JSON invalido: {error}") from error

    try:
        _check_json_value(value, set())

        # Return an independent JSON-native value so mutable defaults are
        # not shared between executions.
        return json.loads(
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        )
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError(
            f"{label}: valor no compatible con JSON: {error}"
        ) from error


def list_value(value: Any, label: str) -> list[Any]:
    """Accept a native list or JSON text containing an array."""
    parsed = json_value(value, label)

    if not isinstance(parsed, list):
        raise ValueError(
            f"{label}: se requiere una lista o un arreglo JSON, "
            'por ejemplo ["A", "B"]'
        )

    return parsed


def table_rows(
    value: Any,
    columns: list[str],
    label: str,
) -> list[dict]:
    """Normalise a table input to a list of {column: value} rows.

    Accepts a list of dicts, a dict of equal-length lists, or an object
    with to_dict("records"), such as a pandas DataFrame.

    Unknown columns are rejected. Missing columns become None.
    """
    if hasattr(value, "to_dict") and not isinstance(value, dict):
        value = value.to_dict("records")

    if isinstance(value, dict):
        lengths = {
            len(v)
            for v in value.values()
            if isinstance(v, (list, tuple))
        }
        if len(lengths) > 1 or not all(
            isinstance(v, (list, tuple))
            for v in value.values()
        ):
            raise ValueError(
                f"{label}: las columnas deben ser listas del mismo largo"
            )

        size = lengths.pop() if lengths else 0
        value = [
            {k: v[i] for k, v in value.items()}
            for i in range(size)
        ]

    if not isinstance(value, (list, tuple)):
        raise ValueError(
            f"{label}: se requiere una tabla (lista de filas)"
        )

    if len(value) > MAX_TABLE_ROWS:
        raise ValueError(f"{label}: maximo {MAX_TABLE_ROWS} filas")

    rows: list[dict] = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError(
                f"{label}: cada fila debe ser un objeto columna/valor"
            )

        extra = set(row) - set(columns)
        if extra:
            raise ValueError(
                f"{label}: columnas desconocidas {sorted(extra)}"
            )

        clean = {}
        for column in columns:
            cell = row.get(column)
            if isinstance(cell, float) and not math.isfinite(cell):
                cell = None
            clean[column] = cell

        rows.append(clean)

    return rows


def validate_parameters(
    definitions: Any,
    supplied: Any,
) -> dict[str, Any]:
    if not isinstance(supplied, dict):
        raise ValueError("parametros debe ser un diccionario")

    items = list(definitions or [])
    keys = [_field(item, "key") for item in items]

    if set(supplied) - set(keys):
        raise ValueError("Parametros desconocidos")

    result: dict[str, Any] = {}

    for item in items:
        key = _field(item, "key")
        label = _field(item, "label") or key
        kind = _field(item, "type", "string")
        required = bool(_field(item, "required"))
        default = _field(item, "default")
        options = list(_field(item, "options") or [])
        value = supplied.get(key, default)

        if kind == "dataframe":
            empty = value is None or (
                isinstance(value, str) and not value
            )
            value = table_rows(
                [] if empty else value,
                [
                    str(column)
                    for column in (_field(item, "columns") or [])
                ],
                label,
            )
            if required and not value:
                raise ValueError(f"Parametro requerido: {label}")

            result[key] = value
            continue

        if kind in ("list", "json"):
            empty = value is None or (
                isinstance(value, str) and not value.strip()
            )

            if empty:
                if required:
                    raise ValueError(f"Parametro requerido: {label}")

                result[key] = [] if kind == "list" else None
                continue

            if kind == "list":
                value = list_value(value, label)
                if required and not value:
                    raise ValueError(f"Parametro requerido: {label}")
            else:
                value = json_value(value, label)
                if required and value is None:
                    raise ValueError(f"Parametro requerido: {label}")

            result[key] = value
            continue

        # Preserve the existing validation rules for scalar parameters.
        if value is None or value == "":
            if required:
                raise ValueError(f"Parametro requerido: {label}")

        elif kind in ("string", "secret", "select") and not isinstance(
            value, str
        ):
            raise ValueError(f"{label}: se requiere texto")

        elif kind == "number" and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{label}: numero invalido")

        elif kind == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{label}: booleano requerido")

        if (
            kind == "select"
            and value not in (None, "")
            and value not in options
        ):
            raise ValueError(f"Opcion invalida: {label}")

        result[key] = value

    return result