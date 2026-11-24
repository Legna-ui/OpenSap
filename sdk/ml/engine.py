"""scikit-learn engine: dataset reading, training pipelines, persistence and prediction.

Contributors: every heavy import (pandas, sklearn, joblib) stays inside functions so
`import macro_sdk` works without the optional ML module. Used by the training worker
(`backend/ml_worker.py`), by the SDK (`modelo_*`) and by the backend prediction endpoint.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import store
from .store import ModelError

MAX_ROWS = 200_000
MAX_DATASET_BYTES = 50 * 1024 * 1024
MAX_CATEGORIES = 30

ALGORITHMS: dict[str, dict[str, dict[str, Any]]] = {
    "classification": {
        "logistic": {"label": "Regresión logística", "params": ["C", "max_iter"]},
        "random_forest": {"label": "Bosque aleatorio", "params": ["n_estimators", "max_depth"]},
        "gradient_boosting": {"label": "Gradient boosting", "params": ["n_estimators", "learning_rate", "max_depth"]},
        "knn": {"label": "Vecinos cercanos (KNN)", "params": ["n_neighbors"]},
        "decision_tree": {"label": "Árbol de decisión", "params": ["max_depth"]},
    },
    "regression": {
        "linear": {"label": "Regresión lineal", "params": []},
        "ridge": {"label": "Ridge", "params": ["alpha"]},
        "random_forest": {"label": "Bosque aleatorio", "params": ["n_estimators", "max_depth"]},
        "gradient_boosting": {"label": "Gradient boosting", "params": ["n_estimators", "learning_rate", "max_depth"]},
    },
}
DEFAULT_ALGORITHM = {"classification": "random_forest", "regression": "random_forest"}
INTEGER_PARAMS = {"n_estimators", "max_depth", "n_neighbors", "max_iter"}
PARAM_RANGE = {
    "C": (1e-4, 1e4), "max_iter": (10, 10_000), "n_estimators": (5, 1000), "max_depth": (1, 50),
    "learning_rate": (1e-3, 1.0), "n_neighbors": (1, 200), "alpha": (1e-6, 1e4),
}

_CACHE: dict[tuple[str, str], Any] = {}


def available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("sklearn", "joblib", "pandas"))


def versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in ("sklearn", "joblib"):
        try:
            result[name] = __import__(name).__version__
        except Exception:
            result[name] = None
    return result


def require() -> None:
    if not available():
        raise ModelError(
            "El módulo de Machine Learning no está instalado. Reinstala Macro Studio marcando "
            "«Módulo de aprendizaje automático» o copia el addon sobre la instalación."
        )


def algorithm_catalog() -> dict[str, Any]:
    return {task: {key: {"label": spec["label"], "params": spec["params"]} for key, spec in items.items()}
            for task, items in ALGORITHMS.items()}


# ------------------------------------------------------------------ data


def read_dataset(path: str | Path):
    """CSV (any separator) or XLSX into a DataFrame, bounded in size and rows."""
    require()
    import pandas as pd

    path = Path(path)
    if not path.is_file():
        raise ModelError(f"No existe el archivo de datos: {path}")
    if path.stat().st_size > MAX_DATASET_BYTES:
        raise ModelError("El archivo de datos supera 50 MB")
    suffix = path.suffix.lower()
    try:
        if suffix in (".xlsx", ".xlsm", ".xls"):
            frame = pd.read_excel(path)
        else:
            frame = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
    except Exception as error:
        raise ModelError(f"No se pudo leer el archivo de datos: {error}") from error
    if len(frame) > MAX_ROWS:
        raise ModelError(f"Máximo {MAX_ROWS} filas")
    frame.columns = [str(c).strip() for c in frame.columns]
    return frame


def to_frame(datos: Any):
    """DataFrame from a DataFrame, list of dicts, dict of lists or a single dict (one row)."""
    require()
    import pandas as pd

    if hasattr(datos, "to_dict") and not isinstance(datos, dict):
        return datos.copy()
    if isinstance(datos, dict):
        if datos and all(isinstance(v, (list, tuple)) for v in datos.values()):
            return pd.DataFrame(datos)
        return pd.DataFrame([datos])
    if isinstance(datos, (list, tuple)):
        return pd.DataFrame(list(datos))
    raise ModelError("datos debe ser un DataFrame, una lista de diccionarios o un diccionario")


def column_kind(series) -> str:
    import pandas as pd

    if pd.api.types.is_bool_dtype(series):
        return "category"
    if pd.api.types.is_numeric_dtype(series):
        return "number"
    return "category" if series.nunique(dropna=True) <= 50 else "text"


def describe(frame) -> list[dict[str, Any]]:
    columns = []
    for name in frame.columns:
        series = frame[name]
        kind = column_kind(series)
        columns.append({
            "name": name, "kind": kind, "nulls": int(series.isna().sum()), "unique": int(series.nunique(dropna=True)),
            "examples": [None if v != v else (v.item() if hasattr(v, "item") else v)
                         for v in series.dropna().head(3).tolist()],
        })
    return columns


def detect_task(series) -> str:
    import pandas as pd

    clean = series.dropna()
    if pd.api.types.is_bool_dtype(clean) or not pd.api.types.is_numeric_dtype(clean):
        return "classification"
    unique = clean.nunique()
    if unique <= 20 and (pd.api.types.is_integer_dtype(clean) or unique <= 10):
        return "classification"
    return "regression"


# ------------------------------------------------------------- training


def _clean_params(algorithm_spec: dict[str, Any], params: dict[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in (params or {}).items():
        if value in (None, ""):
            continue
        if key not in algorithm_spec["params"]:
            raise ModelError(f"Parámetro no permitido para este algoritmo: {key}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ModelError(f"El parámetro {key} debe ser numérico")
        low, high = PARAM_RANGE[key]
        if not low <= value <= high:
            raise ModelError(f"El parámetro {key} debe estar entre {low} y {high}")
        clean[key] = int(value) if key in INTEGER_PARAMS else float(value)
    return clean


def _estimator(task: str, algorithm: str, params: dict[str, Any], seed: int):
    from sklearn.ensemble import (
        GradientBoostingClassifier,
        GradientBoostingRegressor,
        RandomForestClassifier,
        RandomForestRegressor,
    )
    from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.tree import DecisionTreeClassifier

    jobs = max(1, (os.cpu_count() or 2) - 1)
    if task == "classification":
        return {
            "logistic": lambda: LogisticRegression(max_iter=params.pop("max_iter", 1000), **params),
            "random_forest": lambda: RandomForestClassifier(random_state=seed, n_jobs=jobs, **params),
            "gradient_boosting": lambda: GradientBoostingClassifier(random_state=seed, **params),
            "knn": lambda: KNeighborsClassifier(n_jobs=jobs, **params),
            "decision_tree": lambda: DecisionTreeClassifier(random_state=seed, **params),
        }[algorithm]()
    return {
        "linear": lambda: LinearRegression(),
        "ridge": lambda: Ridge(**params),
        "random_forest": lambda: RandomForestRegressor(random_state=seed, n_jobs=jobs, **params),
        "gradient_boosting": lambda: GradientBoostingRegressor(random_state=seed, **params),
    }[algorithm]()


def build_pipeline(frame, features: list[str], task: str, algorithm: str, params: dict[str, Any] | None, seed: int):
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    spec = ALGORITHMS[task].get(algorithm)
    if spec is None:
        raise ModelError(f"Algoritmo no disponible para {task}: {algorithm}")
    numeric = [c for c in features if column_kind(frame[c]) == "number"]
    categorical = [c for c in features if c not in numeric]
    transformers = []
    if numeric:
        transformers.append(("num", Pipeline([("fill", SimpleImputer(strategy="median")),
                                              ("scale", StandardScaler())]), numeric))
    if categorical:
        transformers.append(("cat", Pipeline([
            ("fill", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="infrequent_if_exist", max_categories=50)),
        ]), categorical))
    estimator = _estimator(task, algorithm, _clean_params(spec, params), seed)
    return Pipeline([("prep", ColumnTransformer(transformers)), ("model", estimator)])


def _prepare(frame, features: list[str]):
    """Features as numbers or text so the imputers never see mixed types."""
    import pandas as pd

    prepared = frame[features].copy()
    for name in features:
        if column_kind(prepared[name]) == "number":
            prepared[name] = pd.to_numeric(prepared[name], errors="coerce")
        else:
            prepared[name] = prepared[name].astype("string").astype(object).where(prepared[name].notna(), None)
    return prepared


def train(
    frame, target: str, features: list[str] | None = None, *, task: str = "auto", algorithm: str = "auto",
    params: dict[str, Any] | None = None, test_size: float = 0.2, cv: int = 0, seed: int = 42,
    log: Callable[[str], None] = print,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Fit a pipeline; returns (pipeline refit on all rows, metrics, metadata fragment)."""
    require()
    import numpy as np
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import (
        accuracy_score,
        confusion_matrix,
        f1_score,
        mean_absolute_error,
        mean_squared_error,
        precision_score,
        r2_score,
        recall_score,
    )
    from sklearn.model_selection import cross_val_score, train_test_split

    if target not in frame.columns:
        raise ModelError(f"La columna objetivo «{target}» no existe")
    names = [c for c in (features or [c for c in frame.columns if c != target]) if c != target]
    missing = [c for c in names if c not in frame.columns]
    if missing or not names:
        raise ModelError(f"Variables no válidas: {missing or 'elige al menos una'}")
    data = frame.dropna(subset=[target]).reset_index(drop=True)
    if len(data) < 10:
        raise ModelError("Se necesitan al menos 10 filas con valor en la columna objetivo")
    task = detect_task(data[target]) if task == "auto" else task
    if task not in ALGORITHMS:
        raise ModelError("La tarea debe ser classification o regression")
    algorithm = DEFAULT_ALGORITHM[task] if algorithm == "auto" else algorithm
    x_all = _prepare(data, names)
    y_all = data[target].astype(str) if task == "classification" else data[target].astype(float)
    log(f"Datos: {len(data)} filas · {len(names)} variables · tarea: {task} · algoritmo: {algorithm}")

    stratify = None
    if task == "classification":
        counts = y_all.value_counts()
        if len(counts) < 2:
            raise ModelError("El objetivo necesita al menos dos clases distintas")
        stratify = y_all if counts.min() >= 2 else None
    x_train, x_test, y_train, y_test = train_test_split(
        x_all, y_all, test_size=test_size, random_state=seed, stratify=stratify)
    model = build_pipeline(data, names, task, algorithm, params, seed)
    log(f"Entrenando con {len(x_train)} filas y evaluando con {len(x_test)}…")
    model.fit(x_train, y_train)
    predicted = model.predict(x_test)

    metrics: dict[str, Any] = {"train_rows": int(len(x_train)), "test_rows": int(len(x_test))}
    if task == "classification":
        labels = sorted(y_all.unique().tolist())
        metrics.update(
            accuracy=float(accuracy_score(y_test, predicted)),
            f1_macro=float(f1_score(y_test, predicted, average="macro", zero_division=0)),
            precision_macro=float(precision_score(y_test, predicted, average="macro", zero_division=0)),
            recall_macro=float(recall_score(y_test, predicted, average="macro", zero_division=0)),
            baseline_accuracy=float(y_all.value_counts(normalize=True).max()),
            labels=labels, confusion=confusion_matrix(y_test, predicted, labels=labels).tolist(),
        )
        scoring, main = "accuracy", "accuracy"
    else:
        rmse = float(np.sqrt(mean_squared_error(y_test, predicted)))
        metrics.update(
            r2=float(r2_score(y_test, predicted)), mae=float(mean_absolute_error(y_test, predicted)), rmse=rmse,
            baseline_mae=float(mean_absolute_error(y_test, np.full(len(y_test), y_train.mean()))),
        )
        scoring, main = "r2", "r2"
    log(f"Resultado en datos de prueba: {main} = {metrics[main]:.4f}")

    if cv and cv >= 2:
        scores = cross_val_score(build_pipeline(data, names, task, algorithm, params, seed), x_all, y_all,
                                 cv=min(cv, len(data) // 2), scoring=scoring)
        metrics["cv_mean"], metrics["cv_std"] = float(scores.mean()), float(scores.std())
        log(f"Validación cruzada ({len(scores)} pliegues): {scores.mean():.4f} ± {scores.std():.4f}")
    try:
        limit = min(len(x_test), 2000)
        result = permutation_importance(model, x_test.iloc[:limit], y_test.iloc[:limit], n_repeats=5,
                                        random_state=seed, scoring=scoring)
        metrics["importance"] = sorted(
            ({"name": n, "value": float(v)} for n, v in zip(names, result.importances_mean, strict=True)),
            key=lambda item: -item["value"])
    except Exception as error:
        log(f"No se calculó la importancia de variables: {error}")

    log("Reajustando el modelo con todos los datos…")
    final = build_pipeline(data, names, task, algorithm, params, seed).fit(x_all, y_all)
    inputs = []
    for name in names:
        kind = column_kind(data[name])
        entry: dict[str, Any] = {"name": name, "kind": kind}
        if kind == "number":
            numbers = data[name].dropna().astype(float)
            entry.update(min=float(numbers.min()) if len(numbers) else None,
                         max=float(numbers.max()) if len(numbers) else None)
        else:
            values = data[name].dropna().astype(str).value_counts().head(MAX_CATEGORIES).index.tolist()
            entry["categories"] = values
        inputs.append(entry)
    fragment = {
        "task": task, "algorithm": algorithm, "target": target, "inputs": inputs, "rows": int(len(data)),
        "classes": sorted(y_all.unique().tolist()) if task == "classification" else None,
        "params": _clean_params(ALGORITHMS[task][algorithm], params), "refit_all": True,
    }
    return final, metrics, fragment


def save_version(model_id: str, name: str, pipeline, metrics: dict[str, Any], fragment: dict[str, Any],
                 source: str = "") -> tuple[int, dict[str, Any]]:
    """Persist a trained pipeline as the next immutable version of `model_id`."""
    require()
    import joblib

    number, folder = store.new_version(model_id)
    try:
        joblib.dump(pipeline, folder / store.MODEL_FILE, compress=3)
        meta = {
            **fragment, "id": model_id, "name": name or model_id, "version": number, "metrics": metrics,
            "sklearn": versions()["sklearn"], "python": ".".join(map(str, sys.version_info[:3])),
            "source": source, "created_at": datetime.now(UTC).isoformat(),
            "sha256": store.sha256_file(folder / store.MODEL_FILE),
        }
        (folder / store.META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except BaseException:
        import shutil

        shutil.rmtree(folder, ignore_errors=True)
        raise
    return number, meta


# ------------------------------------------------------------ prediction


def load(folder: Path, meta: dict[str, Any]):
    require()
    import joblib

    path = store.verify(folder, meta)
    key = (str(path), meta["sha256"])
    if key not in _CACHE:
        trained = meta.get("sklearn")
        current = versions()["sklearn"]
        if trained and current and trained.split(".")[:2] != current.split(".")[:2]:
            print(f"Aviso: el modelo se entrenó con scikit-learn {trained} y se carga con {current}.", flush=True)
        _CACHE[key] = joblib.load(path)
    return _CACHE[key]


def predict(pipeline, meta: dict[str, Any], datos: Any, probabilidades: bool = False):
    import pandas as pd

    single = isinstance(datos, dict) and not all(isinstance(v, (list, tuple)) for v in datos.values() if datos)
    frame = to_frame(datos)
    columns = [item["name"] for item in meta["inputs"]]
    if not any(c in frame.columns for c in columns):
        raise ModelError(f"Los datos no tienen ninguna de las columnas del modelo: {columns}")
    prepared = pd.DataFrame({c: frame[c] if c in frame.columns else None for c in columns})
    for item in meta["inputs"]:
        if item["kind"] == "number":
            prepared[item["name"]] = pd.to_numeric(prepared[item["name"]], errors="coerce")
        else:
            col = prepared[item["name"]]
            prepared[item["name"]] = col.astype("string").astype(object).where(col.notna(), None)
    output = pipeline.predict(prepared)
    values = [v.item() if hasattr(v, "item") else v for v in output]
    if meta.get("task") == "regression":
        values = [float(v) for v in values]
    if probabilidades and meta.get("task") == "classification" and hasattr(pipeline, "predict_proba"):
        classes = [str(c) for c in pipeline.classes_]
        probs = pipeline.predict_proba(prepared)
        values = [{"prediccion": v, "probabilidades": {c: float(p) for c, p in zip(classes, row, strict=True)}}
                  for v, row in zip(values, probs, strict=True)]
    return values[0] if single else values
