# 案例包 COS 存储

行内 Docker 镜像使用 COS 保存案例资源和运行记录；本地 Mac 工作台默认仍使用本地存储。新格式 ZIP 导入后，案例清单、dataset 和 Git bundle 分开保存；仓库 bundle 按内容 SHA-256 命名，同一份资源可被多个案例集复用。容器启动时先恢复批次记录、任务日志和案例清单，不下载所有 Git 历史；执行使用某个上传案例集的任务时，才下载该案例集所需 bundle 并准备仓库。旧版整包 ZIP 仍可恢复。

普通参数保存在 `deployment/case-storage.json`。案例资源、运行记录和模型配置复用当前测评工作台 COS 桶，各自使用独立前缀：旧版案例 ZIP 为 `code-review-benchmark/case-archives/v1/archives/`；拆分后的案例清单和 dataset 位于 `code-review-benchmark/case-archives/v2/datasets/`，Git bundle 位于 `code-review-benchmark/case-archives/v2/repositories/`；运行记录为 `code-review-benchmark/run-data/`，模型配置为 `code-review-benchmark/model-configs/v1/profiles/`。endpoint、region 和 bucket 沿用当前值。运行数据以 SHA-256 内容对象和清单保存，每 30 秒扫描一次；持续追加的日志和原始输出按当次已写入的长度生成快照，其余文件连续 15 秒未变化后同步；容器收到正常停止信号时会再做一次最终同步。工作区仓库副本不上传，完整答卷、原始输出、评分范围、评分结果、报告、工作台任务日志和模型配置会持久化。模型配置包含 API Key，只能保存在私有 COS 桶中，并限制平台凭证仅能访问上述工作台前缀。

平台账号需在案例资源、旧案例 ZIP、运行数据和模型配置前缀分别具备 `ListBucket`、`GetObject`、`PutObject` 权限；不需要 `DeleteObject`。运行数据和 Git bundle 使用内容寻址，更新会留下仍可被旧案例集或批次引用的资源，因此需为这些前缀申请足够容量并按组织的数据保留要求管理桶。COS 访问密钥不写入配置和源码，由平台 Secret 注入：

- `BENCH_CASE_S3_ACCESS_KEY_ID`
- `BENCH_CASE_S3_SECRET_ACCESS_KEY`
- 使用临时凭证时提供 `BENCH_CASE_S3_SESSION_TOKEN`

新格式案例资源对象名使用各文件的 SHA-256；相同 dataset 或 bundle 会复用已有对象。写入后会回读对象元数据确认 COS 已接收，清单最后写入；资源未完整保存时不会发布可恢复的案例清单。恢复时会校验对象大小和 SHA-256，COS 不可用或资源校验失败会阻止工作台启动，避免显示不完整的案例集。此前上传的标准案例 ZIP 首次恢复时会迁移成拆分资源并记录迁移标记，后续启动直接恢复清单；更早的 pilot5 专用 ZIP 继续走兼容恢复。旧版 ZIP 仍按 ZIP 的 SHA-256 去重。运行数据同步状态会显示在工作台；同步异常时请勿重建容器，等状态恢复后再更新。

新版本首次启动时，会把 `BENCH_CONFIG_ROOT` 中已有的模型配置迁移到模型配置前缀，并恢复 COS 中的配置；之后新增模型及每批次固定配置在保存时立即上传并回读校验，COS 保存失败会阻止本次保存或批次提交。已有模型配置不需要在每次重新部署后重新录入。新版本从部署后创建的批次开始同步到 COS；部署完成后产生的运行记录在后台自动保存。旧服务中未备份的记录不会迁入。

恢复时仅下载本地缺失的文件；本地已有文件与 COS 不同时保留本地版本，避免覆盖尚未同步的结果。同步与恢复使用同一进程间锁串行执行，网页导入只提交同步请求，不另起 COS 写入器。

单包上限仍由 `BENCH_CASE_UPLOAD_MAX_MIB` 控制，默认 2048 MiB。新上传的原始 ZIP 只在导入过程中暂存；COS 长期保存独立 dataset、案例记录和 Git bundle。`BENCH_DATA_ROOT` 保存本地 dataset、已下载的 bundle 缓存和运行仓库。如将 Web 和 Worker 拆成独立容器，两边必须共享可写的运行目录，Worker 启用运行数据同步，Web 和 Worker 使用相同 COS 配置和 Secret；独立容器启动后均可从 COS 恢复案例清单，Worker 在执行案例前准备需要的仓库。
