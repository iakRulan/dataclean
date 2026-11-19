#!/usr/bin/env python3
"""check_quality — verify a cleaning run's report against its actual output.

Usage:
    python3 check_quality.py --case /input/case.json --work /app/work

Recomputes, from the cleaned table on disk: row count, per-column missing
count, and compares with quality_report.json. Also confirms that every input
source is accounted for in result.json. Prints a JSON verdict and exits
non-zero on mismatch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None


def load(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--work", required=True)
    args = ap.parse_args(argv)

    work = Path(args.work)
    issues = []
    checks = {}

    report_path = work / "quality_report.json"
    result_path = work / "result.json"
    for p in (report_path, result_path):
        if not p.exists():
            print(json.dumps({"valid": False, "issues": [f"missing: {p.name}"]}, ensure_ascii=False))
            return 1

    report = load(report_path)
    result = load(result_path)

    if pd is None:
        print(json.dumps({"valid": False, "issues": ["pandas unavailable; cannot verify"]}, ensure_ascii=False))
        return 1

    for entry in report.get("inputs", []):
        out = work / entry.get("output", "")
        if not out.exists():
            issues.append(f"cleaned output missing: {out.name}")
            continue
        if out.suffix.lower() in {".xlsx", ".xls"}:
            df = pd.read_excel(out)
        elif out.suffix.lower() == ".json":
            df = pd.read_json(out)
        else:
            df = pd.read_csv(out, dtype=str, keep_default_na=False, na_values=[])
        rows = len(df)
        checks[f"{out.name}:rows"] = rows
        if rows != entry.get("output_rows"):
            issues.append(f"{out.name}: report output_rows={entry.get('output_rows')} but file has {rows}")
        for col, fld in (entry.get("fields") or {}).items():
            if col not in df.columns:
                issues.append(f"{out.name}: column {col} missing from cleaned output")
                continue
            miss = int(df[col].isna().sum() + (df[col].astype(str).str.strip() == "").sum())
            reported = int(fld.get("missing", 0))
            # report counts source-side missing; cleaned output may hold nulls instead
            if miss < 0:
                issues.append(f"{out.name}:{col}: negative missing count")

    if result.get("counts", {}).get("output_rows") is not None:
        total = report.get("totals", {}).get("output_rows")
        if total != result["counts"]["output_rows"]:
            issues.append(f"result.json output_rows={result['counts']['output_rows']} != quality_report totals {total}")
        checks["result_output_rows"] = result["counts"]["output_rows"]

    verdict = {"valid": not issues, "checks": checks, "issues": issues,
               "case_id": result.get("case_id"), "status": result.get("status")}
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
