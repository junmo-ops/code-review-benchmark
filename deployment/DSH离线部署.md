# DSH 离线运行依赖与行内验收

> 第一次部署请先读 [DSH 部署操作单](DSH部署操作单.md)，按机器和操作顺序执行。本页是依赖制品准备、校验原理和验收边界的技术参考，不需要从头读完才能开始。

**历史离线路线，不适用于当前行内 Git 策略和 Dockerfile。** 当前改为独立 headless 源码工程发布内部 npm、测评镜像从公司源获取；只按 [DSH 部署操作单](DSH部署操作单.md) 部署。下文保留历史离线工具和 sandbox 记录，不能再通过传 `BENCH_DSH_BUNDLE` 启用新 Dockerfile，也不要求准备 Bubblewrap/RPM。需要重新启用历史沙盒构建时必须另行配置受控依赖输入，不能自动降级。

本页对应 06 票。固定 `@deepseek-ai/dsh@0.2.1-rc.2`，沿用 `dependencies/dsh/package-lock.json`，不更新版本或 overrides。自动化编译脚本仍为：

```sh
devops build --build.command="echo container_source_ready"
```

镜像步骤仍使用仓库根目录为上下文，Dockerfile 为 `backend/Dockerfile`。原有前端、Python、Git、LCR 的镜像源和构建流程不变；**本方案只让 DSH 与 Bubblewrap 离线安装，不表示整个镜像构建离线**。不自动提交二进制、源码、推送或发布。

## 默认状态

`BENCH_DSH_ENABLED` 构建参数默认 `0`，同时写入镜像环境。未准备 DSH 不阻断既有非 DSH 流程，日志明确显示 disabled，选择 DSH 会报错而非裸跑。目前只确认平台支持原 echo 命令与 Dockerfile 路径，**尚未确认平台能传额外 build args**。完整制品实际注入后，明确有两条启用路径：

- 平台已确认支持构建参数时，镜像构建步骤设置 `BENCH_DSH_ENABLED=1`，不改 echo。
- 平台不支持该参数时，在行内审批后的源码配置中，将同一 `backend/Dockerfile` 的 `ARG BENCH_DSH_ENABLED=0` 改为 `ARG BENCH_DSH_ENABLED=1`，仍走原 echo、原 Dockerfile 路径和根上下文。此操作由操作者明确执行，本次不自动修改为已启用或替其提交。

两条路径都会让完整安装成为必过项；制品缺失即构建失败。安装后由 Dockerfile 的 ENV 继承运行开关 `1`，启动时另做隔离预检。平台若显式覆盖运行环境为 `0`，DSH 仍关闭；**只在运行环境改成 `1` 不能补出未安装的依赖**。不能把默认关闭的构建成功说成 DSH 已安装可用。

启用后构建阶段执行离线 RPM 安装、完整锁定 npm 安装和构建检查；启动实际容器时，在资源/COS 恢复及 Web/Worker 启动前执行隔离预检。构建成功、`USER root`、`bwrap --version` 都不等于运行时 namespace 可用。

## 1. 准备 npm 制品

在受控可联网 Linux x86_64/glibc 机器、Node >=22.19.0 环境，从同一份项目源码运行。示例目录 `/controlled/dsh` 由操作者指定，不是交付包：

```sh
PYTHONPATH=backend/src:. python3 -m deployment.dsh_offline prepare-npm --bundle /controlled/dsh
PYTHONPATH=backend/src:. python3 -m deployment.dsh_offline verify --component npm --bundle /controlled/dsh
```

准备命令在新临时目录用独立空 npm 缓存执行 `npm ci --ignore-scripts --include=optional --engine-strict`；不使用主机 `node_modules`、用户 npm 凭证或全局缓存。下载地址为锁内 npmjs 地址，不假设行内镜像含 DSH。只导出 `_cacache`，不导出日志、HOME 或 `.npmrc`。重复准备请使用新目录，命令不覆盖已有制品。

