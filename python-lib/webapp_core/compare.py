"""Row-level comparison of configured column pairs across every computed output file.

A comparison rule (configured in the Admin UI) is::

    { "left": "<computed column>", "right": "<other column>",
      "type": "numeric" | "text", "tolerance": 0.01 }

For each row of each file every rule is evaluated; the row is *matched* only when all
rules pass. ``build_comparison_workbook`` returns one ``.xlsx`` with ``Matched``,
``Mismatched`` and ``Summary`` sheets.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import openpyxl


def _num(x):
    return pd.to_numeric(pd.Series([x]), errors="coerce").iloc[0]


def _cmp_cell(left: Any, right: Any, rule: Dict[str, Any]) -> Tuple[bool, float]:
    rtype = rule.get("type", "numeric")
    if rtype == "numeric":
        lv, rv = _num(left), _num(right)
        if pd.isna(lv) and pd.isna(rv):
            return True, 0.0
        if pd.isna(lv) or pd.isna(rv):
            return False, float("nan")
        delta = abs(float(lv) - float(rv))
        return delta <= float(rule.get("tolerance", 0) or 0), delta
    ls = "" if left is None or (isinstance(left, float) and np.isnan(left)) else str(left).strip()
    rs = "" if right is None or (isinstance(right, float) and np.isnan(right)) else str(right).strip()
    if not rule.get("case_sensitive"):
        ls, rs = ls.lower(), rs.lower()
    return ls == rs, 0.0 if ls == rs else float("nan")


def compare_file(filename: str, values_df: pd.DataFrame,
                 rules: List[Dict[str, Any]], key_columns: List[str] | None = None
                 ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    matched: List[Dict[str, Any]] = []
    mismatched: List[Dict[str, Any]] = []
    key_columns = [k for k in (key_columns or []) if k in values_df.columns]

    for pos, (_, row) in enumerate(values_df.iterrows()):
        excel_row = pos + 2
        key_part = {f"key:{k}": row.get(k) for k in key_columns}
        row_ok = True
        row_details: List[Dict[str, Any]] = []
        for rule in rules:
            lcol, rcol = rule.get("left"), rule.get("right")
            if lcol not in values_df.columns or rcol not in values_df.columns:
                row_ok = False
                row_details.append({"rule": f"{lcol} vs {rcol}", "status": "column missing"})
                continue
            ok, delta = _cmp_cell(row.get(lcol), row.get(rcol), rule)
            row_ok = row_ok and ok
            row_details.append({
                "rule": f"{lcol} vs {rcol}", "status": "ok" if ok else "diff",
                "left_col": lcol, "left_val": row.get(lcol),
                "right_col": rcol, "right_val": row.get(rcol),
                "delta": None if (isinstance(delta, float) and np.isnan(delta)) else delta,
            })
        base = {"source_file": filename, "row": excel_row, **key_part}
        if row_ok:
            matched.append({**base, **{d["rule"]: "ok" for d in row_details}})
        else:
            for d in row_details:
                if d["status"] != "ok":
                    mismatched.append({**base, **d})
    return matched, mismatched


def run_comparison(files: List[Tuple[str, pd.DataFrame]], recipe: Dict[str, Any]
                   ) -> Dict[str, pd.DataFrame]:
    rules = recipe.get("comparison", [])
    key_columns = recipe.get("comparison_keys", [])
    all_matched: List[Dict[str, Any]] = []
    all_mismatched: List[Dict[str, Any]] = []
    summary: List[Dict[str, Any]] = []

    for filename, vdf in files:
        m, mm = compare_file(filename, vdf, rules, key_columns)
        all_matched.extend(m)
        all_mismatched.extend(mm)
        mismatch_rows = {(d["source_file"], d["row"]) for d in mm}
        summary.append({
            "source_file": filename,
            "rows": len(vdf),
            "matched_rows": len(m),
            "mismatched_rows": len(mismatch_rows),
            "mismatch_findings": len(mm),
        })

    return {
        "Matched": pd.DataFrame(all_matched),
        "Mismatched": pd.DataFrame(all_mismatched),
        "Summary": pd.DataFrame(summary),
    }


def build_comparison_workbook(files: List[Tuple[str, pd.DataFrame]],
                              recipe: Dict[str, Any]) -> bytes:
    sheets = run_comparison(files, recipe)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    order = ["Summary", "Matched", "Mismatched"]
    for name in order:
        frame = sheets.get(name, pd.DataFrame())
        ws = wb.create_sheet(title=name)
        if frame.empty:
            ws.append([f"(no {name.lower()} rows)"])
            continue
        ws.append(list(frame.columns))
        for _, row in frame.iterrows():
            ws.append([
                None if (isinstance(v, float) and np.isnan(v)) else
                (v.item() if hasattr(v, "item") else v)
                for v in row.values
            ])
    meta = wb.create_sheet(title="_meta")
    meta.append(["generated", datetime.utcnow().isoformat() + "Z"])
    meta.append(["category", recipe.get("name", recipe.get("id", ""))])
    meta.append(["rules", "; ".join(f"{r.get('left')} vs {r.get('right')}"
                                    for r in recipe.get("comparison", []))])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
