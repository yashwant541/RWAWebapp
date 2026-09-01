"""Analyse an uploaded sample workbook.

The sample ``.xlsx`` holds, on separate sheets:
  * one **data sheet** whose rows may start at any row (header auto-detected);
  * some columns of that data sheet contain **Excel formulas** -> "computed" columns;
  * other sheets are **lookup / mapping tables** referenced by the formulas;
  * formulas may reference **toggle** names that are neither columns nor lookup sheets.

``analyze_sample(bytes)`` returns a plain dict the Admin UI renders for review/editing.
Only openpyxl + (optional) the sibling ``formula_translate`` module are used - no dataiku.
"""

from __future__ import annotations

import io
import re
import string
from typing import Any, Dict, List, Optional, Tuple

import openpyxl

from . import formula_translate

MAX_SCAN_ROWS = 40
_CELL_RE = re.compile(r"(\$?)([A-Za-z]{1,3})(\$?)(\d+)")
_SHEET_REF_RE = re.compile(r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_ ]*))!")
_FUNC_RE = re.compile(r"([A-Za-z][A-Za-z0-9_.]*)\s*\(")
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Excel built-ins we translate / recognise; never treated as toggles.
KNOWN_FUNCS = {
    "IF", "IFS", "IFERROR", "AND", "OR", "NOT", "ROUND", "ROUNDUP", "ROUNDDOWN",
    "ABS", "MIN", "MAX", "SUM", "AVERAGE", "COUNT", "INT", "MOD", "SQRT", "POWER",
    "VLOOKUP", "HLOOKUP", "INDEX", "MATCH", "LOOKUP", "XLOOKUP",
    "CONCAT", "CONCATENATE", "LEFT", "RIGHT", "MID", "LEN", "UPPER", "LOWER", "TRIM",
    "TEXT", "VALUE", "TODAY", "NOW", "YEAR", "MONTH", "DAY", "DATE", "TRUE", "FALSE",
    "ISBLANK", "ISNUMBER", "ISERROR", "COALESCE",
}


# --------------------------------------------------------------------------- utils
def col_letter_to_index(letter: str) -> int:
    """``A`` -> 0, ``B`` -> 1, ``AA`` -> 26 ..."""
    n = 0
    for ch in letter.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def col_index_to_letter(idx: int) -> str:
    idx += 1
    out = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        out = string.ascii_uppercase[rem] + out
    return out


def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def _looks_like_label(v: Any) -> bool:
    s = _norm(v)
    if not s:
        return False
    try:
        float(s.replace(",", ""))
        return False
    except ValueError:
        return True


def detect_header_row(rows: List[List[Any]], max_scan: int = MAX_SCAN_ROWS) -> int:
    """Return the 0-based index of the most likely header row.

    Heuristic: the row with the most distinct non-empty text cells that also has a
    non-empty data row beneath it and no empty cells between its populated columns.
    """
    best_idx, best_score = 0, -1.0
    limit = min(len(rows), max_scan)
    for i in range(limit):
        row = rows[i]
        labels = [c for c in row if _looks_like_label(c)]
        non_null = [c for c in row if _norm(c)]
        if len(labels) < 2:
            continue
        nxt = rows[i + 1] if i + 1 < len(rows) else []
        has_data_below = any(_norm(c) for c in nxt)
        distinct = len({_norm(c).lower() for c in labels})
        score = distinct + 0.5 * len(non_null)
        if has_data_below:
            score += 3
        if len(labels) == len(non_null):  # header row is usually all-text
            score += 1
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx


def _sheet_rows(ws, values_only: bool = True) -> List[List[Any]]:
    return [list(r) for r in ws.iter_rows(values_only=values_only)]


def _headers_from_row(row: List[Any]) -> List[str]:
    headers, seen = [], {}
    for j, cell in enumerate(row):
        name = _norm(cell) or f"Column{col_index_to_letter(j)}"
        if name in seen:
            seen[name] += 1
            name = f"{name}.{seen[name]}"
        else:
            seen[name] = 0
        headers.append(name)
    return headers


