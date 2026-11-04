# 冻结证据标定与离线 robust AR（可选）

既有 EWMA 支持冻结 floor/normalizer，另可显式选择固定模型 `method=robust_ar`。Java 不训练、不标定、不选择方法。不传新参数时保持原行为；`configs/robust-evidence.properties` 和 `sql/robust-evidence.sql` 不变。

| 参数 | 默认值 | 校验与作用 |
| --- | --- | --- |
| `evidence_scale_floor` | `0` | 有限非负数，仅作为 EWMA 标准化分母的下界 |
| `evidence_normalizer` | `1` | 有限正数，仅缩放最终证据分数 |

配置从 Java record、`fromMap`、properties、runner 生成的 SQL 属性到 UDTF 贯通。原有 11/14/15/17 参数公共构造器均保留；新 20 参数构造器追加不可变 `RobustArModel`、`arLevelContext`、`arLevelScaleFloor`，默认为 `null/false/0`。

## 评分与状态边界

旧方法每个可评分点按原逻辑先算 baseline、residual、center、scale 和 raw score/flag，历史更新仍完全由原 `tau`、strategy 和 method 控制。AR 的原始历史规则另见下节。仅 EWMA 输出分支计算：

```text
z = (residual - center) / max(scale, evidence_scale_floor)
z = clamp(z, -evidence_clip, evidence_clip)   # 未设置或 none 时不截断
alpha = 2 / (evidence_span + 1)
u = (1 - alpha) * u + alpha * z
score = evidence_score = abs(u) / evidence_normalizer
flag = score > evidence_tau
```

归一化不反馈给 `u`；floor 不改写诊断列 `scale`，也不改变 epsilon、raw_score/raw_flag、相位或残差/相位历史。`score_mode=point` 完全忽略这两个有效参数，仍以 `tau` 判断；非法参数即使在 point 模式也拒绝。十列标准输出和十五列诊断输出的列名、次序均不变。

`evidence_tau` 单位是**归一化后的分数**，score/flag/event/active 和独立 CSV events 投影共享同一阈值。比较仍为严格 `>`；有限 cap 时 EWMA 配置必须满足：

```text
evidence_tau < evidence_clip / evidence_normalizer
```

例如 cap=4、norm=2 时，tau=1.5 有效，tau=2 或 3 无法到达，初始化即拒绝。norm=0.5 时 cap=4 可支持 tau=6；默认 norm=1 等同旧规则。无 cap 不设此上限。point 不做 EWMA 可达性限制。

UDTF 的 `validate` 和 `beforeStart` 都执行相同配置校验。旧方法的缺口、null 和非有限输入仍按既有规则清零证据，不清历史、不重做 warmup；只有新 AR 方法须重做全部历史 warmup。每个输出列、查询及重复 `beforeStart` 均持有新的独立 detector/gate。算术溢出仍拒绝而不输出非有限分数。

## 固定 robust AR 与 level 联合证据

训练由 Python 离线完成：训练 prefix 中心/尺度、Huber AR 系数及模型选择不进入 Java，也不读取真实标签。v8 拟合的 lag 集为排序去重的 `(1,2,3,6,12,P,2P)`；推理接收明确导出的 lag 列表，不自动补项或重排。各系数按给定顺序逐项相加，不调用 BLAS/FMA。

| 参数 | 约束/默认 |
| --- | --- |
| `ar_lags` | 必填，逗号分隔、正整数、唯一严格升序；最多 1024 项，maxLag <= 1,000,000 |
| `ar_coefficients` | 必填，同长度的有限 double，与 lags 一一对应 |
| `ar_intercept`, `ar_center` | 必填，有限 double |
| `ar_scale`, `ar_lag_clip` | 必填，有限且 > 0；v8 lag_clip=6 |
| `ar_level_context` | 严格 true/false（大小写不敏感），默认 false |
| `ar_level_scale_floor` | 有限且 >= 0，默认 0；仅联合证据用 |

`RobustArModel` 构造及数组访问器均复制数组，模型可安全共享但历史绝不共享。缺少任一模型字段、非法数、lag 重复/逆序/零、长度不匹配均拒绝。即使旧方法附带模型字段也会验证完整模型；有效但未使用的 context 参数不影响旧方法。AR 要求 `window=period=P`、`epsilon=auto`（拒绝其他值，避免偏离冻结自动 epsilon 规格）。既有 period<=100000/state-limit 仍生效；AR 的 `maxLag+2*P<=2000000`，状态内存 O(maxLag+2P+lag数)，每点预测 O(lag数)、median/MAD O(P log P)。

```text
laginput_i = clamp((x[t-lag_i]-ar_center)/ar_scale, +/- ar_lag_clip)
prediction = ar_center + ar_scale * (ar_intercept + ordered_sum(coeff_i * laginput_i))
residual = x[t] - prediction
residualCenter = median(previous P raw residuals)
residualScale = max(1.4826 * MAD(previous P raw residuals), residualEpsilon)
residualEpsilon = max(1e-9, .001 * 1.4826 * MAD(first P raw residuals))
raw_score = abs(residual-residualCenter)/residualScale
raw_flag = raw_score > tau
```

