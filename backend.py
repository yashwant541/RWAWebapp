"""Flask backend for the Dataiku "category formula & comparison" webapp.

Paste this into the webapp's **Python** pane. It expects the sibling package
``webapp_core`` to be importable - add ``webapp/python-lib`` to the project libraries
(Project > Libraries > Git/Filesystem) or copy ``python-lib/webapp_core`` into the
project's ``python-lib`` folder.

Deployment checklist is in ``webapp/README.md``.
"""

from __future__ import annotations

import io
import json
import traceback
from datetime import datetime
from functools import wraps

import pandas as pd
from flask import request, jsonify, send_file

from webapp_core import auth, config_store as cs
from webapp_core import sample_parser, compute, compare

# Dataiku provides `app`; fall back to a standalone Flask app for local runs.
try:  # pragma: no cover
    app  # type: ignore  # noqa: F821
except NameError:  # pragma: no cover
    from flask import Flask
    app = Flask(__name__)

CACHE_DIR = "_cache"
COMPUTED_SUFFIX = "__computed.xlsx"


# --------------------------------------------------------------------------- helpers
def _err(msg, code=400):
    return jsonify({"ok": False, "error": str(msg)}), code


def _settings():
    return cs.load_settings()


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Admin-Token", "")
        user = auth.verify_token(token, _settings().get("secret", ""))
        if not user:
            return _err("admin authentication required", 401)
        request.admin_user = user  # type: ignore[attr-defined]
        return fn(*args, **kwargs)

    return wrapper


def _category(cat_id):
    settings = _settings()
    cat = next((c for c in settings["categories"] if c["id"] == cat_id), None)
    return cat


def _recipe_or_404(cat_id):
    if not _category(cat_id):
        return None, _err("unknown category", 404)
    return cs.load_recipe(cat_id), None


def _base_name(path):
    return path.lstrip("/").split("/")[-1]


def _stem(name):
    n = _base_name(name)
    for suf in (COMPUTED_SUFFIX, ".xlsx", ".xls", ".csv", ".txt"):
        if n.lower().endswith(suf):
            return n[: -len(suf)]
    return n


# --------------------------------------------------------------------------- auth API
@app.route("/api/login", methods=["POST"])
def login():
    body = request.get_json(force=True, silent=True) or {}
    settings = _settings()
    adm = settings["admin"]
    if body.get("username") != adm["username"] or not auth.verify_password(
        body.get("password", ""), adm["salt"], adm["hash"]
    ):
        return _err("invalid credentials", 401)
    token = auth.make_token(adm["username"], settings["secret"])
    return jsonify({"ok": True, "token": token, "must_change": adm.get("must_change", False)})


@app.route("/api/admin/change-credentials", methods=["POST"])
@require_admin
def change_credentials():
    body = request.get_json(force=True, silent=True) or {}
    settings = _settings()
    adm = settings["admin"]
    if not auth.verify_password(body.get("current_password", ""), adm["salt"], adm["hash"]):
        return _err("current password is wrong", 403)
    new_pw = body.get("new_password", "")
    if len(new_pw) < 6:
        return _err("new password must be at least 6 characters")
    salt, h = auth.hash_password(new_pw)
    settings["admin"] = {
        "username": body.get("new_username") or adm["username"],
        "salt": salt, "hash": h, "must_change": False,
    }
    cs.save_settings(settings)
    return jsonify({"ok": True})


# --------------------------------------------------------------------- categories API
@app.route("/api/categories", methods=["GET"])
def categories():
    settings = _settings()
    out = []
    for cat in sorted(settings["categories"], key=lambda c: c.get("order", 0)):
        recipe = cs.load_recipe(cat["id"])
        out.append({
            "id": cat["id"], "name": cat["name"],
            "configured": cs.recipe_is_configured(recipe),
            "n_canonical": len(recipe.get("canonical_schema", [])),
            "n_computed": len(recipe.get("computed_columns", [])),
            "folders_wired": all(recipe.get("folders", {}).get(k)
                                 for k in ("input", "mapping", "output", "template")),
        })
    return jsonify({"ok": True, "categories": out})


