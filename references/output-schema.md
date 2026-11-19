# 输出字段字典

## `cleaning_log.json`

| 字段 | 说明 |
|-|-|
| `case_id` | 输入标识 |
| `log[]` | 动作序列 |
| `log[].step` | `load` / `normalize_columns` / `dedup` / `unit_convert` / `final` |
| `log[].rows` | 该步后的行数 |
| `log[].mapping` | 列名映射（normalize_columns） |
| `log[].keys` / `removed` | 去重键与删除数 |
| `log[].mode` / `factor` / `factors` / `rows` | 单位换算细节 |
| `log[].error` | 加载失败原因 |
| `notes[]` | 编码 / 分隔符 / 解析说明 |

## `quality_report.json`

```
inputs[]:
  source, output, input_rows, output_rows, columns, row_delta
  fields[col]:
    missing, missing_kinds{}, non_numeric, missing_action,
    unit_conversion, outliers, outlier_action,
    missing_detail[≤50], outlier_detail[≤50]
totals: {input_rows, output_rows}
```

`missing_detail` 每项：`{index, from, to, kind, action}`。
`outlier_detail` 每项：`{index, value, action, to?, bounds?}`。

## `rules_snapshot.json`

`case_id` / `rules` / `dictionary` / `units` / `workorder` / `script_version` —— 用于复跑。

## `result.json`

| 字段 | 说明 |
|-|-|
| `case_id` | 输入标识 |
| `task` | `research-data-cleaning` |
| `status` | `complete` / `partial` |
| `deliverables` | 清洗表与证据文件路径 |
| `counts` | `inputs` / `cleaned` / `input_rows` / `output_rows` / `failed_loads` |
| `uncertainty` | 限制与未处理项 |
| `sources` | 输入文件 |
| `changes` | 关键步骤 |

## `check_quality.py` 判决

`{valid, checks, issues[], case_id, status}`，`valid=false` 时退出码非零。
