# DSH + Skill 部署操作单

**现在走两条流水线：先发布一个内部 npm 包，再更新测评系统。不要再往 Git 提交 `.tar.gz`、`.tgz` 或 `.7z`。** `.7z` 只用于传输，解压后的源码才入库。不需要 RPM，也不需要 Bubblewrap。

## 1. 新建 DSH 内部 npm 项目

解压源码发行包，密码 `123456`。将里面 `dsh-review-runtime-source/` 的内容放进一个独立的公司 Git 项目，不是覆盖 LCR，也不是覆盖测评项目。

新项目根目录应该直接看到：

```text
package.json
SOURCE_PLAN.json
scripts/
vendor/
README.md
```

这是只供代码检视的内部发行包。保留 DSH 核心及原生 Skill，不携带 DSH 自带网页、语音、Office 和交互终端；**测评系统自己的网页保留**。`vendor/` 是保留许可证的可读源码，不是编码或改后缀藏包，仍须按公司第三方源码规则审核。

当前行内包名 `@LV01.38/dsh-review-runtime`，版本 `0.2.1-rc.2-review.2`，使用者已确认发布成功。源码工程的 `package.json` 和测评项目的 `dependencies/dsh/headless-release.json` 已同步新名称与实际发布源码指纹。以后换名仍须重新构建、核对源码指纹，不能只改安装命令。review.1 的发布源码需先应用支持 Node22.9 构建的修正包，不能只修改 engines。

## 2. 配置这个新项目的编译阶段

流水线的 **Node22.9.0 环境保持不变**，构建宿主需为 Linux x64/glibc，并有 `cc` 和支持 C++17 的 `c++`。这是独立 DSH 项目的构建环境，不改 LCR 的 Go 编译阶段。不需要先执行 `npm install`，不再查询或安装新的 Node 包。DSH 本身仍在测评镜像的 Node22.22.x 中运行。

```sh
devops build \
  --build.command="npm run build -- --registry=http://central.jaf.cmbchina.cn/artifactory/api/npm/group-npm/" \
  --build.dist="./dist"
```

使用者已确认公司流水线必须使用 `--build.dist="./dist"` 声明产物目录，不能省略。构建核对源码、从 C/C++ 编译原生组件、校验产物，并附带固定摘要的目标检查程序。Node22.9 不执行 DSH，目标检查必须在测评镜像的 Node22.22 中通过；不把交叉构建当作运行验收。成功日志末尾包含：

```text
Built DSH runtime: 433 locked module locations; target Node 22.22.x check is mandatory during image install
```

仍需从公司 npm 下载下面 **4 个固定平台包**。它们在你发来的报告中元数据匹配，但是否真能下载，只能以这次行内构建结果为准。不会回退公网或要求同步另外 62 个缺项。

| 包 | 版本 |
| --- | --- |
| `@koromix/koffi-linux-x64` | `3.1.1` |
| `@vscode/ripgrep-linux-x64` | `1.18.0` |
| `@img/sharp-linux-x64` | `0.35.5` |
| `@img/sharp-libvips-linux-x64` | `1.3.4` |

如果这一步失败，先停在这里，带回编译错误或 npm 错误码，不要更换依赖版本或跳过检查。源码工程的 README 解释构建输入和边界。

## 3. 接平台的 npm 发布依赖节点

沿用你发布 LCR 的节点方式，发布这个新项目的根 `package.json` 和编译后的 `dist/`。**不是只把 `dist/` 当成一个没有 `package.json` 的新 npm 项目。** 节点必须保留根源码和构建产物，发布守护会拒绝源码改过却没有重建的旧产物。

公司 npm 已能取到 `@LV01.38/dsh-review-runtime@0.2.1-rc.2-review.2`，使用者提供的发布清单记录 Node22.9.0 / GCC7.3.0。发布成功不代表 DSH 可在22.9运行。这个包自带运行依赖，没有安装钩子，不会安装时再拉那些 404 的外部依赖。本项目没有自动替使用者提交、推送或发布。

## 4. 更新测评项目，再启用 DSH

解压测评项目源码包，密码也是 `123456`，与公司测评项目对比合并并审核入库，保留行内自己的配置和密钥。这里提供完整项目源码，不要求你的行内项目已经应用此前每个补丁；包含新安装入口和版本/源码指纹固定文件。**不能只发内部 npm 包而继续用旧 Dockerfile**。

测评源码包不重复携带独立发行工程的大型 `vendor/`，仅保留参与本地校验的构建工具和测试；需要发布 npm 的完整源码在第 1 步的独立源码包中。案例仓库、运行数据、`node_modules`、离线缓存及本机 LCR 安装目录都不在源码交付中，原有行内资源继续沿用。

测评项目原来的 echo 编译命令、Dockerfile 路径 `backend/Dockerfile`、构建上下文根目录均不变。发布成功后，构建参数设为：

```text
BENCH_DSH_ENABLED=1
BENCH_DSH_RUNTIME_MODE=pod
```

如果平台不能传构建参数，只在公司源码的 `backend/Dockerfile` 把 `ARG BENCH_DSH_ENABLED=0` 改为 `ARG BENCH_DSH_ENABLED=1`。本次仍保持 `0`，避免内部包尚未发布就阻塞现有系统。

新 Dockerfile 会从公司 npm 获取固定内部包，校验源码、平台制品与产物，再暂存还原模块布局。其 Node22.22 中强制检查 DSH 版本、原生模块访问、文件锁、grep、FFI 和 Sharp，成功才安装；启动预检再验。必须同时更新 review.2 的测评消费入口，不能沿用仅检查版本/grep 的旧入口。不会找旧的离线压缩包，不会安装 Bubblewrap，也不会修改 LCR 版本。仅修改 POD 运行环境的 enabled，不能代替构建时安装。

## 5. 新 POD 内验收

部署成功后在 POD 执行：

```sh
cd /opt/deployments
.venv/bin/python -m deployment.dsh_preflight --check-runtime
```

末条 JSON 应包含 `status: ready`、`runtime_mode: pod`、`os_isolation: false`、`kind: dsh-review-headless`、`locked_packages: 433`。这不是 OS 沙盒通过，而是明确没有额外 OS 沙盒；工具限制与进程监督仍须通过。

然后用测评网页各新建一个小批次：不加载 Skill 的基线、加载指定 Skill 的待测组。检查完整答卷、真实 Skill 加载证明、日志与报告，再扩大到已有清单。模式和内部发行身份会冻结，旧批次不自动改成新执行条件。

## 本地已验与行内待验

本地使用 Node 22.22.0、真实 DSH 和确定性回环模型验证源码构建、发布包还原及专项行为，不调用真实模型或 COS。使用者已确认公司源码构建和发布节点成功，并提供已发布包的清单；实际目标 Kylin ABI、测评镜像构建和 POD 上的小批次仍需验收，不能从发布成功推断行内已部署成功。

POD 模式只有软件限制，同账号进程仍可能访问同容器数据，不提供操作系统级文件或网络隔离。请只用于接受这一边界的专用测评 POD。
