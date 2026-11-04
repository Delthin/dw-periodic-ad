# 因果 EWMA 时间证据：工程契约

目的：聚合同方向、持续的周期残差，减少逐点越阈值的碎片化。仅增加每实例 O(1) 状态，不改变周期基线、MAD 或 bounded 更新规则。不是概率模型，也不是新周期估计器。

## 参数与输出

| 参数 | 默认 | 约束 / 语义 |
|---|---|---|
| `score_mode` | `point` | `point` 或 `ewma`；与 UDF `mode` 独立 |
| `evidence_span` | `12` | 正整数，最大 Java int；`alpha=2/(span+1)` |
| `evidence_tau` | `3` | 有限正数，EWMA 的有效阈值 |
| `tau` | `4` | 原始点阈值；无论输出模式，都控制 FREEZE/GUARDED 原始状态写入 |
| `mode`（UDF） | `score` | `score` / `flag` / `event` / `active` |
| `exit_ratio` | `0.5` | (0,1]，安全恢复阈值 = 此比例 × **所选分数阈值** |
| `recovery_ms` | `step_ms` | >= step_ms 且为其整数倍 |

即使在 point 模式，显式提供的无效 evidence 参数也拒绝。旧 DetectorConfig 11 参数构造器、Detection 10 参数构造器和默认 CSV 接口保留；省略新参数完全保持 point 行为。

在每个正式评分点，用旧状态得到 `z=(residual-residual_center)/scale`，原始分数 `abs(z)`；EWMA 从零开始：

```
u = (1-alpha)*u + alpha*z
score = abs(u)
flag = score > evidence_tau
```

是有符号 z 的聚合，不是 abs(z) 的平滑；相反方向会抵消。无额外 EWMA 预热、无未来值。相邻已评分时间戳差不等于 step_ms 则从零重启；null/NaN/Infinity 跳过、不输出且重置证据，但不重建原始历史。重复/倒序/离网格时间仍拒绝。整个 detector/UDF 实例独立；每次查询与 beforeStart 都创建新状态。

`Detection.score()/flag()` 始终代表所选模式；`rawScore()/rawFlag()` 追踪原始分数与更新决策；`evidenceScore()` 在 EWMA 模式为 abs(u)，point 为 null。`DetectorConfig.scoreThreshold()` 给出当前输出阈值。FREEZE/GUARDED 只依照原始分数 > tau 决定是否更新，不能将聚合 flag 反馈进去。其他原始状态和 warmup 都不改变。

UDF 的 score 为 DOUBLE，其他三种为 BOOLEAN；flag 是逐点越阈值，event 仅在状态进入时 true，active 是滞回状态。EWMA 的 event/active 使用 evidence_tau，point 使用 tau。缺口只打断告警层的连续安全恢复，**不强行清除 active**（与原告警契约一致）。因此 EWMA 证据重置不等于通知状态重置。

## CSV 与 SQL

```bash
source scripts/env.sh
# 十列形状不变；score/flag 为所选输出模式
java -jar runner/target/periodic-ad-runner.jar score \
  data/processed/nyc_taxi.csv results/ewma-scores.csv configs/temporal-evidence.properties
# 显式追加 raw_score,raw_flag,evidence_score,score_mode,score_threshold 五列
java -jar runner/target/periodic-ad-runner.jar score-diagnostics \
  data/processed/nyc_taxi.csv results/ewma-diagnostics.csv configs/temporal-evidence.properties
java -jar runner/target/periodic-ad-runner.jar events \
  results/ewma-diagnostics.csv results/ewma-events.csv configs/temporal-evidence.properties
# 真实数据库 score/flag/event/active 全点比较，重复两次查询
java -Xmx256m -jar runner/target/periodic-ad-runner.jar compare \
  data/processed/nyc_taxi.csv configs/temporal-evidence.properties results/ewma-db
```

`batch` 读取每行自己的配置，状态不共享。`events` 同时支持原十列和诊断十五列；诊断 CSV 的有效阈值与配置不一致会拒绝并保留原输出。原十列没有模式元数据，调用方必须使用配套配置，不能把 point 的 alarm-example.properties 套在 EWMA CSV 上。告警 CSV 保留历史列名 `raw_flag`，其含义是**输入 score 的越阈值结果**，并非 Detection.rawFlag（EWMA 时二者不同）。

SQL 完整示例：[sql/temporal-evidence.sql](../sql/temporal-evidence.sql)。四列各自执行一遍检测，不应把这种验证查询当作单实例性能测试。输入必须先包含全部历史和 warmup，再在消费输出时选择测试区间；不要在 UDF 前截掉训练历史。

## 研究适用边界

这是已看过 NYC 测试数据后的探索性复核，不是未见测试集上的确认性提升。固定原有 P=W336、K6、tau4、clip2，span12/evidence_tau3 的 NYC 测试事件结果为：命中 5/5，FP 29→12（窗口外 12→3、重复 17→9），事件 F1 .2564→.4545；首次告警相对 point 延后 1/2/1.5/2/1 小时。窗口点级 proxy TP/FP 258/32→317/11 不是精确异常点标签。短至 0.5h/3h 的异常和部分弱偏移可能丢失；不能声称通用提升。事件评价的一小时合并规则与在线 event/active 告警门是不同概念，不能互换指标。

现有 130 条机制合成结果可在 `results/temporal-evidence/mechanism-summary.json` 查看，不需要重跑。新增 Java/数据库逐点复核产物统一在 `results/temporal-evidence/java-integration/`，不提交原始大数据或运行日志。没有为新模式测量百万点性能，不沿用旧模式的性能结论。

## 本机实际复核

- Java 17 离线 Maven `package`，54 个 Java 测试通过；覆盖手算有符号递推、严格阈值、所有 method/strategy 的原始状态不变、缺口/null/非有限值、参数越界、独立实例、UDF 生命周期重启、CSV/SQL 的有效阈值契约。
- NYC 10,320 个输入点、2,352 个预热点、7,968 个评分点。默认 point CSV 与历史原始输出 SHA256 相同；EWMA 的原始统计/更新字段完全相同。对 Python/Pandas 原型分数最大绝对差 5.33e−15，flag 无差异；重新计算事件指标精确复现上述结果。
- 真实本地 IoTDB 2.0.11：point、EWMA 各 7,968 点 × 四 mode × 两次独立查询，DB/Java 分数最大误差 0，flag/event/active 无差异。使用同一个独立 device，第二配置只读复用并先全量核对原始值；未删除既有数据。
- `verification.json` 保存独立审计；`db-ewma/comparison.json`、`db-point/comparison.json` 及各自 `comparison_1/2.csv`、`alarms_1/2.csv` 保存全量证据。部署脚本保存原 JAR，备份路径和新 JAR 哈希记录在 `deploy.log`。
