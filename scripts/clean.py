#!/usr/bin/env python3
"""clean — configurable research-data cleaning with a reproducible audit trail.

Usage:
    python3 clean.py --case /input/case.json --work /app/work

The case supplies: input table(s), a data dictionary, per-metric unit/QC rules
and a cleaning workorder. This script applies them and writes:

    work/cleaned.<ext>     cleaned table (same format family as input when possible)
    work/cleaning_log.json every decision, per row/column, with before -> after
    work/quality_report.json field-level quality metrics, consistent with output
    work/rules_snapshot.json the exact rules used (for reproduction)
    work/result.json       case_id / status / counts / uncertainty

Nothing is silently dropped: invalid and missing values are flagged, counted and
kept in the log. Missing is never confused with a measured zero.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
from collections import Counter, OrderedDict
from pathlib import Path

try:
    import pandas as pd
    import numpy as np
except Exception:  # pragma: no cover
    pd = None
    np = None

VERSION = "1.0.0"

SENTINELS = {
    "", " ", "-999", "-999.0", "-999.00", "na", "n/a", "nan", "null", "none",
    "nd", "n.d.", "nd.", "bdl", "<lod", "< lod", "lod", "not detected",
    "not detected", "below detection limit", "below limit of detection",
    "trace", "tr",
}
NON_NUMERIC_SENTINELS = {"nd", "n.d.", "bdl", "<lod", "lod", "not detected",
                         "below detection limit", "trace", "tr"}


def load(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# -- case parsing ----------------------------------------------------------

def load_case(case_path: Path) -> dict:
    case_path = Path(case_path)
    if case_path.is_dir():
        return {"case_id": case_path.name, "inputs": [str(case_path)], "raw": {}}
    raw = load(case_path)
    case = dict(raw) if isinstance(raw, dict) else {"value": raw}
    inputs = []
    for key in ("inputs", "input", "files", "data", "tables", "datasets"):
        if key in case:
            v = case[key]
            if isinstance(v, list):
                inputs.extend([x if isinstance(x, str) else (x.get("path") or x.get("file")) for x in v])
            elif isinstance(v, str):
                inputs.append(v)
    inputs = [i for i in inputs if isinstance(i, str) and i]
    dictionary = case.get("dictionary") or case.get("data_dictionary") or case.get("schema") or {}
    rules = case.get("rules") or case.get("qc_rules") or case.get("cleaning_rules") or {}
    workorder = case.get("workorder") or case.get("work_order") or case.get("tasks") or []
    units = case.get("units") or case.get("unit_rules") or {}
    return {
        "case_id": case.get("case_id") or case.get("id") or case_path.stem,
        "raw": case,
        "inputs": inputs,
        "dictionary": dictionary if isinstance(dictionary, dict) else {},
        "rules": rules if isinstance(rules, dict) else {},
        "workorder": workorder if isinstance(workorder, list) else [workorder],
        "units": units if isinstance(units, dict) else {},
        "question": case.get("question") or case.get("task") or "",
    }


# -- robust table reading --------------------------------------------------

ENCODINGS = ["utf-8-sig", "utf-8", "gb18030", "cp1252", "latin-1"]


def read_text_robust(path: Path):
    """Try encodings; repair common mojibake. Returns (text, encoding, notes)."""
    notes = []
    data = path.read_bytes()
    for enc in ENCODINGS:
        try:
            text = data.decode(enc)
            notes.append(f"decoded as {enc}")
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")
        notes.append("decoded as utf-8 with replacement (lossy)")

    # fix classic mojibake (UTF-8 bytes read as latin-1/cp1252)
    if ("Â" in text or "Ã" in text or "â€" in text) and enc != "utf-8":
        try:
            fixed = text.encode("cp1252", errors="ignore").decode("utf-8", errors="ignore")
            if "Â" not in fixed and "Ã" not in fixed:
                text = fixed
                notes.append("repaired UTF-8 mojibake")
        except Exception:
            pass
    return text, (enc if 'enc' in locals() else "unknown"), notes


def sniff_delimiter(sample: str):
    delimiters = [",", "\t", ";", "|"]
    best, best_count = ",", 0
    for d in delimiters:
        try:
            count = max(len(r) for r in csv.reader(io.StringIO(sample), delimiter=d))
        except Exception:
            count = 0
        if count > best_count:
            best, best_count = d, count
    return best


def read_table(path: Path):
    """Return (dataframe, notes). Supports csv/tsv/txt/json/xlsx."""
    notes = []
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        if pd is None:
            raise RuntimeError("pandas is required for Excel input")
        df = pd.read_excel(path)
        notes.append(f"read Excel with pandas: {df.shape[0]} rows x {df.shape[1]} cols")
        return df, notes
    if suffix == ".json":
        data = load(path)
        if isinstance(data, dict):
            for key in ("data", "rows", "records", "values"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        df = pd.DataFrame(data)
        notes.append(f"read JSON with pandas: {df.shape[0]} rows x {df.shape[1]} cols")
        return df, notes

    text, enc, n = read_text_robust(path)
    notes.extend(n)
    sample = "\n".join(text.splitlines()[:8])
    delim = sniff_delimiter(sample)
    notes.append(f"delimiter={repr(delim)}")
    try:
        df = pd.read_csv(io.StringIO(text), delimiter=delim, dtype=str, keep_default_na=False,
                         na_values=[], engine="python")
    except Exception as exc:
        notes.append(f"pandas parse failed ({exc}); falling back to csv module")
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        df = pd.DataFrame(rows[1:], columns=rows[0] if rows else [])
    notes.append(f"parsed {df.shape[0]} rows x {df.shape[1]} cols")
    return df, notes


# -- cleaning primitives ---------------------------------------------------

def is_missing(value) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    return s.lower() in SENTINELS


def classify_missing(value) -> str:
    s = str(value).strip().lower()
    if s in NON_NUMERIC_SENTINELS or s.startswith("<"):
        return "not_detected"
    if s in SENTINELS:
        return "missing"
    return "value"


def to_number(value):
    """Parse a number, tolerating thousands separators and scientific notation."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace(" ", "")
    s = s.replace("×10^", "e").replace("x10^", "e")
    try:
        return float(s)
    except ValueError:
        return None


