# Category Formula Studio — Dataiku webapp

A **Standard** Dataiku webapp (HTML/CSS/JS + Python Flask backend) that turns a sample
Excel workbook into a reusable "recipe" per category, then bulk-processes input files:
validate → compute (live Excel formulas) → per-file outputs + templates → row-level
comparison → consolidated Matched/Mismatched workbook. Includes an admin console.

```
webapp/
  body.html            → paste into the webapp HTML pane
  style.css            → paste into the webapp CSS pane
  app.js               → paste into the webapp JS pane
  backend.py           → paste into the webapp Python pane
  python-lib/webapp_core/   → add to the project libraries (see step 2)
  tests/               → local pytest suite (not deployed)
```

## 1. Create the managed folders

All storage is Dataiku **managed folders**.

| Purpose | How many | Notes |
|---|---|---|
| **Config** | 1 (shared) | Holds `app_settings.json` + `category_<id>.json` recipes + `samples/`. |
| **Input** | 1 per category | Where users upload input files. |
| **Mapping** | 1 per category | Human-readable CSV mirror of the lookup tables. |
| **Output** | 1 per category | Per-file computed workbooks + the comparison workbook + `_cache/`. |
| **Template** | 1 per category | Copy of each computed workbook. |

You can start with just category **A**'s four folders and add the rest later.

Then set a **project variable** so the backend can find the config folder:

```json
{ "WEBAPP_CONFIG_FOLDER": "<id of the config managed folder>" }
```

(Project → Settings → Variables. Alternatively hard-wire `CONFIG_FOLDER_FALLBACK` at the
top of `python-lib/webapp_core/config_store.py`.)

## 2. Add the backend library

Copy `webapp/python-lib/webapp_core/` into the project's **python-lib/** folder
(Project → Libraries → `python-lib`), so `backend.py` can `import webapp_core`.

Code-env packages required: `pandas`, `numpy`, `openpyxl`, and `xlrd` (only for reading
legacy `.xls` inputs). Flask is already present in the webapp backend env.

## 3. Create the webapp

New webapp → **Standard** → enable the **Python backend**. Paste the four panes. In the
backend settings grant read/write access to every managed folder listed above.

### Pages / navigation

The front end is hash-routed, so each screen is a real, linkable page and the browser
Back/Forward buttons and refresh all work:

| Route | Page |
|---|---|
| `#/` | Categories — just the thumbnail grid (the landing page) |
| `#/c/<id>` | One category — the Upload → Validate → Compute → Outputs → Compare stepper |
| `#/admin` / `#/admin/<id>` | Admin console (folders, sample, recipe, settings) |

## 4. First run

1. Open the webapp. Click **Admin** → sign in with `admin` / `changeme`.
2. You are forced to the **Settings** tab — set a real username/password.
3. Pick category **A**:
   * **Managed folders** – paste the four folder ids (the dropdown lists project folders).
   * **Sample workbook** – upload the sample `.xlsx` (data sheet + mapping/lookup sheets).
     The app auto-detects the header row, the formula (computed) columns, the lookup
     sheets and any toggle names used in the formulas.
   * Review the generated **recipe**: fix any Python/pandas expression the translator
     flagged, set toggle values (Yes/No, constant for the category), add **comparison
     rules** (`computed column` vs `another column`, numeric tolerance or text).
   * **Save recipe.** Unresolved references are reported; confirm to save anyway.
4. Back on the home page category **A** shows **Ready**. Open it and use the stepper:
   **Upload** input files → **Validate** (exact schema match on the non-computed columns)
   → **Compute** → **Outputs** (download / delete / clear-all) → **Compare** (one
   consolidated workbook with `Summary`, `Matched`, `Mismatched`).

## How formulas are handled

* The sample's computed columns must contain real Excel formulas. Each is *generalized*
  (the data row number becomes `{r}`) and stored two ways:
  * `excel_formula` — written verbatim into **every** output row (`{r}` → the real row),
    so the output `.xlsx` recalculates live in Excel.
  * `pandas_expr` — a translation used to compute values for the comparison step. Edit it
    in the Admin UI if the auto-translation is imperfect.
* Lookup/mapping sheets are stored in the recipe and re-embedded as extra sheets in every
  output workbook (so `VLOOKUP(... Rates!$A$2:$B$4 ...)` keeps working), and mirrored as
  CSV into the mapping folder.
* Toggles become a `Parameters` sheet in each output and a `PARAM('Name')` value in the
  pandas expression.

## Local development / tests

```bash
cd webapp
pip install pandas numpy openpyxl pytest flask
python -m pytest -q
```

The core modules (`sample_parser`, `formula_translate`, `compute`, `compare`) have no
`dataiku` dependency. `config_store` / `backend` fall back to a local directory tree under
`./_webapp_local` when `dataiku` is not importable, so `backend.app` can be exercised with
Flask's test client (see `tests/` and the pattern in the plan's verification section).
