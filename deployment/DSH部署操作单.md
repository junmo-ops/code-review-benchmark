# DSH + Skill 部署操作单

本页按你在 2026-10-23 选择的方案 2：**DSH 直接在现有 POD 内运行，不再额外套 Bubblewrap。** 不需要准备 RPM、不需要安装 Bubblewrap，也不需要修改 POD 的 namespace、特权或安全策略。

仍是现有一个 Python 容器，域名、端口、登录、COS 和模型配置沿用原服务。下面是从当前旧版更新到新版的步骤。

## 先明确运行边界

POD 模式保留每案例独立快照和 HOME、只读工具、路径检查、禁用 Shell/脚本/写入/联网工具、固定模型转发和进程树清理。**这些是软件限制，不提供操作系统级文件或网络隔离。** 同账号进程仍可能访问同容器数据；只在接受这一边界的专用测评 POD 使用。

运行模式会进入队列、冻结配置、答卷、执行记录和报告。页面显示“POD 内运行，无操作系统级沙盒”；不能把它的结果和沙盒模式当作完全相同的执行条件。模式变化须新建批次，不会把沙盒失败自动降级成 POD 模式。

## 1. 准备一个依赖文件

**本次工作区已生成 `deployment/offline/dsh-npm.tar.gz`，约 125 MiB，不用再打包。** 在项目根目录核对它，然后直接进行第 2 步。本步骤不调用模型或 COS。

```sh
sha256sum deployment/offline/dsh-npm.tar.gz
PYTHONPATH=backend/src:. .venv/bin/python -m deployment.dsh_offline verify --component npm --bundle deployment/offline/dsh-npm.tar.gz
```

本次文件 SHA-256：`6d24a25a186101accaa0ec9993fcab7b45f3c79990506bcba2e13aba45756939`。若文件尚未带入行内，不能只带源码而漏掉它。

只有需要重新准备依赖时，才在已准备依赖材料的开发机执行下面的打包命令。需要 Python 3.12；不要覆盖已批准的依赖文件。

```sh
PYTHONPATH=backend/src:. .venv/bin/python -m deployment.dsh_offline pack-npm --bundle deployment/offline/dsh --destination deployment/offline/dsh-npm.tar.gz
PYTHONPATH=backend/src:. .venv/bin/python -m deployment.dsh_offline verify --component npm --bundle deployment/offline/dsh-npm.tar.gz
```

得到 **`deployment/offline/dsh-npm.tar.gz` 一个文件**，里面是固定版 DSH 的完整 npm 离线缓存和校验清单，不包含主机 node_modules、RPM、案例、待测 Skill 或密钥。它是镜像构建输入，不是源码交付 ZIP，也不是只有 DSH 主包的 npm tarball。镜像仍按原锁文件离线安装全部 npm 依赖。

成功标准：退出码 0；打包输出记录压缩包 SHA-256 和大小，verify 输出 npm 清单摘要。保留这些记录。命令不覆盖已有包；更新时使用新的目标文件名，核对后再按发布流程替换。

如果本机没有 `deployment/offline/dsh/npm.json` 和 `npm-cache/`，先在可联网的 Linux x86_64/glibc、Node >=22.19.0 机器准备：

```sh
PYTHONPATH=backend/src:. python3 -m deployment.dsh_offline prepare-npm --bundle /tmp/dsh-prepared
PYTHONPATH=backend/src:. python3 -m deployment.dsh_offline pack-npm --bundle /tmp/dsh-prepared --destination deployment/offline/dsh-npm.tar.gz
```

`/tmp/dsh-prepared` 必须是未使用的新目录。准备过程只下载锁定的公开 npm 依赖，不使用个人 npm 凭证，不修改 DSH 版本或添加 overrides。

## 2. 让只拉 Git 的流水线拿到它

把新版源码和这个依赖文件通过批准渠道带入行内。**必须将依赖文件纳入流水线实际拉取的目标 Git 提交**，不能只留在电脑目录里。

压缩包默认被 Git 忽略。获准将第三方依赖存入行内 Git 后，由发布人员在行内项目根目录显式暂存并核对：