@app.route("/api/admin/folders", methods=["GET"])
@require_admin
def admin_folders():
    return jsonify({"ok": True, "folders": cs.list_project_managed_folders()})


@app.route("/api/admin/category/<cat_id>", methods=["GET"])
@require_admin
def admin_get_category(cat_id):
    recipe, err = _recipe_or_404(cat_id)
    if err:
        return err
    return jsonify({"ok": True, "recipe": recipe})


@app.route("/api/admin/category/<cat_id>/rename", methods=["POST"])
@require_admin
def admin_rename(cat_id):
    body = request.get_json(force=True, silent=True) or {}
    new_name = (body.get("name") or "").strip()
    if not new_name:
        return _err("name required")
    settings = _settings()
    for cat in settings["categories"]:
        if cat["id"] == cat_id:
            cat["name"] = new_name
            cs.save_settings(settings)
            recipe = cs.load_recipe(cat_id)
            recipe["name"] = new_name
            cs.save_recipe(cat_id, recipe)
            return jsonify({"ok": True})
    return _err("unknown category", 404)


@app.route("/api/admin/category/<cat_id>/folders", methods=["POST"])
@require_admin
def admin_set_folders(cat_id):
    recipe, err = _recipe_or_404(cat_id)
    if err:
        return err
    body = request.get_json(force=True, silent=True) or {}
    folders = body.get("folders", {})
    bad = [k for k, v in folders.items() if v and not cs.folder_ok(v)]
    if bad:
        return _err("folder id not found / not readable: " + ", ".join(bad))
    recipe["folders"] = {k: folders.get(k, recipe["folders"].get(k, ""))
                         for k in ("input", "mapping", "output", "template")}
    cs.save_recipe(cat_id, recipe)
    return jsonify({"ok": True, "recipe": recipe})


@app.route("/api/admin/category/<cat_id>/sample", methods=["POST"])
@require_admin
def admin_upload_sample(cat_id):
    recipe, err = _recipe_or_404(cat_id)
    if err:
        return err
    if "file" not in request.files:
        return _err("no file uploaded")
    f = request.files["file"]
    data = f.read()
    data_sheet = request.form.get("data_sheet") or None
    try:
        analysis = sample_parser.analyze_sample(data, data_sheet=data_sheet)
        tables = compute.lookups_from_sample(analysis, data)
    except Exception as exc:  # noqa: BLE001
        return _err(f"could not parse sample: {exc}")

    # keep the raw sample for re-parsing later
    cs.get_folder(cs._config_folder_id()).write_bytes(f"/samples/{cat_id}.xlsx", data)

    analysis["lookup_tables"] = tables
    analysis["comparison_candidates"] = [c["name"] for c in analysis["computed_columns"]]
    return jsonify({"ok": True, "analysis": analysis, "filename": f.filename})


@app.route("/api/admin/category/<cat_id>/recipe", methods=["POST"])
@require_admin
def admin_save_recipe(cat_id):
    recipe, err = _recipe_or_404(cat_id)
    if err:
        return err
    body = request.get_json(force=True, silent=True) or {}
    incoming = body.get("recipe", {})

    for key in ("data_sheet", "header_row", "canonical_schema", "computed_columns",
                "lookups", "lookup_tables", "toggles", "comparison", "comparison_keys"):
        if key in incoming:
            recipe[key] = incoming[key]
    if "folders" in incoming:
        recipe["folders"].update({k: v for k, v in incoming["folders"].items()})
    recipe["name"] = _category(cat_id)["name"]

    # reference-resolution check
    problems = _check_references(recipe)
    if problems and not body.get("force"):
        return jsonify({"ok": False, "needs_confirmation": True, "problems": problems})

    recipe["configured"] = cs.recipe_is_configured(recipe)
    cs.save_recipe(cat_id, recipe)

    # mirror lookup tables into the mapping folder as CSV (human-readable)
    map_folder = recipe["folders"].get("mapping")
    if map_folder:
        for name, spec in (recipe.get("lookup_tables") or {}).items():
            try:
                df = pd.DataFrame(spec["records"], columns=spec["headers"])
                cs.write_file(map_folder, f"/{name}.csv",
                              df.to_csv(index=False).encode("utf-8"))
            except Exception:  # noqa: BLE001
                pass
    return jsonify({"ok": True, "recipe": recipe, "problems": problems})


