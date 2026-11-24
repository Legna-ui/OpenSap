"""Synchronous child macro calls inside the current executor process.

Not a security sandbox. Trusted Python has the same privileges as the parent
macro. Child packages must be snapshotted under `.workflow/macros/` by the
package save flow (app/features/workflow/service.py) so runs stay reproducible.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import inspect
import json
import os
import re
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from ..runtime.context import MacroContext, MacroError, _context, activar_contexto
from ..runtime.params import validate_parameters

_LOCK = threading.RLock()

_macro_id_pattern = r"[a-z][a-z0-9-]{2,63}"
_revision_pattern = r"[a-f0-9]{32}"


def latest_snapshot(directory: Path) -> Path | None:
    """The newest revision snapshot of one macro under `.workflow/macros/<id>/`."""
    if not directory.is_dir():
        return None
    candidates = [p for p in directory.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    return max(candidates, key=lambda p: ((p / "manifest.json").stat().st_mtime_ns, p.name), default=None)


def ejecutar_macro(macro_id: str, revision: str | None = None, *, parametros: Any = None):
    """Ejecuta una macro importada y devuelve su resultado JSON.

    `revision` es opcional: sin ella se usa la revision mas reciente incluida en el paquete
    (el guardado la fija a la mas alta disponible).
    """
    if revision in ("", "latest", "ultima"):
        revision = None
    if (
        not isinstance(macro_id, str)
        or not re.fullmatch(_macro_id_pattern, macro_id)
        or (revision is not None and (not isinstance(revision, str) or not re.fullmatch(_revision_pattern, revision)))
    ):
        raise ValueError("Referencia de macro no válida")
    parent = _context()
    root = Path(getattr(parent, "_workflow_root", parent.package_dir)).resolve()
    macros_dir = (root / ".workflow" / "macros" / macro_id).resolve()
    package = (macros_dir / revision).resolve() if revision else latest_snapshot(macros_dir)
    if package is None or not package.is_relative_to(root) or not package.is_dir():
        raise FileNotFoundError("Dependencia no incluida. Guarda el paquete desde el editor actualizado.")
    revision = package.name
    stack = tuple(getattr(parent, "_workflow_stack", ()))
    key = f"{macro_id}@{revision}"
    if key in stack or len(stack) >= 16:
        raise MacroError("Recursión circular o profundidad máxima de macros")
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("app"):
        raise MacroError("Una app no se puede llamar como macro hija")
    parameters = validate_parameters(manifest.get("parameters", []), {} if parametros is None else parametros)
    call_id = macro_id + "-" + uuid.uuid4().hex[:12]
    child = MacroContext(parent.run_dir / "children" / call_id, parameters)
    child.package_dir = package
    child.output_dir = parent.output_dir / "macros" / call_id
    child.output_dir.mkdir(parents=True, exist_ok=True)
    child.settings = copy.copy(parent.settings)
    child._workflow_root = root  # noqa: SLF001 - runtime state shared with the parent run
    child._workflow_stack = stack + (key,)
    # Share redactions so values registered by child helpers remain hidden in parent logs.
    child._redactions = parent._redactions
    for definition in manifest.get("parameters", []):
        if definition.get("type") == "secret" and parameters.get(definition["key"]):
            child._redactions.add(str(parameters[definition["key"]]))
    entry = (package / manifest["entrypoint"]).resolve()
    if not entry.is_relative_to(package) or not entry.is_file():
        raise ValueError("Entrypoint inseguro o inexistente")
    parent.log(f"INICIO MACRO: {key}")
    with _LOCK:
        old_path = list(sys.path)
        old_cwd = Path.cwd()
        old_env = dict(os.environ)
        before_modules = dict(sys.modules)
        local_names = {p.stem for p in package.glob("*.py")} | {
            p.name for p in package.iterdir() if p.is_dir() and (p / "__init__.py").is_file()
        }
        local_names.discard("macro_sdk")
        local_names.discard("macro_workflow_runtime")
        try:
            for name in list(sys.modules):
                if name.split(".")[0] in local_names:
                    sys.modules.pop(name, None)
            sys.path.insert(0, str(package))
            os.chdir(package)
            env = package / ".env"
            if env.is_file():
                try:
                    from dotenv import dotenv_values
                except ImportError as error:
                    raise MacroError("Instala python-dotenv para cargar .env de macros importadas") from error
                for secret_key, value in dotenv_values(env).items():
                    if value is not None:
                        os.environ[secret_key] = value
                        if value:
                            child._redactions.add(value)
            module_name = "_workflow_child_" + uuid.uuid4().hex
            spec = importlib.util.spec_from_file_location(module_name, entry)
            if spec is None or spec.loader is None:
                raise MacroError("No se pudo cargar la macro importada")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            with activar_contexto(child):
                spec.loader.exec_module(module)
                value = module.run(child)
                if inspect.isawaitable(value):
                    value = asyncio.run(value)
                result_path = child.run_dir / "result.json"
                if value is not None:
                    child.set_result(value)
                result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else None
            parent.log(f"FIN MACRO: {key}")
            return result
        finally:
            os.chdir(old_cwd)
            os.environ.clear()
            os.environ.update(old_env)
            sys.path[:] = old_path
            for name, module in list(sys.modules.items()):
                location = getattr(module, "__file__", None)
                if name not in before_modules or name.split(".")[0] in local_names:
                    if location:
                        try:
                            if Path(location).resolve().is_relative_to(package):
                                sys.modules.pop(name, None)
                        except (ValueError, OSError):
                            pass
            for name, module in before_modules.items():
                if name.split(".")[0] in local_names:
                    sys.modules[name] = module