# ------------------------------------------------------------------- formula tools
def extract_refs(formula: str) -> Dict[str, List[str]]:
    """Split a formula into referenced sheet names, function names and bare tokens."""
    sheets = sorted({m.group(1) or m.group(2) for m in _SHEET_REF_RE.finditer(formula)})
    funcs = sorted({m.group(1).upper() for m in _FUNC_RE.finditer(formula)})
    # Strip string literals and sheet-qualified refs before scanning bare tokens.
    stripped = re.sub(r'"[^"]*"', "", formula)
    stripped = _SHEET_REF_RE.sub("", stripped)
    stripped = _CELL_RE.sub("", stripped)
    tokens = sorted(
        {
            t for t in _TOKEN_RE.findall(stripped)
            if t.upper() not in KNOWN_FUNCS and not re.fullmatch(r"[A-Za-z]{1,3}\d*", t)
        }
    )
    return {"sheets": sheets, "funcs": funcs, "tokens": tokens}


def generalize_formula(formula: str, row: int, header_letters: Dict[str, str]) -> str:
    """Replace same-sheet cell refs on ``row`` with the ``{r}`` placeholder.

    ``A5`` on row 5 -> ``A{r}``; absolute ``$A$5`` and other-sheet refs are left alone
    so they stay constant across rows.
    """
    def repl(m: re.Match) -> str:
        dollar_col, col, dollar_row, rownum = m.groups()
        # Leave alone if it is part of a sheet-qualified ref (handled by caller context).
        if dollar_row == "$":
            return m.group(0)
        if int(rownum) == row:
            return f"{dollar_col}{col}{{r}}"
        return m.group(0)

    # Protect sheet-qualified refs from row generalisation.
    placeholders: Dict[str, str] = {}

    def stash(m: re.Match) -> str:
        key = f"\x00{len(placeholders)}\x00"
        placeholders[key] = m.group(0)
        return key

    protected = re.sub(_SHEET_REF_RE.pattern + _CELL_RE.pattern, stash, formula)
    generalized = _CELL_RE.sub(repl, protected)
    for key, val in placeholders.items():
        generalized = generalized.replace(key, val)
    return generalized


