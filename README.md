# 周期性监测数据的自适应异常检测

数据仓库与数据挖掘课程项目。用历史同周期位置估计正常水平，以残差中位数/MAD 评分，再以有界幅度更新相位历史。同一套 Java 核心既可重放 CSV，也可作为 Apache IoTDB 2.0.11 的逐行 UDTF 执行。

## 代码结构

| 目录 | 职责 |
|---|---|
| `detector-core/` | 参数、预热状态机、因果评分、三种更新策略及基线，无数据库依赖 |
| `detector-udf/` | `PeriodicADUDTF`：IoTDB 生命周期和 score/flag 单列输出 |
| `runner/` | CSV/batch、数据库逐点一致性、同核心 A/B 性能实验 |
| `scripts/` | 数据准备、验证段校准、事件评价、NumPy 独立核验、系统实验 |
| `configs/` | 固定参数、实验口径、数据来源和筛选记录 |
| `docs/data.md` | 主数据的时间约定、哈希和转换依据 |

`data/raw/`、`data/processed/`、`results/`、`.local/` 和构建产物不进入 Git。仓库不存任务计划、AI 过程记录或汇报草稿。

## 本机运行

仓库位于 `/Volumes/SSD/projects/dw-periodic-ad`。Java 17、Python 3.12 和 Maven 已准备；IoTDB 仅监听本机。新的 macOS 环境可运行 `bash scripts/bootstrap_local.sh`，该命令会下载依赖，必要时通过 Homebrew 安装 JDK，需要预装 Homebrew 与 uv。

```bash
cd /Volumes/SSD/projects/dw-periodic-ad
source scripts/env.sh
mvn -B -ntp verify
python -m unittest discover -s tests -v

# 已有原始数据时仅核验；首次获取添加 --download
python scripts/prepare_data.py
python scripts/prepare_supplement.py

# 使用已选参数重放；无需启动数据库
java -jar runner/target/periodic-ad-runner.jar score \
  data/processed/nyc_taxi.csv results/taxi-scores.csv configs/detector.properties

# 验证段选参 → 原样重放 → 测试事件评价 + 受控机制诊断
python scripts/experiments.py
```

完整逐点输出含 `timestamp_ms,value,baseline,residual,residual_center,scale,score,flag,update,phase`。未更新状态时 `update` 为空，不代表 0。score 是稳健标准化偏差，不是概率。

## IoTDB 部署与一致性

```bash
bash scripts/iotdb.sh status
# 仅服务未运行时执行；已有监听时不要重复 start
bash scripts/iotdb.sh start
bash scripts/deploy_detector.sh

java -Xmx256m -jar runner/target/periodic-ad-runner.jar compare \
  data/processed/nyc_taxi.csv configs/detector.properties results/database/nyc_taxi
```

部署脚本核对服务归属，备份同名 JAR，拒绝重复项目类，只重新注册 `periodic_ad`。`compare` 自动创建独立 UUID device、核验原始数据，比较 CSV 与 UDF 的每个时间戳、分数和布尔告警，并重复查询检查状态隔离；不删除已有数据。将输出的 `db_device=root.periodic_ad.xxx` 加入参数文件可只读复用已有序列。

SQL 示例见 `sql/detector.sql`。单次 UDF 查询从输入历史重新建立状态，并非跨查询持续监控服务。查询必须带足预热历史，不能先按数值过滤输入。score 和 flag 是两个 UDF 实例；性能实验只查询 score，避免双算。

## 评价口径与参数

- 主数据：NAB `realKnownCause/nyc_taxi.csv`，10,320 点，30 分钟采样；7–8 月训练、9 月 1 日–10 月 15 日验证、10 月 16 日起测试。
- `configs/experiment.json` 固定候选集合、随机种子、背景预算和事件合并间隔。验证副本使用预设扰动，幅度仅由训练段计算；真实测试标签在选参冻结后才读取。
- 本次主方法选择 P=W=336、K=6、τ=4、c=2；2,352 点预热后输出 7,968 行，完整覆盖 5,184 个测试点。
- 相邻告警间隔 ≤1 小时合并，按首次报警时间归属。官方窗口闭区间，每窗只记一次 TP，重复事件和窗口外事件计 FP。不做 point adjustment；不是 NAB 官方加权分数。
- 主比较的四个基线分别选参；机制消融固定主方法参数只改变一项。测试阈值扫描仅用于诊断，不据此回选参数。
- 原始值不插值、不删异常；时间戳决定相位。重复、倒序、离网格时间被拒绝；缺失和非有限输入跳过，允许整数倍采样间隔的缺口。

## 本次结果

| 方法 | 事件命中 | 重复 / 窗口外 FP | 事件 F1 |
|---|---:|---:|---:|
| 周期 + 有界更新 | 5/5 | 17 / 12 | 0.256 |
| 周期 + 直接更新 | 5/5 | 18 / 13 | 0.244 |
| 固定周期模型 | 5/5 | 23 / 57 | 0.111 |
| 滑动中位数 / MAD | 1/5 | 0 / 0 | 0.333 |
| 滑动均值 / 标准差 | 0/5 | 0 / 0 | 0.000 |

周期方法覆盖更多真实事件，有界更新的改善较小；它不是所有指标最优。相同参数下，有界比直接更新只减少 1 个窗口外事件。受控永久阶跃中三种策略最终都适应新水平，说明原始残差窗口也会改变中心，不能靠冻结相位历史保证持续报警。

补充候选固定为 NAB `realAdExchange` 的 6 条与 `realTraffic` 的 7 条。先检查固定间隔、严格递增、至少 8 个训练周期，再检查训练前 40% 的日周期相关系数 ≥0.5，按路径排序最多取 3 条；不使用标签筛选。13 条均未通过结构条件，未降低门槛或修改原值，因此本次没有补充序列检测结果，不能宣称跨数据集有效。

数据库内外 7,968 行时间戳、分数和 flag 完全一致；独立 NumPy 参考最大绝对差约 3.64e−12。28 个 Java、35 个 Python 测试通过。

## 性能实验

```bash
python scripts/prepare_benchmark.py
python scripts/run_system_experiments.py  # 两轮预热 + 五轮交替 AB/BA
```

A 为 SQL UDF → 完整消费 score；B 为 SQL 原始值 → 同一 Java 核心评分。二者从发出 SQL 到消费完全部结果计时，文件写入不在计时区。B 的逐点纯算法计时探针开销包含在 B 路径中。每轮比较输出数量与时间戳/score/flag 校验和。

本机暖缓存中位数（毫秒）：10,320 点 A/B = 131.6/96.5；100,000 点 = 1,235.8/1,168.0；1,000,000 点 = 12,636.4/11,890.8。数据库内执行未提速。逐点返回分数的方案只减少预热部分返回行数，不能据此声称显著减少网络流量。

`results/database/*/summary.json` 与 `timings.csv` 保存重复分布及口径；`process-resources.json` 的 1 秒 RSS/CPU 采样覆盖导入、核验、预热和测量整个命令，不是单条路径的峰值资源。没有测量实际协议字节数或数据库 UDF 的独立纯算法耗时。

## 汇报

[完整离线汇报](docs/周期异常检测项目汇报.html)包含算法细节、代码架构、实际 Java/SQL、真实数据图和结果解释。下载该 HTML 文件后用浏览器打开，图和样式均已内嵌。

原始课程要求和选题报告保留在课程目录，不复制到代码仓库。
