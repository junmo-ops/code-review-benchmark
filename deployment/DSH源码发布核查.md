# DSH 源码发布核查

## 已确认条件

公司 Git 禁止归档及相关二进制制品入库；固定版 `@deepseek-ai/dsh@0.2.1-rc.2` 在公司 npm 查询为 404，无法申请同步。既有离线依赖包和 50 MB 以下传输包不能解决 Git 的入库限制，不应改后缀、编码或藏入缓存规避规则。

使用者提供的 LCR 编译节点为：

```sh
devops build \
  --build.command="go mod tidy && go run ./scripts/buildcmd --target all --output dist" \
  --build.dist="./dist"
```

随后由平台的发布依赖节点发布公司 npm。这里允许的是流水线产生的制品，不是将 `.tgz` 或二进制预先提交 Git。LCR 源码构建还依赖公司 Go Module 镜像；不能由其成功推断 DSH 的 npm 依赖也齐备。

## DSH 对应路线

已建立独立工程 `dependencies/dsh-review-source/`：固定 DSH 发行版的可读 JavaScript/配置和必要第三方源码、许可证，在构建阶段编译原生组件并组装内部 npm 制品，再经平台发布节点发布。该目录应单独作为一个公司 Git 项目，不需要将整个测评工程复制到 npm 发布流水线。

使用者已批准检视专用 headless 范围，不携带 DSH 自带网页、语音、Office 和交互终端；测评网页、检视策略及原生 Skill 不因此删除。运行时仍使用明确的 `pod` 模式，不需要 Bubblewrap/RPM。DSH JavaScript 来自固定 `0.2.1-rc.2` 的已校验发行包，不把另一个工作区 HEAD 当成该版本。必要原生加载器从单独固定提交的 C++ 源码内部构建，必须披露内部身份，不能冒称与官方预编译字节相同。

你提供的核查报告确认：254 个第三方准确版本中，192 元数据匹配、62 E404。保守依赖闭包保留 433 个模块位置，其中 55 个 E404 版本改由保留许可证的源码提供，7 个随明确不支持的能力排除；不是把 62 个缺项全部删除。4 个平台输入仍从公司源获取，版本、归档完整性和具体文件摘要都固定。

**部署顺序和最终编译命令只看 [DSH 部署操作单](DSH部署操作单.md)。** 本地已完成源码构建与真实 npm 制品还原；使用者已确认公司流水线源码构建及发布成功，实际目标镜像安装与 Kylin 运行仍待实测。下面保留依赖核查工具的用法，现阶段不需要反复重新查询同一报告。

2026-10-26：已确认流水线构建宿主固定为 Node22.9.0，且公司源查询 Node22.22.0/22.22.x 分发包均 E404；不再尝试其他版本或镜像。review.2 将构建/发布宿主与运行目标拆开，22.9 可严格组装固定输入并编译，但 DSH 仍须在测评镜像的22.22中强制执行目标检查。没有降低 DSH engines、关闭依赖 engine-strict 或改依赖版本；不以交叉打包通过冒称目标运行通过。

2026-10-27：使用者补齐本地和 Git 中缺失的10,882个 vendor 文件后，SOURCE_OK、行内编译及发布均成功；流水线必须声明 `--build.dist="./dist"`。实际包为 `@LV01.38/dsh-review-runtime@0.2.1-rc.2-review.2`，Node22.9.0 / GCC7.3.0，源码指纹 `9a9196667a228e81c031d1c4c4f077ac11dc30f1f3bceda685bfb750ab2c9b91`。该指纹可由原始源码、改名和两空格格式化的 package.json 在本机独立复现；源码计划及目标检查程序摘要未变。目标检查仍为 required-at-install，尚未完成实际 Kylin 运行验收。公司 tarball URL 显式包含默认80端口，安装器按协议、主机和有效端口判断同源，仍拒绝不同协议、主机、非默认端口和内嵌凭据。

## 在行内开发机检查

直接在能访问公司 npm 的行内开发机运行，不需要配置流水线节点，也不需要先部署新版 POD。支持 Windows、Linux 和 macOS；需要 Node 22 或更高版本及 npm。解压更新补丁后，在项目根目录执行下面一行（PowerShell、CMD 和 Linux/macOS 终端均适用）：

```sh
node scripts/dsh/registry-audit.mjs --registry=http://central.jaf.cmbchina.cn/artifactory/api/npm/group-npm/ --output=../dsh-registry-audit.json --details
```

不需要执行 `npm install` 或安装本项目依赖。交付补丁同时包含原始的 `dependencies/dsh/package.json` 和 `package-lock.json`；也可用 `--lock` 指向随包锁文件。沿用开发机既有 npm 认证，不把账号或令牌写入命令。报告写到项目上级目录，避免误提交；报告已存在时拒绝覆盖，请换一个文件名。ZIP 只是传输容器，不提交 Git。

Windows 使用 Node 直接运行 npm 的 `npm-cli.js`，不调用 `npm.ps1` 或 `npm.cmd`，不要求调整 PowerShell 执行策略。无论开发机系统是什么，核查目标始终是容器的 Linux x64/glibc，而不是把 Windows/macOS 的可选依赖当成目标依赖。

工具按原锁文件选出 Linux x64/glibc 运行依赖，保留匹配平台的可选依赖及准确版本，不改锁文件或添加 overrides。`@deepseek-ai/*` 被单独计为待核查的源码候选，不假定它们已经获准入库或能编译；其余依赖默认并发 4 个查询公司源的版本和完整性元数据。每次查询关闭重试，并显式覆盖对应 scope 的 registry，避免个人 scope 配置把查询导向另一源。

当前工作区锁文件在该平台按名称与准确版本去重后有 560 项，其中 306 项为 `@deepseek-ai/*` 源码候选，254 项为需要查询的其他依赖；不是仅检查 DSH 主包，也不把其他平台的可选包混作 Linux 缺项。

只执行 `npm view`，不安装包、下载包归档、执行包生命周期脚本、访问模型或 COS。npm 自身可能写入本地元数据缓存。终端输出统计和报告路径，`--details` 追加非匹配项的名称、版本和状态，方便从流水线日志收集；报告不保留原始 npm 错误正文。不要另行发送 `.npmrc`、认证头或 npm 调试日志。

结果含义：

- `metadata_matches`：该准确版本的元数据与锁文件完整性字段相符，**不等于归档已能下载、原生组件能运行或整个源码工程能构建**。
- `missing_or_not_visible`：E404；可能缺包，也可能服务端隐藏了无权访问的包，不能一律断言未同步。
- `access_denied`：E401/E403，先处理已有账号的读取权限。
- `metadata_mismatch`：版本或完整性字段不符，不自动换版本或接受被重打包的制品。
- `query_failed`：超时、连接或响应格式等问题，不计为确认缺包。

有非匹配项时退出码为 2，但仍保存报告；参数、环境或输出文件问题退出码为 1。不因为命令非零就把报告丢掉。将终端统计和报告中的非匹配项发回即可；公司地址按需要脱敏。

## 验证范围

本地 Linux 回归用真实 npm 和回环 HTTP registry 验证查询、冲突 scope 覆盖、404/403 区分、完整性不符、输出不覆盖与锁文件不变；Windows 调用选择另有跨平台路径、带空格目录、PATH 大小写和缺 npm 回归，但未在真实 Windows 上执行。没有查询真实公司源。仅覆盖当前发布版的运行锁文件，不包含未来源码构建额外引入的 TypeScript、打包器、编译器或系统库。检查结果用于下一步选择源码维护范围，不是 Git 策略、npm 发布权限或实际 Kylin 构建的验收。
