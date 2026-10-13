# 数据说明（NAB nyc_taxi）

本文档记录主序列与标签的来源、时间编码、核验结果、划分与标签覆盖，以及对数据做的与**没有**做的处理。

> 状态：本文件由 `scripts/prepare_data.py` 的机械数据准备产出。**异常检测算法尚未实现**；此处不含任何检测分数、指标或结论。标签仅供后续评价使用，未参与任何转换或清洗。

## 1. 来源与许可

| 项 | 值 |
| --- | --- |
| 数据集 | NAB `data/realKnownCause/nyc_taxi.csv`（NYC 出租车乘客数，30 分钟桶） |
| 标签 | NAB `labels/combined_windows.json`，键 `realKnownCause/nyc_taxi.csv` |
| 仓库 | https://github.com/numenta/NAB |
| 固定 commit | `ea702d75cc2258d9d7dd35ca8e5e2539d71f3140`（2026-10-13 首次下载时用 `git ls-remote` 解析 master 得到） |
| 获取方式 | `python scripts/prepare_data.py --download`（固定 commit 的 raw URL） |
| 许可 | Numenta Anomaly Benchmark (NAB) license，MIT-style；`Copyright 2014-2024 Numenta Inc.` |
| 许可出处 | https://raw.githubusercontent.com/numenta/NAB/ea702d75cc2258d9d7dd35ca8e5e2539d71f3140/LICENSE.txt |
| 数据说明出处 | https://raw.githubusercontent.com/numenta/NAB/ea702d75cc2258d9d7dd35ca8e5e2539d71f3140/data/README.md |

每个原始文件的 URL、获取 UTC 时间、字节数与 SHA-256 记录在 `configs/data-manifest.json`。

已核验 SHA-256（与课程资料一致，未作修改）：

| 文件 | SHA-256 |
| --- | --- |
| `nyc_taxi.csv` | `d8fa6f7f0734bf5c8be12c52a94e20a82664c397d9dec4449156bd453d32856d` |
| `combined_windows.json` | `1e1fbc4601321aad8d0f8b3784c8134299379f68f6c1f7777565f8ffd57ab6b1` |
| `LICENSE.txt` | `0a0b4d0b10cb1f7ed9ab2993ef93defc03447e6eba9daca1315dd32dae4877e3` |
| `data_README.md` | `1d32963e44a52323b44affff374a1222ebe5ef19ef8f7f6c6fd7a747aa52db30` |

原始文件按原字节保存在被 Git 忽略的 `data/raw/` 下，不改写。若将来远程 master 文件变化导致哈希不同，脚本会**明确报错并停止**，不会静默改期望值或换来源。

## 2. 时间编码：技术坐标 ≠ 业务时区

源文件的时间戳文本**不带时区**。为让同一文本在任意机器上得到相同整数，本项目采用统一技术约定：

> **把无时区的源时间按 UTC 坐标编码为 epoch 毫秒**（"UTC-coordinate encoding"）。

- 初始时刻：`2014-07-01 00:00:00` → `1404172800000`。
- 采样间隔：`1800000` ms（30 分钟）。
- 编码只使用 `utc=True` 的解释，**不读取本机时区**，也不做夏令时换算。

**重要区分：** 这只是技术时间坐标，**不表示原始业务时间真实属于 UTC**。本项目不推断源时间的真实业务时区，也不进行 America/New_York 夏令时换算。评价与告警都必须使用与数据完全一致的编码；标签窗口用相同编码转换，保证点与窗口可比。

## 3. 主序列核验

- 行数：10,320。
- 范围：`2014-07-01 00:00:00` 至 `2015-01-31 23:30:00`（epoch `1404172800000` 至 `1422747000000`）。
- 核验通过项：数值有限；无空值/空时间戳；时间戳无重复；严格递增；固定 30 分钟网格。

准备脚本将这些检查作为硬前置条件。任一失败即报错退出，**不生成任何处理产物**。

处理产物 `data/processed/nyc_taxi.csv` 字段：

| 列 | 说明 |
| --- | --- |
| `timestamp_ms` | UTC 坐标编码的 epoch 毫秒（int64） |
| `source_timestamp` | 原始文本时间（原样保留） |
| `value` | 原始乘客数（未缩放） |

## 4. 数据划分

半开区间边界（源时间与 epoch 毫秒）：