`npm.json` 记录目标 linux/x64/glibc、项目两个依赖文件的 SHA256、缓存逐文件 SHA256 和实际 DSH/grep 版本。校验是内容身份核对，不是制品来源签名或安装成功证明。安装在另一新目录复制缓存，再执行 `npm ci --offline --ignore-scripts --include=optional --engine-strict`；逐项核对本平台适用的全部锁定依赖，防止 npm 静默漏装可选包。运行真实 DSH `--version` 和实际 `ripgrep --version` 验证 headless 入口与只读检视工具的可执行依赖。不启用 Shell/PTY、Office、Skill 脚本等能力，也不为其执行生命周期脚本。

`--offline` 和 `--ignore-scripts` 的语义见 [npm ci 官方文档](https://docs.npmjs.com/cli/v11/commands/npm-ci/)。本机 Ubuntu/Node 25 上的安装证据不能代替目标 Node 22/Kylin ABI 验收。

## 2. 准备目标 Bubblewrap RPM

**只能在实际 C86/Kylin10 Python 基础镜像对应环境准备**，不能复制 Ubuntu 的 bwrap。先由行内人员取得并记录两个基础镜像的实际 digest、`/etc/os-release`、`uname -m`、glibc、Node/npm、RPM/yum/yumdownloader 版本、批准的仓库快照 ID、脱敏 baseurl 与发行方 GPG key 指纹。Tag 名不是环境证明。本实现只接受 Linux x86_64/glibc、Kylin `VERSION_ID=10` 或 `V10`；若真实发行元数据不同，先核实，不伪造 os-release。

准备环境需要 `yumdownloader`（常由 yum-utils 提供）、`rpm`、`rpmkeys`，以及目标发行方已批准、已导入的 GPG 信任密钥。缺失工具或信任密钥是阻塞，不加 `--nogpgcheck`，不导入下载包附带的未知密钥。下面的 digest 与仓库 ID 必须替换为实际核验值；不能直接照抄占位符：

```sh
PYTHONPATH=backend/src:. python3 -m deployment.dsh_offline prepare-system --bundle /controlled/dsh --base-image csbase.registry.cmbchina.cn/paas/cmb-python-3.12@sha256:<实际64位摘要> --rpm-source <批准的快照仓库ID,必要的依赖仓库ID>
PYTHONPATH=backend/src:. python3 -m deployment.dsh_offline verify --bundle /controlled/dsh
```

命令禁用其他软件仓库，仅在指定 ID 中使用 `yumdownloader --resolve --alldeps` 下载 Bubblewrap 和传递系统依赖。`system.json` 保留镜像身份声明、实际发行元数据/摘要、glibc、架构、仓库 ID、RPM NEVRA、依赖、可信签名检查诊断和逐文件 SHA256。诊断中的准备路径不作为安装身份，搬入镜像后仍重新验证签名。镜像 digest 参数是操作者提供的来源声明，不是容器内部自动认证；须与平台实际拉取的 digest 对照留证。

在目标最终镜像安装时，先比对发行元数据、架构、glibc 和 RPM 内容，再复核签名；`yum --disablerepo=* --setopt=localpkg_gpgcheck=1 --setopt=gpgcheck=1 install` 只从制品目录解析安装，不在线补依赖，不用 `--nodeps`。基础环境缺失的依赖、错误架构、签名不可信或 ABI 不匹配均失败。实际执行 bwrap 版本命令验证动态链接器；拒绝 setuid/setgid 或 file-capability bwrap，不为通过安装添加特权。

## 3. 进入真实构建上下文

完整制品布局必须是：

```text
deployment/offline/dsh/npm.json
deployment/offline/dsh/system.json
deployment/offline/dsh/npm-cache/_cacache/{content-v2,index-v5,...}
deployment/offline/dsh/rpms/*.rpm
```

```sh
PYTHONPATH=backend/src:. python3 -m deployment.dsh_offline stage --bundle /controlled/dsh --destination deployment/offline/dsh
PYTHONPATH=backend/src:. python3 -m deployment.dsh_offline verify --bundle deployment/offline/dsh
```

stage 校验前后内容身份，只复制清单及其受控目录，不覆盖已有目录，输出两份清单的 SHA256。清单及批准的摘要需随行内验收记录保存，用可信渠道核对；仅有可改写的清单不提供真实性保证。`.dockerignore` 纳入这些散装缓存/RPM，排除主机 node_modules、缓存、秘密、output、测试及本地工单。整个路径刻意被 `.gitignore` 忽略，**stage 到开发机不代表它已上传至 CI**。

真实流水线必须由操作者接通以下输入链，且保持 echo 命令不变：源码 checkout -> 从批准的内部制品存储向**实际 checkout 根目录**注入以上完整目录 -> 在该 checkout 运行 verify 并比对批准摘要 -> 原 echo 步骤 -> 原镜像构建步骤（根上下文、`backend/Dockerfile`、构建参数 `BENCH_DSH_ENABLED=1`）。也可以向平台上传已注入完整目录的源码上下文，但须核实平台是否保留 git-ignored 文件。不得只上传 Git 源码再期待被忽略的本机缓存自动出现。注入应使用平台独立的受控制品输入步骤，不把下载命令塞进 echo，也不在 Dockerfile 从外网临时下载 DSH/RPM。

2026-10-23 使用者确认：当前平台只能拉 Git，不能额外注入制品。因此上面的独立输入链不是当前可直接执行的路径。若行内批准将审核后的离线依赖纳入项目 Git，由发布人员按 [DSH 部署操作单](DSH部署操作单.md)显式暂存、核对和提交，并以目标分支的干净 checkout 校验完整制品；不自动提交二进制或推送。若不批准进 Git 且不能增加制品输入，本接入仍阻塞。不能声称当前 CI 已拿到完整制品。本地 `DSH_OFFLINE_CONTEXT=1` 的专项确实通过 Docker 引擎 COPY/导出根上下文核对散装 npm 制品与排除项；它不证明行内上传、生产 Dockerfile 构建或目标 RPM 安装成功。

## 4. 构建与运行验收分开

完整制品就绪后，可在行内受控环境本地构建验证；生产发布仍使用原平台步骤，不自动推送：

```sh
docker build --build-arg BENCH_DSH_ENABLED=1 --build-arg BENCH_DSH_RUNTIME_MODE=sandbox --build-arg BENCH_DSH_BUNDLE=deployment/offline/dsh -f backend/Dockerfile -t benchmark-dsh:acceptance .
```

构建日志应有完整依赖安装、`deployment.container --check-build` 的 DSH 结果，且 `runtime_namespace` 为 `not_tested`。既有 LCR/Python/React 检查继续执行。浏览器回归不进入生产 Dockerfile。

然后在**实际平台容器、相同运行身份和默认安全策略**中，不带模型/COS 凭证，单独运行：

```sh
/opt/deployments/.venv/bin/python -m deployment.dsh_preflight --check-runtime
```

该命令不调用模型/COS，真实使用项目的 Bubblewrap 参数，核对 user/mount/PID/network namespace 与外层不同，尝试写入只读案例、读取隐藏目录和网络连接并验证被拒。平台容器启动也会重复此预检；失败不启动服务，无裸跑、提权或去隔离降级。namespace 能力由运行平台、内核和安全策略共同决定，见 [Bubblewrap 上游说明](https://github.com/containers/bubblewrap)。不得加 privileged、cap-add、放松 seccomp/AppArmor 或 setuid 以取得通过。平台默认策略不允许时，记录故障并保持 DSH 关闭；现有非 DSH 镜像仍可构建/运行。

## 本地验证入口与尚未验收项

```sh
PYTHONPATH=backend/src:backend/tests:. DSH_OFFLINE_TEST_BUNDLE=deployment/offline/dsh .venv/bin/python -m unittest -v test_dsh_deployment
PYTHONPATH=backend/src:backend/tests:. DSH_OFFLINE_CONTEXT=1 .venv/bin/python -m unittest -v test_dsh_context
npm run check
npm run verify
```

联网 prepare 专项需显式设置 `DSH_OFFLINE_PREPARE=<新目录>`；常规回归不自动下载 npm 包。安装专项与上下文专项需要先按上文准备/暂存 npm 制品，显式开关未设置时会报告 skip，而非已验证。全部本地测试只用本地模型/存储替身，不访问真实模型/COS；联网准备只下载公开固定 npm 依赖。真实镜像、目标签名 RPM/依赖、目标 Node 22 ABI、行内 npm/Python 源、平台输入与 namespace 未取得实际证据前，06 票不标 done。具体执行记录见 `.scratch/dsh-skill/issues/06-offline-runtime-deployment.md`。
