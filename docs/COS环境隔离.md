# dev / st 共用代码和 COS 桶

存储前缀支持部署环境变量覆盖，优先于 `deployment/case-storage.json` 的 `s3` 配置。没有设置环境变量时完全沿用配置文件，st 无需修改。显式设置为空或包含空路径段、`.`、`..` 时会报错，不回退到默认位置。

在 dev 部署平台配置：

```text
BENCH_COS_PREFIX=code-review-benchmark/dev/case-archives
BENCH_COS_RUN_PREFIX=code-review-benchmark/dev/run-data
```

两项均需设置才能完整隔离资源和运行记录；它们不改变桶、地址、认证方式或 local/cos 存储模式。

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
