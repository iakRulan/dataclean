# dataclean — 科研数据清洗与处理 Skill

把一张脏表洗成能直接支撑下游统计的表，并且**证明每一步**：缺失值/未检出/真零分开处理、按真实主键去重、按显式映射换算单位、异常值有规则有记录、质量报告与输出逐字段一致。

## 解决什么问题

普通 Agent 做数据清洗的常见失败：不读数据字典；缺失值一律列均值填充，把"未检测"和"检测为 0"混为一谈；只删完全相同的整行；单位换算丢精度或把日期转成文本；质量报告用自然语言描述，数字和结果表对不上；失败时直接覆盖原文件。

## 安装与依赖

- Python 3.9+
- pandas / numpy / openpyxl（Excel 输入需要）
- 无需联网

```
dataclean/
├── SKILL.md
├── README.md
├── GITHUB_URL.txt
├── references/
│   ├── sentinels-and-units.md
│   └── output-schema.md
├── examples/
│   ├── case.example.json
│   └── selftest-summary.md
└── scripts/
    ├── clean.py
    └── check_quality.py
```

## 用法（最小示例）

```bash
python3 scripts/clean.py --case /input/case.json --work /app/work
python3 scripts/check_quality.py --case /input/case.json --work /app/work
```

输入示例：

```json
{
  "case_id": "demo",
  "inputs": ["data.csv"],
  "dictionary": {"conc": {"type": "numeric", "missing": "flag", "outlier": "flag"}},
  "rules": {"key_columns": ["sample_id"], "missing": "flag", "outlier": "flag"},
  "units": {"conc": {"unit_column": "unit", "to": "ug/L", "factors": {"mg/L": 1000, "ug/mL": 1}}}
}
```

> 以上为**示意示例**，不是正式案例结果。

## 输出

| 文件 | 内容 |
|-|-|
| `cleaned_*.csv` | 清洗后数据（UTF-8-BOM，Excel 可直接打开） |
| `cleaning_log.json` | 每个动作：列名映射、去重、单位换算、行列变化 |
| `quality_report.json` | 字段级缺失/异常/转换统计，与输出一致 |
| `rules_snapshot.json` | 本次使用的规则、字典、单位映射、脚本版本 |
| `result.json` | `case_id` / `status` / counts / uncertainty / sources |

## 关键设计

- **字典优先**：字段语义来自案例，不猜。
- **三态缺失**：`missing` / `not_detected` / 真值 `0` 分开。
- **不毁原件**：源文件只读，输出新文件。
- **无静默跳过**：每个替换/删除/盖帽都进日志。
- **报告一致**：`check_quality.py` 独立重算并比对。

## 局限

- 异常值用 IQR 规则，不代表领域判据；
- 不做统计建模与假设检验；
- 复杂字段的语义（如"未检测"的判定）仍需字典给出。

## 许可与版本

- 版本：dataclean 1.0.0
- 依赖：pandas、numpy、openpyxl。