先存 maxLag 个原始 x，再存 P 个预测残差，**首个评分点为 index=maxLag+P**（从 0 计）。始终先预测/统计/评分，再追加当前原始 x 和原始 residual；不使用 phase bounded/freeze/guarded 更新，不因 raw_flag 丢样本，`update` 列记录原始 x。phase 列仍为原时间网格相位；十列/十五列 schema 不变。

独立 level 历史取前 P 个原始 x 的 rolling median/MAD，levelEpsilon 从最初 P 个 x 以同一公式初始化并冻结。若同时 `score_mode=ewma`、`ar_level_context=true`，将上面的普通 z 替换为：

```text
zAR = (residual-residualCenter)/max(residualScale,evidence_scale_floor)
zLevel = (x-levelMedian)/max(levelScale,ar_level_scale_floor)
z = sign(zAR) * sqrt(abs(zAR)*abs(zLevel))
```

随后才执行 evidence_clip、EWMA、normalizer 与统一 evidence_tau。符号仅来自 zAR，不取 zLevel 的符号；raw diagnostics 不变。context=false 时 z=zAR。legacy POINT 永不应用这些证据参数；v8 的 AR `point` 通道须配置 **score_mode=ewma, evidence_span=1, evidence_clip=none**，才能保留 floor/context/norm。

仅对 ROBUST_AR，时间不连续、null、NaN、+/-Infinity 会清空 feature/residual/level 历史、两自动 epsilon 和 EWMA，并重新 warmup；当前有限点在 gap 后作为新历史的第一个值。累计 rows/scored/skipped 不清零；时间递增和采样网格校验不变。不要将跨缺口的上一观测误作 x[t-lag]。

`configs/robust-ar.properties` 和 `sql/robust-ar.sql` 是同一个**未拟合的演示模型**，可配合测试 synthetic input 使用，不代表 NAB/UCR 的获胜参数。生产须原样导出离线模型、训练 floor、验证 norm、阈值；不允许线上更新这些常数。

## 离线标定与示例

Python 负责离线拟合、标定与选择；自家方法的候选分数和测试分数均由生产 Java 内核回放生成。仅用训练阶段的 `median(scale) * 0.5` 得到 floor 并冻结；再用验证背景上未归一化 `abs(u)` 的 99% 分位得到 norm，固定后用于测试/生产。运行时不重估、不开标签清洗、不做全序列标准化，Java 不选择 periodic/rolling_mad/robust_ar 或 span/clip。标定方须提供有限合法常量（norm 必须大于零），不能从测试集回算。

`configs/adaptive-evidence.properties` 与 `sql/adaptive-evidence.sql` 展示 floor=0.5、norm=2、cap=4、tau=1.5、span=12。这些只是互相一致的调用示例，**不是最终入选 NAB variant，也不是性能结论**。span=6/12、cap=6/4 均可直接配置。SQL 只针对已有 Tree 数据设备，需替换示例设备名；保留完整 warmup 输入，评价区间应在输出侧截取。

离线 CLI 使用方式（不连接数据库）：

```sh
java -jar runner/target/periodic-ad-runner.jar score INPUT.csv SCORES.csv configs/adaptive-evidence.properties
java -jar runner/target/periodic-ad-runner.jar score-diagnostics INPUT.csv DIAGNOSTICS.csv configs/adaptive-evidence.properties
java -jar runner/target/periodic-ad-runner.jar events SCORES.csv EVENTS.csv configs/adaptive-evidence.properties
```

关闭这两个适配项只需省略参数或设为 `evidence_scale_floor=0`、`evidence_normalizer=1`；是否启用 EWMA/输入 cap 仍由各自原参数决定。

## 可复现参考验证（不训练、不读标签、不连接数据库）

`scripts/verify_robust_ar.py` 显式从只读参考仓导入 `predictive_baseline.py` 与 v8 evidence 函数，只使用 prediction/reference_scores，不调用 fit/calibrate/choose。用参考仓现有 Python 环境运行；输出路径强制位于运行脚本所在 worktree，禁写参考仓的字节码缓存。真实回放固定一个 periodic 和两个 AR 序列（ewma6 与 span1 point），使用显式给定的本 worktree JAR。保存参考源码/配置 SHA256、冻结模型、输入 SHA256、Python/Java CSV 与逐列误差；不计算真实性能指标或读取标签文件。

```sh
PYTHONDONTWRITEBYTECODE=1 REFERENCE/.venv/bin/python scripts/verify_robust_ar.py fixtures --reference-root REFERENCE --output detector-core/src/test/resources/robust-ar
PYTHONDONTWRITEBYTECODE=1 REFERENCE/.venv/bin/python scripts/verify_robust_ar.py replay --reference-root REFERENCE --output results/robust-ar-replay --jar runner/target/periodic-ad-runner.jar
```

六个固定 synthetic fixtures 覆盖 context 开/关 × span1/6/12、cap none/6/4；Java 测试另涵盖手算、原始历史、prefix causality、gap/null/nonfinite、非法配置、旧构造器、SQL 四模式和查询隔离。历史 v8 回放中 Java/NumPy 的分数最多相差几个 ULP，因此数学参考比较使用明确小容差，三个真实回放的 flags 完全一致。当前校准与测试直接采用 Java 分数，严格校验 CSV flag 等于回读后的 score > 冻结阈值；Python 参考的阈值边界差异另行记录。真实 IoTDB 查询/部署由集成方另行复核。