# ------------------------------------------------------------------------- analyse
def analyze_sample(file_bytes: bytes, data_sheet: Optional[str] = None) -> Dict[str, Any]:
    """Parse the sample workbook. See module docstring for the return shape."""
    wb_f = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=False)
    wb_v = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    warnings: List[str] = []

    # 1. pick the data sheet: explicit choice, else the sheet with the most formula cells,
    #    else the sheet with the largest populated area.
    def formula_count(ws) -> int:
        c = 0
        for r in ws.iter_rows():
            for cell in r:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    c += 1
        return c

    sheet_names = wb_f.sheetnames
    if data_sheet and data_sheet in sheet_names:
        ds_name = data_sheet
    else:
        scored = sorted(
            sheet_names,
            key=lambda n: (formula_count(wb_f[n]), wb_f[n].max_row * wb_f[n].max_column),
            reverse=True,
        )
        ds_name = scored[0]
        if formula_count(wb_f[ds_name]) == 0:
            warnings.append(
                "No Excel formula cells were found on any sheet; "
                f"'{ds_name}' was chosen by size. Pick the data sheet manually if wrong."
            )

    ws_f, ws_v = wb_f[ds_name], wb_v[ds_name]
    frows = _sheet_rows(ws_f)
    vrows = _sheet_rows(ws_v)
    if not frows:
        raise ValueError(f"Data sheet '{ds_name}' is empty.")

    header_row = detect_header_row(vrows)
    headers = _headers_from_row(frows[header_row])
    letter_by_header = {h: col_index_to_letter(j) for j, h in enumerate(headers)}
    header_by_letter = {col_index_to_letter(j): h for j, h in enumerate(headers)}

    first_data = header_row + 1
    if first_data >= len(frows):
        raise ValueError(f"Data sheet '{ds_name}' has a header but no data rows.")

    # 2. classify columns
    canonical: List[str] = []
    computed: List[Dict[str, Any]] = []
    for j, name in enumerate(headers):
        formula_cell = None
        formula_excel_row = None
        for ridx in range(first_data, len(frows)):
            val = frows[ridx][j] if j < len(frows[ridx]) else None
            if isinstance(val, str) and val.startswith("="):
                formula_cell = val
                formula_excel_row = ridx + 1  # openpyxl rows are 1-based
                break
        if formula_cell is None:
            canonical.append(name)
            continue
        generalized = generalize_formula(formula_cell, formula_excel_row, letter_by_header)
        pandas_expr, xl_notes = formula_translate.translate(
            generalized, header_by_letter
        )
        refs = extract_refs(generalized)
        computed.append(
            {
                "name": name,
                "position": j,
                "excel_formula": generalized,
                "sample_excel_formula": formula_cell,
                "pandas_expr": pandas_expr,
                "refs": refs,
                "notes": xl_notes,
            }
        )

    if not computed:
        warnings.append("No computed (formula) columns detected on the data sheet.")

    # 3. lookup sheets = every other sheet
    lookups: List[Dict[str, Any]] = []
    for name in sheet_names:
        if name == ds_name:
            continue
        lrows = _sheet_rows(wb_v[name])
        if not any(any(_norm(c) for c in row) for row in lrows):
            continue
        lhr = detect_header_row(lrows)
        lheaders = _headers_from_row(lrows[lhr]) if lrows else []
        preview = [
            {lheaders[k]: row[k] if k < len(row) else None for k in range(len(lheaders))}
            for row in lrows[lhr + 1: lhr + 6]
        ]
        lookups.append(
            {
                "sheet": name,
                "header_row": lhr,
                "columns": lheaders,
                "preview": preview,
                "n_rows": max(0, len(lrows) - lhr - 1),
            }
        )
    lookup_sheet_names = {l["sheet"] for l in lookups}

    # 4. unresolved tokens -> toggle candidates
    canon_lower = {c.lower() for c in canonical}
    comp_lower = {c["name"].lower() for c in computed}
    toggles: List[str] = []
    for c in computed:
        for tok in c["refs"]["tokens"]:
            tl = tok.lower()
            if tl in canon_lower or tl in comp_lower:
                continue
            if tok in lookup_sheet_names:
                continue
            if tok not in toggles:
                toggles.append(tok)
        for sh in c["refs"]["sheets"]:
            if sh not in lookup_sheet_names:
                warnings.append(
                    f"Column '{c['name']}' references sheet '{sh}' which is not a "
                    "lookup sheet in the workbook."
                )

    # 5. name-collision guard (computed columns must be distinct from data columns)
    collisions = comp_lower & canon_lower
    if collisions:
        warnings.append(
            "Computed columns reuse data-column names: "
            + ", ".join(sorted(collisions))
            + ". Rename them so comparisons can address each side."
        )

    return {
        "sheets": sheet_names,
        "data_sheet": ds_name,
        "header_row": header_row,
        "canonical_schema": canonical,
        "computed_columns": computed,
        "lookups": lookups,
        "toggles": [{"name": t, "value": "Yes"} for t in toggles],
        "warnings": warnings,
    }


def sheet_to_records(file_bytes: bytes, sheet: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Return ``(headers, rows-as-dicts)`` for one sheet, header auto-detected.

    Used by the backend to persist each lookup sheet into the mapping folder.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    rows = _sheet_rows(wb[sheet])
    hr = detect_header_row(rows)
    headers = _headers_from_row(rows[hr]) if rows else []
    records = []
    for row in rows[hr + 1:]:
        if not any(_norm(c) for c in row):
            continue
        records.append(
            {headers[k]: (row[k] if k < len(row) else None) for k in range(len(headers))}
        )
    return headers, records
