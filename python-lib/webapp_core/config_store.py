"""Persistence for the webapp.

* App settings + per-category recipes live as JSON in a **dedicated config managed
  folder**. Its id comes from the project variable ``WEBAPP_CONFIG_FOLDER`` (or the
  ``WEBAPP_CONFIG_FOLDER`` env var / the ``CONFIG_FOLDER_FALLBACK`` constant below).
* Category input / mapping / output / template files live in the four managed folders
  the admin wires to each category.

When ``dataiku`` is unavailable (local tests) everything degrades to a local directory
tree rooted at ``$WEBAPP_LOCAL_ROOT`` (default: ``./_webapp_local``).
"""

from __future__ import annotations

import io
import json
import os
import string
from typing import Any, Dict, List, Optional

from . import auth

CONFIG_FOLDER_FALLBACK = ""  # set to a managed-folder id to hard-wire it
SETTINGS_FILE = "app_settings.json"
N_DEFAULT_CATEGORIES = 9

try:  # pragma: no cover - exercised only inside Dataiku
    import dataiku
    _HAS_DATAIKU = True
except Exception:  # noqa: BLE001
    dataiku = None
    _HAS_DATAIKU = False


# --------------------------------------------------------------------------- backends
class _LocalFolder:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _p(self, path: str) -> str:
        return os.path.join(self.root, path.lstrip("/"))

    def list_paths(self) -> List[str]:
        out = []
        for base, _, files in os.walk(self.root):
            for f in files:
                out.append("/" + os.path.relpath(os.path.join(base, f), self.root).replace("\\", "/"))
        return out

    def read_bytes(self, path: str) -> bytes:
        with open(self._p(path), "rb") as fh:
            return fh.read()

    def write_bytes(self, path: str, data: bytes) -> None:
        full = self._p(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)

    def delete(self, path: str) -> None:
        try:
            os.remove(self._p(path))
        except FileNotFoundError:
            pass


class _DkuFolder:  # pragma: no cover - exercised only inside Dataiku
    def __init__(self, folder_id: str):
        self.f = dataiku.Folder(folder_id)

    def list_paths(self) -> List[str]:
        return self.f.list_paths_in_partition()

    def read_bytes(self, path: str) -> bytes:
        with self.f.get_download_stream(path) as s:
            return s.read()

    def write_bytes(self, path: str, data: bytes) -> None:
        self.f.upload_stream(path, io.BytesIO(data))

    def delete(self, path: str) -> None:
        try:
            self.f.delete_path(path)
        except Exception:  # noqa: BLE001
            pass


def get_folder(folder_id: str):
    if _HAS_DATAIKU and folder_id:
        return _DkuFolder(folder_id)
    root = os.environ.get("WEBAPP_LOCAL_ROOT", os.path.abspath("./_webapp_local"))
    return _LocalFolder(os.path.join(root, folder_id or "config"))


def _config_folder_id() -> str:
    if _HAS_DATAIKU:  # pragma: no cover
        try:
            var = dataiku.get_custom_variables().get("WEBAPP_CONFIG_FOLDER")
            if var:
                return var
        except Exception:  # noqa: BLE001
            pass
    return os.environ.get("WEBAPP_CONFIG_FOLDER") or CONFIG_FOLDER_FALLBACK or "config"


def _config_folder():
    return get_folder(_config_folder_id())


# --------------------------------------------------------------------------- JSON IO
def _read_json(folder, name: str, default: Any = None) -> Any:
    try:
        return json.loads(folder.read_bytes("/" + name))
    except Exception:  # noqa: BLE001
        return default


def _write_json(folder, name: str, obj: Any) -> None:
    folder.write_bytes("/" + name, json.dumps(obj, indent=2, default=str).encode("utf-8"))


# --------------------------------------------------------------------------- settings
def default_settings() -> Dict[str, Any]:
    letters = list(string.ascii_uppercase[:N_DEFAULT_CATEGORIES])
    return {
        "admin": auth.default_admin(),
        "secret": auth.new_secret(),
        "categories": [{"id": c, "name": c, "order": i} for i, c in enumerate(letters)],
    }