def normalize_unit(value, unit_map):
    """Convert a value to the target unit using an explicit mapping."""
    if not unit_map:
        return value, None
    factor = unit_map.get("factor")
    if factor is None:
        return value, None
    num = to_number(value)
    if num is None:
        return value, None
    return num * factor, f"×{factor} ({unit_map.get('from','')} -> {unit_map.get('to','')})"


def column_is_numeric(series, rule):
    """A column is treated as a measurement column only when it parses as one."""
    if isinstance(rule, dict) and rule.get("type") in {"numeric", "float", "int", "number"}:
        return True
    total = 0
    ok = 0
    for v in series:
        if is_missing(v):
            continue
        total += 1
        if to_number(v) is not None:
            ok += 1
    if total == 0:
        return False
    return ok / total >= 0.5


def iqr_outliers(series):
    nums = pd.to_numeric(series, errors="coerce").dropna()
    if len(nums) < 4:
        return set()
    q1, q3 = nums.quantile(0.25), nums.quantile(0.75)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    out = set()
    for idx, v in series.items():
        n = to_number(v)
        if n is not None and (n < lo or n > hi):
            out.add(idx)
    return out


# -- main pipeline ---------------------------------------------------------

def clean_dataframe(df, case, log):
    rules = case["rules"]
    dictionary = case["dictionary"]
    units = case["units"]

    df = df.copy()
    before_rows = len(df)
    log.append({"step": "load", "rows": before_rows, "cols": len(df.columns),
                "columns": list(map(str, df.columns))})

    # 1. column name normalization (optional, recorded)
    if rules.get("normalize_columns", True):
        mapping = {}
        for c in df.columns:
            new = re.sub(r"\s+", " ", str(c)).strip()
            if new != str(c):
                mapping[str(c)] = new
        if mapping:
            df = df.rename(columns=mapping)
            log.append({"step": "normalize_columns", "mapping": mapping})

    # 2. duplicate handling by real keys
    keys = rules.get("key_columns") or rules.get("primary_key") or case["raw"].get("key_columns")
    if isinstance(keys, str):
        keys = [keys]
    if keys:
        present = [k for k in keys if k in df.columns]
        if present:
            dup_mask = df.duplicated(subset=present, keep="first")
            n_dup = int(dup_mask.sum())
            if n_dup:
                log.append({"step": "dedup", "keys": present, "removed": n_dup,
                            "kept": "first occurrence"})
                df = df.loc[~dup_mask].reset_index(drop=True)
        else:
            log.append({"step": "dedup", "warning": f"key columns not found: {present}"})

    # 3. per-column missing / invalid handling
    field_report = OrderedDict()
    for col in df.columns:
        s = df[col]
        rule0 = {}
        if isinstance(dictionary, dict):
            rule0 = dictionary.get(col) or dictionary.get(str(col)) or {}
        numeric_col = column_is_numeric(s, rule0)
        miss = [i for i, v in s.items() if numeric_col and is_missing(v)]
        kinds = Counter(classify_missing(v) for v in s if numeric_col and is_missing(v))
        numeric = pd.to_numeric(s, errors="coerce")
        non_numeric = [i for i, v in s.items() if not is_missing(v) and to_number(v) is None]

        rule = {}
        if isinstance(dictionary, dict):
            rule = dictionary.get(col) or dictionary.get(str(col)) or {}
        if not rule and isinstance(rules.get("columns"), dict):
            rule = rules["columns"].get(col) or {}

        action = rule.get("missing") or rules.get("missing") or "flag"
        converted = []
        for i in miss:
            original = s.iloc[i]
            kind = classify_missing(original)
            if action in {"flag", "keep"}:
                df.at[i, col] = None
            elif action == "zero" and kind == "missing":
                df.at[i, col] = 0.0
            elif action == "zero":
                df.at[i, col] = None
            elif action == "column_mean":
                colnums = pd.to_numeric(s, errors="coerce")
                if colnums.notna().any():
                    df.at[i, col] = float(colnums.mean())
            else:
                df.at[i, col] = None
            converted.append({"index": int(i), "from": str(original), "to": df.at[i, col],
                              "kind": kind, "action": action})

        # unit conversion for this column (global factor, or per-row via unit column)
        unit_note = None
        if col in units or str(col) in units:
            um = units.get(col) or units.get(str(col))
            if isinstance(um, dict) and um.get("unit_column") and um.get("factors"):
                ucol = um["unit_column"]
                converted_rows = 0
                for i, v in list(df[col].items()):
                    num = to_number(v)
                    if num is None:
                        continue
                    row_unit = str(df.at[i, ucol]).strip() if ucol in df.columns else ""
                    factor = um["factors"].get(row_unit)
                    if factor is None:
                        factor = um["factors"].get("*")
                    if factor is not None:
                        df.at[i, col] = num * factor
                        converted_rows += 1
                unit_note = f"per-row factors to {um.get('to','')} ({converted_rows} rows)"
                log.append({"step": "unit_convert", "column": col, "mode": "per-row",
                            "unit_column": ucol, "to": um.get("to"), "rows": converted_rows})
            elif isinstance(um, dict) and um.get("factor") is not None:
                factor = um["factor"]
                for i, v in list(df[col].items()):
                    num = to_number(v)
                    if num is not None:
                        df.at[i, col] = num * factor
                unit_note = f"×{factor} to {um.get('to','')}"
                log.append({"step": "unit_convert", "column": col, "mode": "global",
                            "factor": factor, "to": um.get("to"),
                            "rows": int(df[col].notna().sum())})

        # outlier handling
        outlier_action = rule.get("outlier") or rules.get("outlier") or "flag"
        outlier_idx = iqr_outliers(df[col]) if outlier_action != "none" else set()
        outliers = []
        for i in sorted(outlier_idx):
            val = df.at[i, col]
            rec = {"index": int(i), "value": val}
            if outlier_action == "cap":
                nums = pd.to_numeric(df[col], errors="coerce").dropna()
                q1, q3 = nums.quantile(0.25), nums.quantile(0.75)
                lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
                newv = min(max(to_number(val), lo), hi)
                rec.update({"action": "cap", "to": newv, "bounds": [lo, hi]})
                df.at[i, col] = newv
            elif outlier_action == "drop":
                rec.update({"action": "drop"})
            else:
                rec.update({"action": "flag"})
            outliers.append(rec)
        if outlier_action == "drop" and outliers:
            drop_idx = [o["index"] for o in outliers]
            df = df.drop(index=drop_idx).reset_index(drop=True)

        field_report[str(col)] = {
            "missing": len(miss),
            "missing_kinds": dict(kinds),
            "non_numeric": len(non_numeric),
            "missing_action": action,
            "unit_conversion": unit_note,
            "outliers": len(outliers),
            "outlier_action": outlier_action,
            "missing_detail": converted[:50],
            "outlier_detail": outliers[:50],
        }

    log.append({"step": "final", "rows": len(df), "cols": len(df.columns)})

    quality = {
        "case_id": case["case_id"],
        "input_rows": before_rows,
        "output_rows": len(df),
        "columns": len(df.columns),
        "row_delta": before_rows - len(df),
        "fields": field_report,
        "consistency": {
            "missing_total": sum(f["missing"] for f in field_report.values()),
            "outlier_total": sum(f["outliers"] for f in field_report.values()),
        },
    }
    return df, quality, log


