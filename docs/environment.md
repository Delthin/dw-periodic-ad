# 环境记录

核验日期：2026-10-13。工程根目录：/Volumes/SSD/projects/dw-periodic-ad。

| 项目 | 实际版本 / 配置 |
| --- | --- |
| OS / 架构 | macOS 26.2 (25C56)，arm64 |
| JDK | Homebrew OpenJDK 17.0.20.1+0 |
| Maven | 官方二进制 3.9.11，位于 .local/tools/apache-maven-3.9.11 |
| Python | 复用 uv 已有的 CPython 3.12.12，项目 .venv |
| pandas / numpy / matplotlib | 3.0.6 / 2.5.4 / 3.11.2；完整依赖见 requirements-lock.txt |
| IoTDB | All-in-one 2.0.11，SHOW VERSION 返回 build 7c0de06 |
| 模型 / 精度 | Tree，epoch ms，Session 显式 UTC 坐标 |
| UDF API / Session | org.apache.iotdb 2.0.11；实际传递依赖 TsFile 2.4.0 |
| 实例布局 | 单机 1 ConfigNode + 1 DataNode |
| 节点内存 | 启动时 MEMORY_SIZE=1G：每节点 heap 768M、direct 256M；不是全实例共 1G |
| 实验端口 | RPC 6667；内部 10710、10720、10730、10740、10750、10760，均为 127.0.0.1 |
| HTTP/MQTT | enable_rest_service=false，enable_mqtt_service=false |
| UDF 路径 | .local/runtime/apache-iotdb-2.0.11-all-bin/ext/udf |

下载及官方 SHA-512 校验记录在 results/preparation-20261013/download-verification.json。服务端、下载包和 Maven 二进制不提交 Git。

JDK 通过 brew install openjdk@17 安装，Homebrew 自动安装/更新了所需依赖；完整日志在 .local/logs/jdk-install.log。没有修改全局 shell 配置，也没有创建系统 Java 链接。每次进入工程先 source scripts/env.sh；Maven 已核到使用同一 Java 17。

服务生命周期由 bash scripts/iotdb.sh start|stop|status|cli 管理。start 检查端口并用实际 SQL 查询验证连接；stop 在运行官方停止脚本前核对监听进程属于本工程，不强制终止其他服务。

Tree CLI 已实际连接，SHOW VERSION 为 2.0.11，SHOW CLUSTER 中两节点均 Running；文本证据见 results/preparation-20261013/cli-version-cluster.txt。运行结束后停止实例，库内数据保留。

构建存在两类已记录提示：runner 尚未配置 SLF4J provider，客户端日志默认 NOP；上游 TsFile 2.4.0 的 common 与 tsfile JAR 有 31 个重复 class，字节逐一比较全部相同。当前 SQL 链路与完整数据校验通过。发布最终 JAR 前再检查第三方许可汇总；这些提示不作为性能/算法证据。