| 划分 | 起始源时间 | 结束源时间（不含） | 起始 ms | 结束 ms（不含） | 行数 |
| --- | --- | --- | --- | --- | --- |
| train | 2014-07-01 00:00:00 | 2014-09-01 00:00:00 | 1404172800000 | 1409529600000 | 2976 |
| validation | 2014-09-01 00:00:00 | 2014-10-16 00:00:00 | 1409529600000 | 1413417600000 | 2160 |
| test | 2014-10-16 00:00:00 | 2015-02-01 00:00:00 | 1413417600000 | 1422748800000 | 5184 |
| 合计 | | | | | 10320 |

边界记录在 `data/processed/splits.json`。

## 5. 标签窗口与覆盖

标签键 `realKnownCause/nyc_taxi.csv` 共 **5** 个窗口。以下源窗口直接来自 `combined_windows.json`，epoch 毫秒为同一 UTC 坐标编码；本项目将标签窗口视为**闭区间**（`start <= t <= end`），本文件的覆盖统计也按闭区间计算。

| # | 源窗口起始 | 源窗口结束 | 起始 ms | 结束 ms |
| --- | --- | --- | --- | --- |
| 0 | 2014-10-30 15:30:00 | 2014-11-03 22:30:00 | 1414683000000 | 1415053800000 |
| 1 | 2014-11-25 12:00:00 | 2014-11-29 19:00:00 | 1416916800000 | 1417287600000 |
| 2 | 2014-12-23 11:30:00 | 2014-12-27 18:30:00 | 1419334200000 | 1419705000000 |
| 3 | 2014-12-29 21:30:00 | 2015-01-03 04:30:00 | 1419888600000 | 1420259400000 |
| 4 | 2015-01-24 20:30:00 | 2015-01-29 03:30:00 | 1422131400000 | 1422502200000 |

> **这些窗口是 NAB 依据已知事件（马拉松、感恩节、圣诞、元旦、暴雪）给出的标注区间，不是精确的故障起点。** 后续评价不应把窗口边界当作真实异常的发生时刻；真实事件的"检测延迟"只在有明确起点的注入实验中才有意义。

划分内标签覆盖（闭区间计数）：

| 划分 | 窗口内点数 | 覆盖窗口数 |
| --- | --- | --- |
| train | 0 | 0 |
| validation | 0 | 0 |
| test | 1035 | 5 |

即全部 5 个真实标签窗口都落在 test 段，train / validation 段不含真实标签点。这符合实施说明中"测试段覆盖真实事件"的设定。

## 6. 仅用训练段的周期相关性

仅用 train 段（2976 点）计算，未使用 validation/test，也**未使用任何标签**：

| lag | 步数 | 物理含义 | Pearson r |
| --- | --- | --- | --- |
| 48 | 48 | 24 小时（日周期） | 0.8484 |
| 336 | 336 | 7 天（周周期） | 0.9670 |

结论：该序列在训练段同时存在明显的日周期与更强的周周期，与选题报告一致。这里只报告相关系数，不据此选择模型超参数。

## 7. 做了什么 / 没做什么

**只做的处理：** 下载原始字节并校验哈希；把源时间按 UTC 坐标编码；结构性核验；格式转换为 `timestamp_ms, source_timestamp, value`；导出标签窗口与划分；绘制总览图。

**明确没有做：** 填补缺失、平滑、整段标准化、按标签清洗或剔除输入、重采样、排序修复、时区转换。

**算法状态：** 周期感知检测器与 IoTDB UDF **尚未实现**，本文档不含检测结果。`results/data-check/` 只保存数据核验产物。

## 8. 产物与复现

被 Git 忽略、由准备脚本生成的产物：

- `data/raw/{nyc_taxi.csv,combined_windows.json,data_README.md,LICENSE.txt}`
- `data/processed/{nyc_taxi.csv,events.json,splits.json}`
- `results/data-check/{summary.json,overview.png}`

提交的元数据：`configs/data-manifest.json`。

复现命令（入口可从任意 cwd 定位仓库根）：

```bash
# 固定本次获取的 commit，下载四个原始文件、校验并转换
python scripts/prepare_data.py --download --skip-ls-remote

# 之后：只核验本地原始文件并转换
python scripts/prepare_data.py

# 单元测试
python -m unittest discover -s tests -v
```

## 9. 待确认项

- 标签窗口的闭/开区间口径若助教另有规定，需同步调整覆盖统计与评价程序。
- 源数据未标真实业务时区；若课程要求按当地时间分析，应核查来源后单独记录新的技术约定。