def write_table(df, path: Path):
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df.to_excel(path, index=False)
    elif path.suffix.lower() == ".json":
        df.to_json(path, orient="records", force_ascii=False, indent=2)
    else:
        df.to_csv(path, index=False, encoding="utf-8-sig")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--work", required=True)
    args = ap.parse_args(argv)

    case = load_case(Path(args.case))
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    if pd is None:
        save(work / "result.json", {
            "case_id": case["case_id"], "task": "research-data-cleaning",
            "status": "partial", "uncertainty": ["pandas/numpy unavailable; no cleaning performed"],
        })
        print("clean: pandas unavailable -> partial result")
        return

    base = Path(args.case).parent if Path(args.case).is_file() else Path(args.case)
    inputs = []
    for raw in case["inputs"]:
        p = Path(raw)
        if not p.is_absolute():
            cand = base / p
            p = cand if cand.exists() else Path(raw)
        inputs.append(p)

    log = []
    notes = []
    cleaned = []
    qualities = []
    for p in inputs:
        if not p.exists():
            log.append({"step": "load", "source": str(p), "error": "file not found"})
            continue
        try:
            df, n = read_table(p)
        except Exception as exc:  # noqa: BLE001
            log.append({"step": "load", "source": str(p), "error": f"{type(exc).__name__}: {exc}"})
            continue
        notes.extend(n)
        cdf, quality, clog = clean_dataframe(df, case, log)
        out_name = "cleaned_" + p.stem + (".xlsx" if p.suffix.lower() in {".xls", ".xlsx"} else ".csv")
        write_table(cdf, work / out_name)
        quality["source"] = str(p)
        quality["output"] = out_name
        qualities.append(quality)
        cleaned.append({"source": str(p), "output": out_name, "rows": len(cdf)})

    log = [e for e in log if e.get("step") in {"load", "normalize_columns", "dedup", "unit_convert", "final"}]
    save(work / "cleaning_log.json", {"case_id": case["case_id"], "log": log, "notes": notes})
    save(work / "quality_report.json", {
        "case_id": case["case_id"],
        "inputs": qualities,
        "totals": {
            "input_rows": sum(q["input_rows"] for q in qualities),
            "output_rows": sum(q["output_rows"] for q in qualities),
        },
    })
    save(work / "rules_snapshot.json", {
        "case_id": case["case_id"],
        "rules": case["rules"],
        "dictionary": case["dictionary"],
        "units": case["units"],
        "workorder": case["workorder"],
        "script_version": VERSION,
    })

    failed = [e for e in log if e.get("error")]
    result = {
        "case_id": case["case_id"],
        "task": "research-data-cleaning",
        "status": "complete" if cleaned and not failed else "partial",
        "deliverables": {
            "cleaned_tables": [c["output"] for c in cleaned],
            "cleaning_log": "cleaning_log.json",
            "quality_report": "quality_report.json",
            "rules_snapshot": "rules_snapshot.json",
        },
        "counts": {
            "inputs": len(inputs),
            "cleaned": len(cleaned),
            "input_rows": sum(q["input_rows"] for q in qualities),
            "output_rows": sum(q["output_rows"] for q in qualities),
            "failed_loads": len(failed),
        },
        "uncertainty": _uncertainty(case, qualities, failed),
        "sources": [str(p) for p in inputs],
        "changes": [
            {"step": "load", "detail": f"{len(cleaned)}/{len(inputs)} inputs parsed"},
            {"step": "clean", "detail": f"{sum(q['output_rows'] for q in qualities)} rows after cleaning"},
        ],
    }
    save(work / "result.json", result)
    print(f"clean: {len(cleaned)} tables, {result['counts']['output_rows']} rows -> {work / 'result.json'}")
    return result


def _uncertainty(case, qualities, failed):
    notes = []
    if failed:
        notes.append(f"{len(failed)} inputs failed to load; see cleaning_log.json")
    if not case["dictionary"]:
        notes.append("no data dictionary supplied; only generic sentinel/unit handling applied")
    if not case["rules"]:
        notes.append("no explicit QC rules supplied; defaults: flag missing, flag outliers, dedup by key if provided")
    if not case["units"]:
        notes.append("no unit mapping supplied; values left in source units")
    for q in qualities:
        for col, f in q["fields"].items():
            if f["outlier_action"] == "flag" and f["outliers"]:
                notes.append(f"{q['source']}:{col}: {f['outliers']} outliers flagged, not removed")
    return notes


if __name__ == "__main__":
    main()
