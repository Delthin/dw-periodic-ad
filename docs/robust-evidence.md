# 可选 EWMA 输入限幅

`evidence_clip` 只作用于 `score_mode=ewma` 的有符号标准化残差：

```text
z = (residual - center) / scale
z = max(-evidence_clip, min(evidence_clip, z))  # 仅启用时
alpha = 2 / (evidence_span + 1)
u = (1 - alpha) * u + alpha * z
score = abs(u)
flag = score > evidence_tau
```

省略或设为 `none`（忽略大小写和首尾空白）完全保留原行为；数值必须有限且为正。EWMA 模式还要求 `evidence_clip > evidence_tau`，否则明确拒绝不可达阈值的配置。point 模式验证数值合法性但不应用限幅，也不限制其与阈值的关系。

`configs/robust-evidence.properties` 与 `sql/robust-evidence.sql` 沿用原 NYC 的 P336/K6/W336、tau4、clip2，配置 span12、evidence_tau3、evidence_clip4。cap4 来自训练/验证阶段对 none/4/6/8 的选择，不基于测试结果选参。SQL 请替换已导入的设备路径，保留完整预热历史。

从零证据出发，单个任意大输入最多贡献 `2/13*4≈0.6154`，不会一次越过 3；连续同向饱和输入第 9 个点可越阈值。已有接近阈值的证据时，单个点仍可能触发告警。限幅旨在减轻孤立极值的拖尾误报，并不保证消除误报；短尖峰或弱偏移可能漏检。winsorization/EWMA 并非原创统计方法，这里是对流式周期残差输入的适配。

限幅不改变原始 score/rawFlag、tau 控制的更新决策、基线或残差历史；`clip` 仍是另一个独立的基线写入参数。时间缺口、null/非有限输入按原 EWMA 契约重置证据，告警 active 不因此强制清除。score/flag/event/active 统一使用所选 `scoreThreshold()`。

标准 CSV 十列及可选诊断十五列不变：`raw_score/raw_flag` 仍为未限幅的逐点值，`evidence_score` 仍为累积证据的绝对值，不是限幅后的单点 z。配置经 properties/map、UDF attributes、compare/benchmark SQL 一致传递；数据库实查需另行验证。
