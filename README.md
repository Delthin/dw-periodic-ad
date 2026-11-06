# 周期感知与快慢互补融合异常检测

数据仓库与数据挖掘课程项目 · 第 9 组：李思懿（组长）、翁汇坤。

最终实验采用 NAB 的 21 条真实序列、3 类业务场景、22 个测试窗口。最终融合 TP/FP/FN 为 15/21/7，事件 F1 为 0.5172。Java/UDF 实现单路检测组件，融合在 Python 中完成。

## 提交材料

只保留三件：展示 PPTX、单个 Markdown 说明文档（含测试用例与实验描述）、代码仓库。

| 内容 | 入口 |
| --- | --- |
| 大作业展示 | [PPTX](deliverables/final/9-李思懿-周期时间序列异常检测.pptx) |
| 说明文档 | [Markdown](deliverables/final/9-李思懿-周期时间序列异常检测-说明文档.md) |

## 代码入口

- `detector-core/`：周期预测、MAD、EWMA 与稳健 AR 评分。
- `detector-udf/`：IoTDB Tree UDTF。
- `runner/`：CSV 运行、库内外对照。
- `scripts/run_submission_experiment.py`：从原始数据完整重跑。
- `scripts/reproduce_submission.py`：使用本地已有分数回放实验。
- `tests/` 与各模块 `src/test/`：已有测试用例。

环境、运行步骤及结果解释见说明文档第 2.4 节。原始数据、模型和运行缓存不随 Git 提交；NAB 已用于方法开发，结果不作为新盲测或跨基准泛化结论。