```sh
git add -f -- deployment/offline/dsh-npm.tar.gz
git diff --cached --stat -- deployment/offline/dsh-npm.tar.gz
```

按行内流程提交、推送源码及此文件；本操作单不自动提交或推送。先确认仓库单文件大小限制；若使用 Git LFS，流水线必须取回实际文件而非只有指针。若不允许依赖入 Git，又不能增加制品输入，单包也不能解决输入通道限制。

从目标分支做一次干净 checkout，用 Python 3.12 执行第 1 步的 verify，并核对批准摘要，避免依赖开发机未跟踪的缓存。

## 3. 启用镜像构建并发布

源码中的 `backend/Dockerfile` 现有三个构建参数为：

```dockerfile
ARG BENCH_DSH_ENABLED=0
ARG BENCH_DSH_RUNTIME_MODE=pod
ARG BENCH_DSH_BUNDLE=deployment/offline/dsh-npm.tar.gz
```

**依赖文件准备好后，只需把第一行的 `0` 改为 `1`，随源码更新到行内。** 如果平台支持 build args，也可以不改源码，在构建步骤传 `BENCH_DSH_ENABLED=1`；二选一即可。后两项保持原值。

流水线其余配置不变：

| 配置 | 值 |
| --- | --- |
| 自动化编译脚本 | `devops build --build.command="echo container_source_ready"` |
| 镜像构建上下文 | 项目根目录 |
| Dockerfile | `backend/Dockerfile` |
| 容器启动命令 | 留空，使用镜像默认命令 |
| 实例数 / 端口 / 存活探针 | `1` / `8080` / `GET /healthz` |

构建会校验并解包单个依赖文件，再运行完整锁定的 `npm ci --offline --ignore-scripts`。缺包或损坏会失败，不会偷偷联网补包。原有前端、Python、Git 和 LCR 安装仍需要既有行内源可访问，**不是整个镜像都已离线**。

确认平台没有旧环境变量覆盖镜像配置：运行时应为 `BENCH_DSH_ENABLED=1`、`BENCH_DSH_RUNTIME_MODE=pod`。只改运行环境的 enabled，不能替代构建阶段安装。

## 4. 看日志确认成功

在**刚构建的新镜像容器**中，可以单独执行以下无模型/COS 调用的预检。当前未更新的旧 POD 不会有此入口。

```sh
/opt/deployments/.venv/bin/python -m deployment.dsh_preflight --check-runtime
```

最后一条 JSON 应有：

```json
{"stage":"runtime","status":"ready","runtime_mode":"pod","os_isolation":false,"runtime_namespace":"not_applicable","software_guards":"passed"}
```

真实输出还会包含依赖版本等字段。`not_applicable` 是正确结果，**POD 模式不应显示 namespace passed**。预检会实际运行软件路径/工具检查和进程监督，不要求新建 namespace；若子进程回收所需的普通 Linux 操作也被平台拒绝，会明确失败，不靠提权绕过。

正式启动也会先执行该预检，再恢复 COS 数据、启动 Web 和 Worker。保留现有认证与 Secret；`/healthz` 表示网页存活，`/readyz` 用于检查任务就绪，不把后者设为存活或入口流量探针。

登录后确认页面上的 POD 模式提示。导入完整 Skill ZIP，先用 AACR 小清单分别运行基线和指定 Skill，再验收实际官方 50 例。保存配置和导入不调用模型；开始检视、评分、模型连接测试会产生真实模型调用，由使用者确认后操作。

若日志显示 `DSH disabled`，先查构建 enabled 参数；若仍调用 bwrap，先查运行 mode 是否被旧平台变量覆盖。依赖摘要错误先核对 Git 中的压缩包；COS 恢复失败与 DSH 沙盒无关，按原 COS 日志处理。

历史沙盒方式仍可显式选择，参数与 RPM 准备见 [DSH 离线部署参考](DSH离线部署.md)。本地测试通过不代替实际 Kylin 镜像构建、Node 22 ABI、公司 COS 或真实模型验收。
