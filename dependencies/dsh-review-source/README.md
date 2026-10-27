# DSH 检视专用内部运行时

这是独立 npm 发行工程，不是整个测评项目，也不是完整的官方 DSH 产品。固定 DSH `0.2.1-rc.2` 的可读 JavaScript、配置与第三方源码保留原始字节和许可证；必要原生组件在构建阶段从 C/C++ 源码编译。它不携带 DSH 自带网页、语音、Office 转换或交互终端，不删除测评系统的网页，也不改写检视循环、Skill 正文或评分协议。

测评 Git 仓库只跟踪本工程的构建工具、源码计划和许可证，不重复提交大型 `vendor/`；完整源码输入在独立的公司 npm 发布仓库维护。从测评仓库检出后如需重建发行包，应先取得经批准的完整 vendor，并按 `SOURCE_PLAN.json` 核对，不得为迁就缺失文件修改清单。测评镜像消费已发布 npm，不需要该 vendor。

## 行内导入

将本目录内容作为独立 npm 源码项目的根目录入 Git，根目录应直接看到 `package.json`、`scripts/`、`vendor/`、`SOURCE_PLAN.json`。不要把交付用的 `.7z`、旧 `.tar.gz`、`node_modules/`、`dist/` 或本机缓存提交 Git。第三方源码仍应走与 LCR 相同的审核流程；本工程不是改后缀或编码藏包。

同时保留 `.gitignore` 和 `.gitattributes` 两个隐藏文件：前者避免遗漏上游名为 `dist`、`bin` 或 `runtime` 的可读 JavaScript，后者防止跨平台 Git 自动转换行尾破坏源码摘要。`vendor/** -text` 仅禁用行尾转换，文件内容仍须通过 UTF-8、二进制魔数和禁用后缀检查。

当前行内内部包名为 `@LV01.38/dsh-review-runtime`，版本为 `0.2.1-rc.2-review.2`，已由使用者在公司流水线发布。包名调整后须重新构建并同步测评端的批准源码指纹，本项目不自动发布或推送。

## 构建环境

构建宿主：Linux x64/glibc，Node **22.9.x**（也支持 22.22.x），npm，`cc`，支持 C++17 的 `c++`。可通过 `CC`、`CXX` 指定编译器可执行文件。所需 Node22.22 公共头文件与 node-addon-api 头文件已随源码固定，无需在公司下载新的 Node、Node 头文件、安装 pnpm 或拉取 GitHub 源码。

**构建宿主不等于运行目标。** Node22.9 只负责编译、组装和校验制品，不运行 DSH；实际运行目标仍为 Node **22.22.x**。`package.json` 的 engines 描述此数据发行工程的构建/发布工具，不意味着其中 DSH 支持 22.9。测评镜像安装与启动必须执行发布物里固定摘要的 `dist/target-check.mjs`，实际检验 DSH 版本和必要原生组件，失败不安装或启用。原生组件还必须匹配目标 Kylin 的 glibc/libstdc++ ABI，本地通过不证明这一点。

构建仍会从指定公司 npm 源获取固定版本的 Koffi、ripgrep 和 Sharp Linux 平台制品。这些版本在使用者提供的报告中元数据匹配，但真实归档下载尚需行内构建确认。除这些明确锁定的平台输入外，不在公司执行完整外部 DSH 的 `npm install`，不尝试公网源。

不需要先执行 `npm install`。使用者已确认公司流水线必须声明产物目录，编译阶段填写：

```sh
devops build \
  --build.command="npm run build -- --registry=http://central.jaf.cmbchina.cn/artifactory/api/npm/group-npm/" \
  --build.dist="./dist"
```

在开发环境直接构建时，执行其中的 `npm run build -- --registry=...` 即可；公司流水线不能省略 `--build.dist="./dist"`。

`dist/` 是构建产物，再接你现有的平台 npm 发布依赖节点。不要把本工程的编译配置改到 LCR 正式流水线上。发布节点需保留本项目源码及 `dist/`，与 LCR 的源码/制品交接方式相同。

构建先核对源码清单，严格安装锁定平台输入（保留 engine-strict，关闭生命周期脚本），编译原生加载器和文件锁/系统组件，再校验完整制品清单。Node22.9 下记录 `buildHostCheck: not-run-cross-build`，绝不把未运行的 DSH 检查标为通过；Node22.22 构建宿主另外实际运行目标检查，但仍要求测评镜像在安装与启动时再验。检查包括原生内建模块访问、真实文件锁、FFI、ripgrep 和 Sharp。失败不替换已有完整 `dist`。`prepack` 和 `prepublishOnly` 核对内容摘要、源码指纹、目标检查程序与版本，源码修改后不能继续发布旧制品。包本身没有运行时 npm 依赖，也没有安装脚本。

## 来源和范围

完整来源、原始 npm 完整性字段、源码 SHA-256 和能力排除记录见 `SOURCE_PLAN.json`。DSH JavaScript 取自已校验发行版，不冒称是另一个上游 HEAD 的 TypeScript 重编译结果。原生加载器源码固定在 `a58d3599a2e551d7d706d1ff796c9fd4bbaff63c`，是明确记录的内部源码构建，不能冒称与官方预编译二进制字节相同。Node 22.22.0 公共头文件及 node-addon-api 8.9.0 仅作为固定的编译头文件，不替换原运行锁文件的依赖版本。

平台仅为 Linux x64/glibc，系统辅助程序在目标 glibc 环境编译，不声明 musl 支持。POD 模式不提供额外 OS 沙盒；检视工具限制仍由测评系统原有执行策略实施，本制品自身不是安全沙盒。

发行物以平铺的 `dist/packages/` 保存模块，避免 npm 自动解析未同步的上游依赖；`dist/runtime-manifest.json` 记录原模块位置、产物摘要、工具链及源码指纹。测评镜像必须在校验后还原模块布局，不能只安装此包就假定现有旧镜像已支持。

## 本地开发验证

`npm test` 运行本工程的源清单、发布守护和还原专项。`npm run build -- --offline --cache=/path/to/verified/npm-cache` 只用于本地模拟锁定平台输入；构建会复制该缓存到临时目录，不修改经批准的缓存。此模拟不证明公司源可下载，也不代替实际 Kylin ABI、构建和发布验收。所有验证不调用真实模型或 COS。
