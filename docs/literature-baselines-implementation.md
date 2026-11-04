# 因果时序文献 baseline（独立 NumPy 实现）

实现：`scripts/literature_baselines.py`；数学参考与回归检查：`tests/test_literature_baselines.py`。仅依赖 NumPy 和标准库，没有 fit、标签、训练/测试分支或全序列统计；不改写输入。返回输入等长的 `float64`，warmup 为 `NaN`，其余为有限非负分数。输入必须是一维有限实数，非有限值（即使在末尾）直接拒绝。

## 来源与身份边界

- Ren et al., *Time-Series Anomaly Detection Service at Microsoft*, KDD 2019, DOI [10.1145/3292500.3330680](https://doi.org/10.1145/3292500.3330680)，[论文 §4.1、§5.3](https://arxiv.org/pdf/1906.03821)：SR 方程 (1)–(9)、m=5、κ=5、q=3、z=21。
- Yeh et al., *Matrix Profile I*, ICDM 2016, DOI [10.1109/ICDM.2016.0179](https://doi.org/10.1109/ICDM.2016.0179)：z-normalized 欧氏距离与 nearest-neighbor profile。
- Lu et al., *Matrix Profile XXIV: Scaling Time Series Anomaly Detection to Trillions of Datapoints and Ultra-fast Arriving Data Streams*, KDD 2022，DOI [10.1145/3534678.3539271](https://doi.org/10.1145/3534678.3539271)，[长版 §3 Definitions 5–8、§5.3](https://www.cs.ucr.edu/~eamonn/DAMP_long_version.pdf)：non-self match、left-profile、有界历史动机。

本实现按数学定义独立编写，未复制微软源码。微软仓库定位版本为 `a3260ea0ddfb868986b924a245e003a97143f9df`；其代码不作为这里的再分发内容或测试 oracle。这里不是 SR-CNN、微软官方 API 的逐位复刻，也不是完整 DAMP。

## SR：`spectral_residual_online(values, window=64, mag_window=3, score_window=21)`

以下均采用零起点索引。在每个 t，单独取 `x[t-window+1:t+1]`，此前不评分；首个有效位置为 `window-1`。要求整数 `window>=12`，`1<=mag_window<=window+5`，`score_window>=1`（拒绝 bool 和非整数）。

1. 最近五条 secant slope 的平均值 `g = mean((x[t]-x[t-i])/i, i=1..5)`。
2. 按论文方程 (9)，`estimate = x[t-4] + 5*g`；不是 `x[t]+g`。将这个相同估计值重复五次加在尾端（m/κ=5/5），不读取真实未来。
3. `F=FFT(padded)`，`L=log(max(abs(F), epsilon_A))`，`R=L-trailing_mean(L, mag_window)`，`S=abs(IFFT(exp(R+i*angle(F))))`。频域从第一个 bin 起做非环绕 trailing average；开头不足 q 个 bin 时用已有 bin 的均值。
4. 从**本次重算**的 S 中取最后一个真实位置 `S[window-1]`。`mu=mean(S[max(0,window-score_window):window])`，返回 `abs(S[window-1]-mu)/mu`。均值包含当前真实点，不含任何估计点；z 大于 window 时截到窗口开头。不是跨时刻缓存旧 saliency，也不是提前给窗首评分。

数值约定：每个观测窗口先用二次幂缩放避免 FFT/外推溢出；这不是拟合或数据集归一化。幅值下限为该次 FFT 最大幅值的 `1e-12`（至少 float64 tiny），零 bin 的相位由 NumPy angle 定义；saliency 用复数逆变换的模，不平方。严格常量窗口分数为 0，mu=0 时也为 0。局部缩放及相对幅值下限使非退化结果保持幅值尺度不变。

实现差异：论文对边界、零幅值、常量和均值包含端点的描述不足以唯一确定代码；这里明确采用任务指定的 trailing-inclusive 最近 z 点、绝对相对偏差，并指定上述数值规则。保留**未除以 10、未 clip 到 1**的原始分数，允许分数大于 1；它不同于官方 bounded score，也不包含 CNN、官方 API 阈值或告警策略。

## Left MP：`left_matrix_profile(values, window, history=2048, exclusion=None)`

令 m=window、query 起点 `i=t-m+1`。对每个 i，候选起点严格为闭区间：

```
max(0, i-exclusion-history+1) <= j <= i-exclusion
```

要求整数 `m>=8`、`history>=1`、`exclusion>=m`（默认 m）。history 是**最新 eligible 候选起点的个数上限**，不是从 i 向前数 history 个原始点；所以 history<m 仍合法。默认最后候选起点为 `i-m`，其尾端为 `i-1`，与 query 完全不重叠；较大 exclusion 额外留空。不存在候选时保留 NaN，首个有效输出位置为 `m+exclusion-1`。

每个窗口独立按 population 标准差（ddof=0）z-normalize，取所有合法左侧窗口的欧氏距离最小值。严格常量/常量（即使常量值不同）距离为 0；常量/非常量为 `sqrt(m)`；不使用人为的“近常量”阈值。不返回 nearest-neighbor 索引，也不从右侧搜索。

数值与执行：窗口局部二次幂缩放、减首值再中心化，避免 `E[x²]-E[x]²` 的高偏置灾难消减；再局部缩放并计算单位向量。使用有界 query/reference 分块矩阵乘法寻找最邻近候选，最终用向量差直接重算距离；近零距离按 `64*eps*m` 误差带重算所有数值并列候选，避免重复周期被消减误差误报。分块可能同时装载多个 query，但各窗口统计独立，候选 mask 严格执行上述区间，不让后一 query 的观测进入前一 query 的距离。

这是**有界参考集上的 exact left-profile**，对应 DAMP 长版 §5.3 所讨论的 X-Lag-Amnesic 有界历史思想；不是全历史 exact profile。除合法候选范围外不做 DAMP early abandonment、best-so-far 近似、forward pruning 或 lookahead；每个合法候选都参与比较。主要运算量 `O(N*history*m)`，临时空间按窗口长度分块，不物化 `N*history*m` 张量。

## 核验

在仓库根目录，以项目解释器运行：

```sh
.venv/bin/python -m unittest discover -s tests -p test_literature_baselines.py -v
```

直接参考不调用生产私有函数：SR 逐窗口/逐频点直译公式；MP 逐 query、逐候选、逐窗口独立 z-normalize，最坏 `O(N²m)`。覆盖随机、周期、常量、level shift；未来替换及截断的 prefix invariance（跨批次边界）；warmup/极短输入/尾端冲击对齐；history/exclusion 与 reference 分块边界；大偏置、小振幅、极端有限数、精确重复窗口；非法参数及非有限输入。只在有限输入域要求因果分数，整数组输入合法性检查不改变此数学依赖。

实测命令、退出码、合成 4k/12k/223k 点性能记录放在被忽略的 `results/literature-baselines/`。这些是吞吐与数学正确性检查，不是 NAB 原始数据评价、官方输出等价性或异常检测精度结论。