def _check_references(recipe):
    canon = {c.lower() for c in recipe.get("canonical_schema", [])}
    comp = {c["name"].lower() for c in recipe.get("computed_columns", [])}
    lookups = set(recipe.get("lookup_tables") or {})
    toggles = {t["name"] for t in recipe.get("toggles", [])}
    problems = []
    for c in recipe.get("computed_columns", []):
        refs = c.get("refs") or sample_parser.extract_refs(c.get("excel_formula", ""))
        for sh in refs.get("sheets", []):
            if sh not in lookups:
                problems.append(f"{c['name']}: lookup sheet '{sh}' is not stored")
        for tok in refs.get("tokens", []):
            tl = tok.lower()
            if tl in canon or tl in comp or tok in lookups or tok in toggles:
                continue
            problems.append(f"{c['name']}: unresolved reference '{tok}' (define a toggle?)")
    for rule in recipe.get("comparison", []):
        for side in ("left", "right"):
            col = (rule.get(side) or "").lower()
            if col and col not in canon and col not in comp:
                problems.append(f"comparison: column '{rule.get(side)}' does not exist")
    return problems


# ----------------------------------------------------------------- category runtime
@app.route("/api/category/<cat_id>/upload", methods=["POST"])
def upload_inputs(cat_id):
    recipe, err = _recipe_or_404(cat_id)
    if err:
        return err
    folder = recipe["folders"].get("input")
    if not folder:
        return _err("this category has no input folder wired yet")
    saved = []
    for f in request.files.getlist("files"):
        name = _base_name(f.filename or "upload")
        cs.write_file(folder, "/" + name, f.read())
        saved.append(name)
    return jsonify({"ok": True, "saved": saved})


@app.route("/api/category/<cat_id>/files", methods=["GET"])
def list_category_files(cat_id):
    recipe, err = _recipe_or_404(cat_id)
    if err:
        return err
    which = request.args.get("folder", "input")
    folder = recipe["folders"].get(which)
    files = [x for x in cs.list_files(folder) if not x["name"].startswith(".")]
    files = [x for x in files if CACHE_DIR + "/" not in x["path"]]
    return jsonify({"ok": True, "folder": which, "files": files})


@app.route("/api/category/<cat_id>/validate", methods=["POST"])
def validate_inputs(cat_id):
    recipe, err = _recipe_or_404(cat_id)
    if err:
        return err
    if not recipe.get("canonical_schema"):
        return _err("category has no schema yet - set it up in Admin")
    folder = recipe["folders"].get("input")
    results = []
    for item in cs.list_files(folder):
        try:
            data = cs.read_file(folder, item["path"])
            df, hr = compute.read_table(data, item["name"])
            v = compute.validate_schema(list(df.columns), recipe["canonical_schema"])
            results.append({"file": item["name"], "header_row": hr, "rows": len(df), **v})
        except Exception as exc:  # noqa: BLE001
            results.append({"file": item["name"], "ok": False, "message": str(exc)})
    return jsonify({"ok": True, "results": results})


