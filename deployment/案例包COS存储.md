# 案例包 COS 存储

行内 Docker 镜像使用 COS 保存案例资源和运行记录；本地 Mac 工作台默认仍使用本地存储。独立仓库 ZIP 上传到仓库资源池，按仓库名和 bundle SHA-256 固定版本；纯案例 ZIP 只保存案例清单和 dataset，并引用已登记仓库。更换案例数量或案例清单时，只需上传新的纯案例 ZIP。运行任务按案例集固定的仓库版本下载 bundle 并准备工作区。旧版同时包含 dataset 和 Git bundle 的 ZIP 仍可导入，导入时也会登记 bundle 到仓库资源池。

普通参数保存在 `deployment/case-storage.json`。案例资源、运行记录和模型配置复用当前测评工作台 COS 桶，各自使用独立前缀：旧版案例 ZIP 为 `code-review-benchmark/case-archives/v1/archives/`；兼容的案例清单和 bundle 位于 `code-review-benchmark/case-archives/v2/`；独立仓库版本及仓库目录位于 `code-review-benchmark/case-archives/v3/repositories/`；纯案例清单和 dataset 位于 `code-review-benchmark/case-archives/v2/datasets/`；运行记录为 `code-review-benchmark/run-data/`，模型配置为 `code-review-benchmark/model-configs/v1/profiles/`。endpoint、region 和 bucket 沿用当前值。运行数据以 SHA-256 内容对象和清单保存，每 30 秒扫描一次；持续追加的日志和原始输出按当次已写入的长度生成快照，其余文件连续 15 秒未变化后同步；容器收到正常停止信号时会再做一次最终同步。工作区仓库副本不上传，完整答卷、原始输出、评分范围、评分结果、报告、工作台任务日志和模型配置会持久化。模型配置包含 API Key，只能保存在私有 COS 桶中，并限制平台凭证仅能访问上述工作台前缀。

平台账号需在案例资源、旧案例 ZIP、运行数据和模型配置前缀具备 `ListBucket`、`GetObject`、`PutObject` 权限；案例资源前缀还需 `DeleteObject`，用于网页删除案例集和未被引用的仓库版本。运行数据和 Git bundle 使用内容寻址，更新会留下仍可被旧案例集或批次引用的资源，因此需为这些前缀申请足够容量并按组织的数据保留要求管理桶。COS 访问密钥不写入配置和源码，由平台 Secret 注入：

- `BENCH_CASE_S3_ACCESS_KEY_ID`
- `BENCH_CASE_S3_SECRET_ACCESS_KEY`
- 使用临时凭证时提供 `BENCH_CASE_S3_SESSION_TOKEN`

新格式案例资源对象名使用各文件的 SHA-256；相同 dataset 或 bundle 会复用已有对象。写入后会回读对象元数据确认 COS 已接收，清单最后写入；资源未完整保存时不会发布可恢复的案例清单。恢复时会校验对象大小和 SHA-256，COS 不可用或资源校验失败会阻止工作台启动，避免显示不完整的案例集。此前上传的标准案例 ZIP 首次恢复时会迁移成拆分资源并记录迁移标记，后续启动直接恢复清单；更早的 pilot5 专用 ZIP 继续走兼容恢复。旧版 ZIP 仍按 ZIP 的 SHA-256 去重。运行数据同步状态会显示在工作台；同步异常时请勿重建容器，等状态恢复后再更新。

新版本首次启动时，会把 `BENCH_CONFIG_ROOT` 中已有的模型配置迁移到模型配置前缀，并恢复 COS 中的配置；之后新增模型及每批次固定配置在保存时立即上传并回读校验，COS 保存失败会阻止本次保存或批次提交。已有模型配置不需要在每次重新部署后重新录入。新版本从部署后创建的批次开始同步到 COS；部署完成后产生的运行记录在后台自动保存。旧服务中未备份的记录不会迁入。

恢复时先读取仓库资源目录与案例集清单，不下载所有 bundle。测评任务开始时按案例集固定的版本下载所需 bundle；同一仓库版本在 COS 只保存一份。恢复时仅下载本地缺失的文件；本地已有文件与 COS 不同时保留本地版本，避免覆盖尚未同步的结果。同步与恢复使用同一进程间锁串行执行，网页导入会校验并保存资源到 COS。

单包上限仍由 `BENCH_CASE_UPLOAD_MAX_MIB` 控制，默认 2048 MiB。新上传的原始 ZIP 只在导入过程中暂存；COS 长期保存独立 dataset、案例记录和 Git bundle。`BENCH_DATA_ROOT` 保存本地 dataset、已下载的 bundle 缓存和运行仓库。如将 Web 和 Worker 拆成独立容器，两边必须共享可写的运行目录，Worker 启用运行数据同步，Web 和 Worker 使用相同 COS 配置和 Secret；独立容器启动后均可从 COS 恢复案例清单，Worker 在执行案例前准备需要的仓库。
