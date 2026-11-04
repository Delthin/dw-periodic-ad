# 周期感知与快慢互补融合异常检测

数据仓库与数据挖掘课程项目 · 第 9 组

李思懿（组长）、翁汇坤

本项目在 Apache IoTDB 2.0.11 Tree 模型上实现因果在线检测组件，并在 Python 中完成快慢证据融合。最终实验使用 **NAB 的 21 条真实序列、3 类业务场景、22 个测试窗口**；不把它们称为 21 个独立数据集。

## 最终提交材料

确认后的报告和 PPT 与代码一起存放在 [`deliverables/final/`](deliverables/final/)。

| 作业要求 | 仓库中的材料 |
| --- | --- |
| 展示 PPT | [12 页 PPT](deliverables/final/9-李思懿-周期时间序列异常检测.pptx) · [PDF 预览](deliverables/final/9-李思懿-周期时间序列异常检测.pdf) |
| 实现代码和说明文档 | 本仓库三个 Java 模块与 `scripts/`；[正式报告 PDF](deliverables/final/9-李思懿-周期时间序列异常检测-说明文档.pdf) · [DOCX](deliverables/final/9-李思懿-周期时间序列异常检测-说明文档.docx) · [代码使用说明](docs/最终版-代码使用说明.md) |
| 测试用例和实验描述 | [`tests/` 和各模块测试](tests/)；[实验描述 PDF](deliverables/final/9-李思懿-周期时间序列异常检测-测试用例与实验描述.pdf) · [Markdown](docs/最终版-测试用例与实验描述.md) |
| 来源与结果依据 | [论文与实现来源](deliverables/final/附录/论文与实现来源.md) · [候选数据来源](deliverables/final/附录/候选序列与来源.csv) · [全部逐窗口结果](deliverables/final/附录/逐窗口对比.csv) |

PPT 直接在原项目汇报的 12 页页面和图形上修改，保留流程图、周期示意、公式、SQL 时序图及原版式。讲稿不是本次必交项。每组由组长提交一份。

## 主要结果

| 方法 | TP | FP | FN | 事件 F1 |
| --- | ---: | ---: | ---: | ---: |
| 最终快慢融合 | 15 | 21 | 7 | **0.5172** |
| Isolation Forest | 13 | 28 | 9 | 0.4127 |
| LOF | 13 | 38 | 9 | 0.3562 |
| Spectral Residual | 12 | 89 | 10 | 0.1951 |
| Left Matrix Profile | 12 | 55 | 10 | 0.2697 |

最终融合相对自适应单路，命中总数保持 15，事件误报从 43 降至 21。具体命中窗口有所变化，点级误报率和部分子组仍未领先。NAB 已用于方法迭代，以上为开发比较，不是新盲测或通用最优结论。

真实 IoTDB 对照中，AR 的 3,168 个有效评分点和周期组件的 8,976 点，四种输出均与库外逐行一致。1 万、10 万、100 万点的 AR 成本实验未观察到 UDF 加速；测量不包括 Python 融合时延。

## 代码结构

- `detector-core/`：周期中位数、有界历史更新、MAD、EWMA、稳健 AR 在线评分。
- `detector-udf/`：IoTDB Tree UDF；`score/flag/event/active` 每次调用输出其中一列。
- `runner/`：CSV 评分、数据库一致性与执行成本对照。
- `scripts/benchmark_iteration2.py`：统一拟合、校准、参数冻结和组件/外部基线评价。
- `scripts/nab_fusion.py`：阈值归一化、两种均值融合及验证模式选择。
- `scripts/run_submission_experiment.py`：从原始 NAB 数据完整重跑最终实验。
- `scripts/reproduce_submission.py`：从离线归档的无损分数重选模式、融合并评价。
- `configs/expanded-manifest.json`：47 条真实候选的纳入/排除原因与哈希。

**最终融合在 Python 中完成。** Java/UDF 支持组成融合的单路组件，尚不把完整融合称为库内单次查询。`periodic_echo` 仅为历史部署探针。

## 从 Git 克隆后完整重跑

需要 Java 17、Maven 3.9.x、Python 3.12。安装到普通本地目录，不在云盘中编译或运行。以下命令不会启动数据库：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
mvn -DskipTests package
.venv/bin/python scripts/prepare_expanded.py --download
.venv/bin/python scripts/run_submission_experiment.py
```

`--download` 从固定 NAB 官方版本下载并校验数据。完整入口在已有 `results/iteration2/final-nab/selection.json` 时拒绝覆盖，应使用新克隆目录。结果位于 `results/iteration2/final-nab/` 和 `results/refit-fusion/`。重新构建的 JAR 使用新的冻结指纹，不应手改旧归档的指纹。

原始数据、虚拟环境、编译产物、完整训练模型和中间实验日志不纳入 Git。离线“代码与数据”和“实验结果”两个 ZIP 随课程提交包提供；合并解压后，可以使用 `reproduce_submission.py` 快速回放，无需重新拟合或启动数据库。归档回放已验证 21 条序列、231 项指标一致；该结果不代表在新机器重新安装依赖和训练。

IoTDB 安装、部署、现有检查与离线归档说明见[代码使用说明](docs/最终版-代码使用说明.md)。全部依赖版本固定在 `requirements-lock.txt`，文档生成依赖另见 `requirements-docs.txt`。

## 来源与评价口径

数据源：Numenta/NAB，固定版本 `ea702d75cc2258d9d7dd35ca8e5e2539d71f3140`，MIT 许可。仅按结构规则纳入序列，真实标签只用于评价，输入不按标签清洗。时间戳按 UTC 坐标编码，不声明原业务时区。

评价在完整可评分历史上按 1 小时间隔合并报警，按事件起点归属测试区间和标签窗口；每窗首警为 TP，重复和窗口外事件为 FP。事件 F1 不等同于官方 NAB score，不使用 point adjustment。IF/LOF、SR、Matrix Profile/DAMP 论文与实际实现的区别均在[来源文档](docs/最终版-论文与实现来源.md)中列明。

旧项目阶段的 HTML 和其他历史文件仍保留在 Git 历史中；当前提交以 `deliverables/final/` 及 `docs/最终版-*.md` 为准。
