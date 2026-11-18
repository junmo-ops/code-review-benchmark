# dev / st 共用代码和 COS 桶

部署平台通过 `BENCH_CLUSTER_SCOPE` 选择 MySQL 批次和调度使用的 scope，同一环境的 Web 和 Worker 必须使用相同值。根据历史批次实际使用的 scope 设置该变量。

`BENCH_COS_PREFIX` 和 `BENCH_COS_RUN_PREFIX` 分别覆盖 `deployment/cluster.json` 中的资源前缀和运行数据前缀。省略环境变量时使用文件中的值。显式设置空值或包含空路径段、`.`、`..` 时会报错。已有环境应沿用原来的桶和前缀。

在 dev 部署平台配置：

```text
BENCH_CLUSTER_SCOPE=dev
BENCH_COS_PREFIX=code-review-benchmark/dev/case-archives
BENCH_COS_RUN_PREFIX=code-review-benchmark/dev/run-data
```

scope 决定数据库里的批次范围，也用于集群证据的子目录。两个 COS 前缀分别决定资源和运行数据的存储位置。

| 数据 | dev 中的路径 |
| --- | --- |
| 案例、仓库资源、Skill | code-review-benchmark/dev/case-archives/ 下的版本目录 |
| 模型配置 | code-review-benchmark/dev/model-configs/v1/profiles/ |
| 测评记录与结果 | code-review-benchmark/dev/run-data/ |

模型配置取资源前缀的父目录，因此必须按上述分层方式区分环境，不要仅使用 `code-review-benchmark/case-archives-dev`，否则模型配置仍可能共用父目录。

## 首次切换

1. 停止 dev 中运行或排队的任务，备份需要保留的数据；st 保持现状。
2. 配置两个前缀，dev 使用独立且干净的数据目录和配置目录。可分别用 `BENCH_DATA_ROOT`、`BENCH_CONFIG_ROOT` 指定，确认容器运行账户具备读写权限。不要复制 st 的 runtime、任务队列或模型配置到 dev。
3. 重启 dev 的服务和 worker。现有存储客户端在创建时读取配置，不能仅修改变量而不重启。
4. 在 dev 重新配置模型、Agent 和 Git 认证，导入一个案例做联调，再检查 COS 新对象位于 dev 前缀下，st 列表和数据不变。

本功能不迁移、删除原 COS 对象，也不会自动清空本地文件。只修改 COS 前缀却继续复用旧本地数据，可能把旧数据同步到新前缀。新前缀为空时不会自动复制 st 数据。

前缀隔离是应用读写路径隔离，不是权限隔离；同一凭证若有整桶权限，仍能访问其他路径。模型和 Agent 的额度是否共享也由各自服务配置决定。
