---
name: dataclean
description: "Clean research data reproducibly: read tables with encoding/delimiter repair, distinguish missing vs not-detected vs true zero, deduplicate by real keys, convert units through an explicit mapping, flag or cap outliers, and emit a cleaned table plus a field-level quality report, a per-decision cleaning log and a rules snapshot. Trigger on: '数据清洗', '数据处理', '缺失值', '异常值', '去重', '单位转换', '数据质量报告', 'clean this dataset', 'data cleaning', 'normalize units'. Not for: statistical modelling, hypothesis testing, figure beautification, database administration."
---

# dataclean — Reproducible Research-data Cleaning with Evidence

> **脚本路径**：本技能安装于 `/skills/weekly-skill/`，脚本绝对路径为 `/skills/weekly-skill/scripts/`（工作目录是 `/app/work`，不要用相对路径）。

Turn a dirty instrument/export table into one that can support downstream statistics — and prove every change. The failure mode this skill exists to prevent: a generic script that ignores the data dictionary, fills missing values with column means, confuses "not detected" with a measured zero, keeps near-duplicates, loses precision in unit conversion, overwrites the original file, and writes a quality report whose numbers do not match the output.

**Method in one line:** parse with encoding/delimiter repair → apply field-level rules from the dictionary/workorder → copy (never overwrite the source) → write the cleaned table + a per-decision log + a quality report + a rules snapshot.

## When to use

- Input: a case file (default `/input/case.json`) with the table(s), a data dictionary, per-metric unit/QC rules and a cleaning workorder; plus the data files.
- Output: under `/app/output` — `cleaned_*.csv|xlsx|json`, `cleaning_log.json`, `quality_report.json`, `rules_snapshot.json`, `result.json`.
- Supports CSV/TSV/pipe/semicolon text, JSON, and Excel.

## Pipeline (run in order)

### Phase 1 — Clean

```bash
python3 /skills/weekly-skill/scripts/clean.py --case /input/case.json --work /app/work
```

What it does, in order:

1. **Read robustly.** Try `utf-8-sig`, `utf-8`, `gb18030`, `cp1252`, `latin-1`; repair UTF-8 mojibake; sniff the delimiter; keep everything as strings first so no precision is lost.
2. **Normalize column names** (optional, recorded as a mapping).
3. **Deduplicate by real keys** (`key_columns` from the case/rules), keeping the first occurrence and logging the count.
4. **Per-field missing/invalid handling.** A column counts as a *measurement* column only when its dictionary type is numeric or ≥50% of its values parse as numbers. Sentinels (`-999`, `NA`, `ND`, `<LOD`, `BDL`, `not detected`, …) are then handled by the configured action:
   - `flag` (default): set to null, record kind (`missing` vs `not_detected`) and index;
   - `zero`: only for `missing`, never for `not_detected`;
   - `column_mean`: fill with the column mean (recorded).
   A measured `0` is **never** treated as missing.
5. **Unit conversion.** Global `factor`, or per-row via `unit_column` + `factors` map; every conversion is logged with the row count and target unit.
6. **Outliers.** IQR rule (1.5×IQR) with `flag` (default) / `cap` / `drop`, per column; indices and bounds recorded.
7. **Write outputs.** Cleaned table (UTF-8-BOM CSV so Excel opens it correctly), `cleaning_log.json`, `quality_report.json`, `rules_snapshot.json`.

### Phase 2 — Verify the report matches the output

```bash
python3 /skills/weekly-skill/scripts/check_quality.py --case /input/case.json --work /app/work
```

Independently recomputes per-field missing counts and row counts from the cleaned table and compares them to `quality_report.json`; exits non-zero on any mismatch. **A quality report whose numbers disagree with the output is a bug, not a finding.**

### Phase 3 — Copy deliverables

```bash
mkdir -p /app/output
cp /app/work/cleaned_* /app/work/cleaning_log.json /app/work/quality_report.json \
   /app/work/rules_snapshot.json /app/work/result.json /app/output/
```

## Rules that must hold

1. **Dictionary first.** Field semantics come from the case's dictionary/workorder; the script never guesses that `-999` means "missing" in a column the dictionary calls text.
2. **Missing ≠ zero ≠ not-detected.** Three different states, three different treatments, all logged.
3. **No silent drops.** Every removed row, replaced value and capped outlier appears in `cleaning_log.json` with before → after.
4. **Explicit units.** Conversion uses a written mapping; the mapping and row counts are stored in `rules_snapshot.json` / the log.
5. **Never overwrite the source.** The original file is read-only; outputs are new files.
6. **Report ⇄ output consistency.** `check_quality.py` enforces that the numbers agree.

## Exception handling

| Situation | Behaviour |
|-|-|
| File missing | logged as `error: file not found`; pipeline continues |
| Encoding undecodable | falls back to `utf-8` with replacement, flagged lossy |
| Delimiter ambiguous | sniffed by max column count; recorded |
| Key columns absent | warning logged; dedup skipped, not guessed |
| Unit factor missing for a row's unit | row left unconverted, counted in the log |
| Outlier action `none` | detection skipped |
| pandas unavailable | writes an honest `partial` result and stops |

## Self-check

```bash
python3 /skills/weekly-skill/scripts/check_quality.py --case /input/case.json --work /app/work
python3 -c "import json;r=json.load(open('/app/work/result.json'));print(r['case_id'], r['status'], r['counts'])"
```

Both must be clean; `status` must be `complete` unless a limit is named in `uncertainty`.