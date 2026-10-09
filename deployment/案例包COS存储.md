# 案例包 COS 存储

行内 Docker 镜像默认启用 COS 案例包存储；本地 Mac 工作台默认仍使用本地存储。服务收到 ZIP 并成功导入后，会按 ZIP 的 SHA-256 写入 COS。容器启动时会列出、下载并校验尚未恢复的案例包，再重建案例清单和 Git 仓库；恢复完成后才启动 Web 与 Worker。过程不会调用模型。

普通参数保存在 `deployment/case-storage.json`。测评工作台使用独立 COS 桶；创建桶后，将该文件中的 endpoint、region 和 bucket 填为新桶的信息。前缀为 `code-review-benchmark/case-archives/v1/archives/`。密钥不写入配置和源码，由平台 Secret 注入：

- `BENCH_CASE_S3_ACCESS_KEY_ID`
- `BENCH_CASE_S3_SECRET_ACCESS_KEY`
- 使用临时凭证时提供 `BENCH_CASE_S3_SESSION_TOKEN`

平台账号需允许专用前缀内的 `ListBucket`、`GetObject`、`PutObject`；不需要删除权限。对象名使用 ZIP 的 SHA-256，重传相同文件会复用同一个对象。上传成功后会回读对象元数据确认 COS 已接收；无法确认时页面会提示重新上传同一个 ZIP。恢复时也会校验文件 SHA-256，COS 不可用或对象校验失败会阻止工作台启动，避免显示不完整的案例清单。

单包上限仍由 `BENCH_CASE_UPLOAD_MAX_MIB` 控制，默认 2048 MiB。上传的原始 ZIP 保存在 COS；解包后的 dataset 与 Git 仓库作为运行缓存保存在 `BENCH_DATA_ROOT`。如果以后将 Web 和 Worker 拆成独立容器，两边都要使用该版本镜像、相同 COS 配置和 Secret，并分别恢复案例包。
