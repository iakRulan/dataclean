# 缺失值、哨兵值与单位

## 三态区分（核心）

| 状态 | 含义 | 处理 |
|-|-|-|
| `missing` | 录入缺失/未记录 | 按规则 flag / zero / column_mean |
| `not_detected` | 未检出 / 低于检出限 | **绝不**当 0；标记并保留原值出处 |
| `0` | 真实测量值 0 | 原样保留 |

只有被判定为**测量列**的字段才做哨兵替换：字典 `type` 为 numeric/float/int，或该列 ≥50% 的非空值可解析为数字。文本列中的 "not detected" 是正常文本，不处理。

## 哨兵值集合

```
"", " ", "-999", "-999.0", "-999.00", "na", "n/a", "nan", "null", "none",
"nd", "n.d.", "bdl", "<lod", "< lod", "lod", "not detected",
"below detection limit", "below limit of detection", "trace", "tr"
```

其中 `nd / n.d. / bdl / <lod / lod / not detected / below detection limit / trace / tr` 归为 `not_detected`，其余归为 `missing`。

## 缺失处理动作

| 动作 | 行为 |
|-|-|
| `flag`（默认） | 置空，记录 index / 原值 / kind |
| `zero` | 仅 `missing` 置 0；`not_detected` 仍置空 |
| `column_mean` | 用该列数值均值填充，记录 |
| `none` | 不处理 |

## 单位换算

两种模式：

1. 全局因子：`{"conc": {"factor": 1000, "from": "mg/L", "to": "ug/L"}}`
2. 逐行（单位列）：`{"conc": {"unit_column": "unit", "to": "ug/L", "factors": {"mg/L": 1000, "ug/mL": 1}}}`，`"*"` 为默认因子。

换算只作用于可解析为数字的单元格；无法解析的保留原值并计入日志。

## 异常值

- 规则：IQR（1.5×IQR），样本量 ≥4 才启用。
- 动作：`flag`（默认，仅标记）/ `cap`（盖帽到上下界）/ `drop`（删除整行）/ `none`。
- 记录：列、索引、原值、边界、动作。

## 去重

按 `key_columns` 排序后 `duplicated(keep="first")`，记录删除数与键。键列不存在时只告警，不猜。

## 编码与分隔符

- 编码尝试顺序：`utf-8-sig` → `utf-8` → `gb18030` → `cp1252` → `latin-1`，最后 `utf-8 + replace`（标记有损）。
- 经典 mojibake（`Â`/`Ã`/`â€`）尝试 cp1252→utf-8 修复。
- 分隔符嗅探：`,` `\t` `;` `|` 取列数最多者。
