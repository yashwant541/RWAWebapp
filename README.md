# Category Formula Studio — Dataiku webapp

A **Standard** Dataiku webapp (HTML/CSS/JS + Python Flask backend). An admin uploads a
sample Excel workbook per category; the app derives a reusable "recipe" (schema, Excel
formulas, lookups, toggles, comparison rules). Users then bulk-upload input files and run
**validate → compute (live Excel formulas) → per-file outputs + templates → row-level
comparison**. Files can be downloaded and deleted from the UI.

```
webapp/
  body.html            → HTML pane
  style.css            → CSS pane
  app.js               → JS pane
  backend_single.py    → Python pane  (everything in one file — use this)
  backend.py + python-lib/webapp_core/   → the same code, split (optional, advanced)
  build_single.py      → regenerates backend_single.py from the split modules
  tests/               → local pytest suite (not deployed)
```

---

## ⚠️ About the "config JSON" — you do NOT create it

There is **no JSON file to write by hand**. The config managed folder starts **empty**.
The first time the webapp backend runs it *creates* `app_settings.json` inside it (admin
login + the 9 category names). Every time you save a recipe in the Admin page it *creates*
`category_A.json`, `category_B.json`, … automatically.

The only thing you must do is: **create an empty managed folder** and tell the backend its
ID via a **project variable**. That's it.

---

## Step 1 — Create the managed folders

In the **Flow**, click **+ (New)** → **Folder** (a "Managed folder"). Pick any connection
(the project's default filesystem/cloud connection is fine). Create these:

| Folder | Name suggestion | Purpose |
|---|---|---|
| Config | `webapp_config` | Holds the auto-generated settings + recipe JSON. **Leave it empty.** |
| Input (category A) | `A_input` | Users upload input files here. |
| Mapping (category A) | `A_mapping` | CSV copy of the lookup tables (written by the app). |
| Output (category A) | `A_output` | Computed workbooks + the comparison workbook. |
| Template (category A) | `A_template` | Copy of each computed workbook. |

Start with **category A only** (5 folders total). Add `B_input`…`B_template` etc. later
when you're ready for more categories.

**How to find a folder's ID:** open the folder — the URL looks like
`…/managedfolder/`**`aB3dK9Zx`**`/view`. That 8-character code is the ID. (Also shown under
the folder's **Settings** tab.)

---

## Step 2 — Point the backend at the config folder

Top bar → **⋮ (More)** → **Variables** (or **Project → Settings → Variables**).
In the **project variables** JSON, add the config folder's ID:

```json
{
    "WEBAPP_CONFIG_FOLDER": "aB3dK9Zx"
}
```

Save. (If variables already exist, just add that one key.)

---

## Step 3 — Check the code environment

The backend needs `pandas`, `numpy`, `openpyxl` (all in the Dataiku default Python env),
plus **`xlrd`** *only if* you want to accept legacy `.xls` inputs. If you need `xlrd`:
**Administration → Code envs →** your Python env **→ Packages to install →** add `xlrd` →
**Update**. Note which env it is; you'll select it in Step 4.

---

## Step 4 — Create the webapp

1. **+ New → Webapp → Standard → "Empty (code your own)"**. Name it e.g. *Category
   Formula Studio*.
2. It opens with four editable panes: **HTML, CSS, JS, Python (Backend)**.
   Make sure **"This web app has a Python backend"** is enabled (Settings tab).
3. Paste:
   * `body.html` → **HTML**
   * `style.css` → **CSS**
   * `app.js` → **JS**
   * `backend_single.py` → **Python / Backend**
4. **Settings → Python backend → Code env**: pick the env from Step 3 (if you added
   `xlrd`), otherwise leave the default.
5. **Settings → Security / Connections**: grant the webapp access to the managed folders
   (it needs read/write on all of them). If your instance doesn't require this, skip it.
6. Click **Save**, then **Start / Restart backend** (top of the Python pane).
7. Click **View web app**.

### Pages (hash-routed — Back button and refresh work)

| URL | Page |
|---|---|
| `#/` | Categories — the thumbnail grid (landing page) |
| `#/c/A` | Category A — Upload → Validate → Compute → Outputs → Compare |
| `#/admin` | Admin console |

---

## Step 5 — First sign-in (in the webapp)

1. Click **Admin** (top-right) → sign in with **`admin`** / **`changeme`**.
2. You're sent to the **⚙ Settings** tab and asked to change the credentials:
   *Current password* = `changeme`, set a new username (optional) and a new password
   (min 6 chars) → **Update**.
3. Sign in again with the new credentials.

---

## Step 6 — Set up category A

Click the **A** tab in the Admin console.

1. **Name** – optionally rename "A" to a real name → **Save name**.
2. **Managed folders** – type or pick the four folder IDs (`A_input`, `A_mapping`,
   `A_output`, `A_template`) → **Save folders**.
3. **Sample workbook** – choose your sample `.xlsx` → **Analyse sample**. The app:
   * finds the data sheet and header row (data can start on any row);
   * lists the **input columns** (everything not driven by a formula) — this becomes the
     exact-match schema for uploads;
   * lists the **computed columns** with their Excel formula and a suggested Python/pandas
     expression — **review each expression** and fix it if the note flags something;
   * shows the **lookup tables** it pulled from the other sheets;
   * proposes **toggles** for any name in a formula that isn't a column or a lookup — set
     each to **Yes** or **No** (constant for the category).
4. **Comparison rules** – add one row per check: **left** = a computed column, **right** =
   the column to compare it against, **type** = numeric (with a tolerance) or text.
5. **Save recipe.** If it reports unresolved references, read them and either fix the
   recipe or confirm to save anyway.

Back on **Categories**, card **A** now shows a green **Ready** badge.

---

## Step 7 — Use it (any user)

Open category **A** from the home page and follow the stepper:

1. **Upload** – drag in `.xlsx/.xls/.csv` input files.
2. **Validate** – each file is checked against the exact input-column schema.
3. **Compute** – valid files become `<name>__computed.xlsx` in the output **and** template
   folders; computed columns contain the **live Excel formula** in every row, and the
   lookup tables are embedded as extra sheets.
4. **Outputs** – download or delete individual files; **Clear all** to empty a folder.
5. **Compare** – produces one `comparison__<timestamp>.xlsx` (Summary / Matched /
   Mismatched) in the output folder.

---

## Adding more categories later

Create that category's four folders (Step 1), then in the Admin page open its tab and do
Step 6. Renaming is in the same tab. The category list itself is fixed at 9 (A–I); rename
the ones you use and ignore the rest.

---

## Local development / tests

```bash
cd webapp
pip install pandas numpy openpyxl pytest flask
python -m pytest -q          # 15 tests
python build_single.py       # regenerate backend_single.py after editing modules
```

`sample_parser`, `formula_translate`, `compute`, `compare` have no `dataiku` dependency.
`config_store` / the backend fall back to a local `./_webapp_local` directory when
`dataiku` isn't importable, so `app` can be driven with Flask's test client.