def load_settings() -> Dict[str, Any]:
    folder = _config_folder()
    s = _read_json(folder, SETTINGS_FILE)
    if not s:
        s = default_settings()
        _write_json(folder, SETTINGS_FILE, s)
    return s


def save_settings(settings: Dict[str, Any]) -> None:
    _write_json(_config_folder(), SETTINGS_FILE, settings)


# --------------------------------------------------------------------------- recipes
def _recipe_name(cat_id: str) -> str:
    safe = "".join(ch for ch in cat_id if ch.isalnum() or ch in "-_")
    return f"category_{safe}.json"


def default_recipe(cat_id: str, name: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": cat_id, "name": name or cat_id,
        "folders": {"input": "", "mapping": "", "output": "", "template": ""},
        "data_sheet": "", "header_row": 0,
        "canonical_schema": [],
        "computed_columns": [],
        "lookups": [],
        "lookup_tables": {},
        "toggles": [],
        "comparison": [],
        "comparison_keys": [],
        "configured": False,
    }


def load_recipe(cat_id: str) -> Dict[str, Any]:
    r = _read_json(_config_folder(), _recipe_name(cat_id))
    if not r:
        settings = load_settings()
        name = next((c["name"] for c in settings["categories"] if c["id"] == cat_id), cat_id)
        return default_recipe(cat_id, name)
    base = default_recipe(cat_id)
    base.update(r)
    return base


def save_recipe(cat_id: str, recipe: Dict[str, Any]) -> None:
    recipe = dict(recipe)
    recipe["id"] = cat_id
    _write_json(_config_folder(), _recipe_name(cat_id), recipe)


def recipe_is_configured(recipe: Dict[str, Any]) -> bool:
    f = recipe.get("folders", {})
    return bool(
        recipe.get("canonical_schema")
        and recipe.get("computed_columns")
        and all(f.get(k) for k in ("input", "output"))
    )


# ------------------------------------------------------------------ managed folders
def list_project_managed_folders() -> List[Dict[str, str]]:
    if not _HAS_DATAIKU:
        return []
    try:  # pragma: no cover
        client = dataiku.api_client()
        project = client.get_project(dataiku.default_project_key())
        return [
            {"id": mf["id"], "name": mf.get("name", mf["id"])}
            for mf in project.list_managed_folders()
        ]
    except Exception:  # noqa: BLE001  # pragma: no cover
        return []


def folder_ok(folder_id: str) -> bool:
    if not folder_id:
        return False
    if not _HAS_DATAIKU:
        return True
    try:  # pragma: no cover
        get_folder(folder_id).list_paths()
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------- category file ops
def list_files(folder_id: str, subdir: str = "") -> List[Dict[str, Any]]:
    if not folder_id:
        return []
    folder = get_folder(folder_id)
    out = []
    for p in folder.list_paths():
        rel = p.lstrip("/")
        if subdir and not rel.startswith(subdir.strip("/") + "/"):
            continue
        if not subdir and "/" in rel and rel.split("/")[0] == "_cache":
            continue
        out.append({"path": p, "name": rel.split("/")[-1]})
    return sorted(out, key=lambda d: d["name"].lower())


def read_file(folder_id: str, path: str) -> bytes:
    return get_folder(folder_id).read_bytes(path)


def write_file(folder_id: str, path: str, data: bytes) -> None:
    get_folder(folder_id).write_bytes(path, data)


def delete_file(folder_id: str, path: str) -> None:
    get_folder(folder_id).delete(path)


def clear_folder(folder_id: str, keep_prefixes: Optional[List[str]] = None) -> int:
    folder = get_folder(folder_id)
    n = 0
    for p in folder.list_paths():
        if keep_prefixes and any(p.lstrip("/").startswith(k) for k in keep_prefixes):
            continue
        folder.delete(p)
        n += 1
    return n
