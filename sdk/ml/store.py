"""Model registry on disk, shared by the backend, the training worker and the SDK.

Contributors: stdlib only (the downloadable SDK kit does not ship `app/features`). Layout:

    <models>/<id>/<n>/model.joblib + metadata.json     immutable versions (1, 2, 3...)
    <models>/<id>/current.json                         {"version": n}: the one in production

A macro package may carry a snapshot of the versions it uses under
`.workflow/models/<id>/<n>/` (same two files) so exported ZIPs stay reproducible.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

MODEL_ID = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
MODEL_FILE = "model.joblib"
META_FILE = "metadata.json"
CURRENT_FILE = "current.json"
SNAPSHOT_DIR = Path(".workflow") / "models"


class ModelError(RuntimeError):
    """Raised for registry problems; the SDK re-raises it as MacroError."""


def models_dir() -> Path:
    explicit = os.environ.get("MACRO_MODELS_DIR", "").strip()
    if explicit:
        return Path(explicit).resolve()
    data = os.environ.get("MACRO_DATA_DIR", "").strip()
    if data:
        return Path(data).resolve() / "models"
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()).resolve() / "MacroStudio" / "models"


def check_id(model_id: Any) -> str:
    if not isinstance(model_id, str) or not MODEL_ID.fullmatch(model_id):
        raise ModelError("Id de modelo no válido: usa minúsculas, números y guiones (3-64 caracteres)")
    return model_id


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def version_dir(model_id: str, version: int, base: Path | None = None) -> Path:
    return (base or models_dir()) / check_id(model_id) / str(int(version))


def versions(model_id: str, base: Path | None = None) -> list[int]:
    folder = (base or models_dir()) / check_id(model_id)
    if not folder.is_dir():
        return []
    return sorted(int(p.name) for p in folder.iterdir() if p.name.isdigit() and (p / META_FILE).is_file())


def read_meta(model_id: str, version: int, base: Path | None = None) -> dict[str, Any]:
    path = version_dir(model_id, version, base) / META_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ModelError(f"No existe la versión {version} del modelo {model_id}") from error


def current_version(model_id: str, base: Path | None = None) -> int | None:
    """Version in production, or the newest one when none was published explicitly."""
    folder = (base or models_dir()) / check_id(model_id)
    try:
        value = int(json.loads((folder / CURRENT_FILE).read_text(encoding="utf-8"))["version"])
        if (folder / str(value) / META_FILE).is_file():
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    found = versions(model_id, base)
    return found[-1] if found else None


def set_current(model_id: str, version: int, base: Path | None = None) -> None:
    if int(version) not in versions(model_id, base):
        raise ModelError(f"No existe la versión {version} del modelo {model_id}")
    folder = (base or models_dir()) / model_id
    (folder / CURRENT_FILE).write_text(json.dumps({"version": int(version)}), encoding="utf-8")


def new_version(model_id: str, base: Path | None = None) -> tuple[int, Path]:
    """Reserve the next version folder (mkdir is atomic, so parallel trainings never collide)."""
    root = (base or models_dir()) / check_id(model_id)
    root.mkdir(parents=True, exist_ok=True)
    number = (max(versions(model_id, base), default=0)) + 1
    while True:
        folder = root / str(number)
        try:
            folder.mkdir()
            return number, folder
        except FileExistsError:
            number += 1


def snapshot_path(package_dir: Path | None, model_id: str, version: int) -> Path | None:
    if package_dir is None:
        return None
    folder = Path(package_dir) / SNAPSHOT_DIR / model_id / str(version)
    return folder if (folder / META_FILE).is_file() else None


def latest_snapshot(package_dir: Path | None, model_id: str) -> int | None:
    if package_dir is None:
        return None
    root = Path(package_dir) / SNAPSHOT_DIR / model_id
    if not root.is_dir():
        return None
    found = [int(p.name) for p in root.iterdir() if p.name.isdigit() and (p / META_FILE).is_file()]
    return max(found, default=None)


def resolve(model_id: str, version: int | None = None, package_dir: Path | None = None) -> tuple[Path, dict[str, Any]]:
    """Folder and metadata to load: the package snapshot first, then the local registry."""
    check_id(model_id)
    wanted = int(version) if version else None
    if wanted is None:
        wanted = latest_snapshot(package_dir, model_id) or current_version(model_id)
    if wanted is None:
        raise ModelError(f"No existe el modelo «{model_id}». Entrénalo en la sección Modelos.")
    snap = snapshot_path(package_dir, model_id, wanted)
    folder = snap or version_dir(model_id, wanted)
    meta_path = folder / META_FILE
    if not meta_path.is_file():
        raise ModelError(f"No existe la versión {wanted} del modelo «{model_id}»")
    return folder, json.loads(meta_path.read_text(encoding="utf-8"))


def verify(folder: Path, meta: dict[str, Any]) -> Path:
    """The model file, after checking it is the one that was trained (joblib/pickle runs code on load)."""
    path = folder / MODEL_FILE
    if not path.is_file():
        raise ModelError("Falta el archivo del modelo")
    if sha256_file(path) != meta.get("sha256"):
        raise ModelError("El archivo del modelo no coincide con su huella (sha256). No se carga por seguridad.")
    return path


def list_models(base: Path | None = None) -> list[dict[str, Any]]:
    root = base or models_dir()
    if not root.is_dir():
        return []
    items = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and MODEL_ID.fullmatch(p.name)):
        found = versions(folder.name, base)
        if not found:
            continue
        current = current_version(folder.name, base)
        meta = read_meta(folder.name, current or found[-1], base)
        items.append({
            "id": folder.name, "name": meta.get("name", folder.name), "task": meta.get("task"),
            "versions": found, "current": current, "metrics": meta.get("metrics", {}),
            "target": meta.get("target"), "created_at": meta.get("created_at"),
        })
    return items


def delete_version(model_id: str, version: int, base: Path | None = None) -> None:
    folder = version_dir(model_id, version, base)
    if not folder.is_dir():
        raise ModelError(f"No existe la versión {version}")
    shutil.rmtree(folder)
    root = folder.parent
    pointer = root / CURRENT_FILE
    if pointer.is_file() and current_version(model_id, base) != _pointed(pointer):
        pointer.unlink(missing_ok=True)
    if not versions(model_id, base):
        shutil.rmtree(root, ignore_errors=True)


def _pointed(pointer: Path) -> int | None:
    try:
        return int(json.loads(pointer.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
