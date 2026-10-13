# 周期感知异常检测 · IoTDB 课程项目

工程位于 /Volumes/SSD/projects/dw-periodic-ad。当前完成环境、NAB 数据核验及透传 UDF 链路；异常检测核心、评价程序和正式实验尚未实现。

先看 [作业要求和后续路线](docs/start-here.md)，再看 [数据说明](docs/data.md) 与 [环境记录](docs/environment.md)。原实施说明保存在 [docs/reference/作业实施说明.md](docs/reference/作业实施说明.md)，其中原建议目录已由本工程路径替代。

## 当前已核验

- Java 17.0.20.1 / Maven 3.9.11 / Python 3.12.12 / IoTDB 2.0.11（build 7c0de06）。
- NAB 主序列 10,320 点、30 分钟间隔，原始数据和标签 SHA-256 与实施说明一致。
- Tree 模型，ConfigNode 与 DataNode 均 Running，仅监听 127.0.0.1。
- 16 点独立样例：CSV → Tablet 导入 → 注册 periodic_echo → SQL 逐点透传，时间和值完全一致；独立重复查询通过。
- 全量主序列导入后，原始查询与透传 UDF 各 10,320 点完全匹配 CSV。
- Java 6 个测试、Python 24 个数据准备测试通过。

periodic_echo 只是部署探针，不是最终 periodic_ad 异常检测函数。不要把当前透传输出当作异常分数。

## 环境恢复

本机已有 Homebrew、uv、Git。首次在新机器使用（仅面向 macOS）：

```bash
cd /Volumes/SSD/projects/dw-periodic-ad
bash scripts/bootstrap_local.sh
source scripts/env.sh
java -version
mvn -version
python --version
```

bootstrap 固定 Maven/IoTDB 版本并校验官方 SHA-512，复用或安装 JDK 17、Python 3.12.12，以 requirements-lock.txt 恢复 Python 包。仅修改本项目的 IoTDB 配置，不改 ~/.zshrc、不建立全局 Java 链接。Homebrew 安装 JDK 时可能更新所需依赖。

## 最短前期复现链路

```bash
cd /Volumes/SSD/projects/dw-periodic-ad
source scripts/env.sh

# 第一次获取数据：固定本次 NAB commit；已有原始数据可去掉下载参数
python scripts/prepare_data.py --download --skip-ls-remote
python -m unittest discover -s tests -p test_prepare_data.py -v
mvn -B -ntp verify

# 如实例已经运行，跳过 start，不重复启动
bash scripts/iotdb.sh start
bash scripts/deploy_echo.sh
java -jar runner/target/periodic-ad-runner.jar import-csv data/processed/nyc_taxi.csv
java -jar runner/target/periodic-ad-runner.jar verify-csv data/processed/nyc_taxi.csv

# 查看真实源数据（只读查询）
java -jar runner/target/periodic-ad-runner.jar query \
  'SELECT value FROM root.periodic_ad.nyc WHERE time >= 1404172800000 AND time < 1404180000000'

# 结束使用
bash scripts/iotdb.sh stop
```

预期：小样例 16 点逐点一致，主序列 10,320 点逐点一致，COUNT(value)=10320。import-csv 会向固定 root.periodic_ad.nyc.value 写入；相同时间戳重复导入会覆盖值，因此只使用已校验的处理 CSV。脚本不会删除序列或数据库。

部署脚本只 DROP/CREATE 本项目的 periodic_echo；替换前保存旧 JAR 和 SHA-256，不操作其他 UDF。IoTDB 默认本地实验账户仅供隔离实例使用；如已改密码，通过 IOTDB_PASSWORD 环境变量提供，不把密码写进提交。

## 目录与入口

- detector-core：当前只有 EchoValue，后续放周期检测核心；不依赖 IoTDB。
- detector-udf：EchoUDTF，包含 core，udf-api 为 provided。
- runner：当前 SmokeRunner 提供导入、逐点校验、只读查询；后续再增加真正 CSV 检测运行器。
- scripts / configs / sql / tests：环境、数据、SQL 和小样例。
- docs：环境、数据、实验约定、决策和任务导读。
- data/raw、data/processed、results：本机数据及证据，Git 忽略，可由命令再生成。
- .local：下载包、Maven、IoTDB 服务及日志；.venv：独立 Python 环境，均不提交。

数据总览：results/data-check/overview.png。前期检查证据：results/preparation-20261013/。

IoTDB 已正常停止后，可用上面的 start 命令恢复；服务数据保存在 .local/runtime/ 下，不要删除该目录来解决启动问题。