@app.route("/api/category/<cat_id>/compute", methods=["POST"])
def run_compute(cat_id):
    recipe, err = _recipe_or_404(cat_id)
    if err:
        return err
    if not cs.recipe_is_configured(recipe):
        return _err("category is not fully configured")
    body = request.get_json(force=True, silent=True) or {}
    only = set(body.get("files") or [])
    in_folder = recipe["folders"]["input"]
    out_folder = recipe["folders"]["output"]
    tpl_folder = recipe["folders"].get("template") or out_folder
    lookups = compute.Lookups(recipe.get("lookup_tables") or {})

    results = []
    for item in cs.list_files(in_folder):
        if only and item["name"] not in only:
            continue
        try:
            data = cs.read_file(in_folder, item["path"])
            df, _ = compute.read_table(data, item["name"])
            v = compute.validate_schema(list(df.columns), recipe["canonical_schema"])
            if not v["ok"]:
                results.append({"file": item["name"], "ok": False, "message": v["message"]})
                continue
            values = compute.evaluate(df, recipe, lookups)
            wb = compute.build_workbook(values, recipe, lookups,
                                       data_sheet_name=recipe.get("data_sheet") or "Data")
            stem = _stem(item["name"])
            out_name = f"{stem}{COMPUTED_SUFFIX}"
            cs.write_file(out_folder, "/" + out_name, wb)
            cs.write_file(tpl_folder, "/" + out_name, wb)
            cs.write_file(out_folder, f"/{CACHE_DIR}/{stem}.json",
                          values.to_json(orient="split", date_format="iso").encode("utf-8"))
            results.append({
                "file": item["name"], "ok": True, "output": out_name,
                "rows": len(values), "errors": values.attrs.get("compute_errors", []),
            })
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            results.append({"file": item["name"], "ok": False, "message": str(exc)})
    return jsonify({"ok": True, "results": results})


@app.route("/api/category/<cat_id>/compare", methods=["POST"])
def run_compare(cat_id):
    recipe, err = _recipe_or_404(cat_id)
    if err:
        return err
    if not recipe.get("comparison"):
        return _err("no comparison rules configured for this category")
    out_folder = recipe["folders"]["output"]
    in_folder = recipe["folders"]["input"]
    lookups = compute.Lookups(recipe.get("lookup_tables") or {})

    files = []
    for item in cs.list_files(out_folder):
        if not item["name"].endswith(COMPUTED_SUFFIX):
            continue
        stem = _stem(item["name"])
        try:
            cached = cs.read_file(out_folder, f"/{CACHE_DIR}/{stem}.json")
            vdf = pd.read_json(io.BytesIO(cached), orient="split")
        except Exception:  # noqa: BLE001 - recompute from the matching input
            src = next((i for i in cs.list_files(in_folder) if _stem(i["name"]) == stem), None)
            if not src:
                continue
            df, _ = compute.read_table(cs.read_file(in_folder, src["path"]), src["name"])
            vdf = compute.evaluate(df, recipe, lookups)
        files.append((item["name"], vdf))

    if not files:
        return _err("no computed outputs found - run compute first")
    wb = compare.build_comparison_workbook(files, recipe)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    name = f"comparison__{ts}.xlsx"
    cs.write_file(out_folder, "/" + name, wb)
    summary = compare.run_comparison(files, recipe)["Summary"]
    return jsonify({"ok": True, "output": name,
                    "summary": json.loads(summary.to_json(orient="records"))})


@app.route("/api/category/<cat_id>/download", methods=["GET"])
def download(cat_id):
    recipe, err = _recipe_or_404(cat_id)
    if err:
        return err
    which = request.args.get("folder", "output")
    path = request.args.get("path", "")
    folder = recipe["folders"].get(which)
    if not folder or not path:
        return _err("folder and path required")
    try:
        data = cs.read_file(folder, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"not found: {exc}", 404)
    return send_file(io.BytesIO(data), as_attachment=True,
                     download_name=_base_name(path))


@app.route("/api/category/<cat_id>/delete", methods=["POST"])
def delete(cat_id):
    recipe, err = _recipe_or_404(cat_id)
    if err:
        return err
    body = request.get_json(force=True, silent=True) or {}
    which = body.get("folder", "")
    folder = recipe["folders"].get(which)
    if not folder:
        return _err("unknown folder")
    if body.get("all"):
        n = cs.clear_folder(folder)
        return jsonify({"ok": True, "deleted": n})
    path = body.get("path", "")
    if not path:
        return _err("path required")
    cs.delete_file(folder, path)
    # drop the sidecar cache for computed outputs
    if path.lstrip("/").endswith(COMPUTED_SUFFIX):
        cs.delete_file(folder, f"/{CACHE_DIR}/{_stem(path)}.json")
    return jsonify({"ok": True, "deleted": 1})


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat() + "Z"})
