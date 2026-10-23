# DSH Linux 无待测 Skill 基线

> 本页记录历史 sandbox 模式。2026-10-23 使用者明确选择新增 POD 内运行模式，不再要求额外 Bubblewrap/RPM；当前行内部署按 [DSH 部署操作单](../deployment/DSH部署操作单.md)，运行边界见 [ADR 0002](adr/0002-explicit-pod-dsh-execution.md)。下文 namespace 验收只适用于 sandbox 模式。

本页只覆盖工单 01：在现有新建测评中选择 DSH + Skill 和“不加载 Skill（基线）”。不包含 Skill 导入、Skill 检视、50 例专项或 Skill 资产持久化，也不自动创建对照批次。

## 运行依赖

- 固定 `@deepseek-ai/dsh@0.2.1-rc.2`。依赖声明及完整传递依赖锁在 `dependencies/dsh/`，不使用 Windows 包或邻接源码仓库代替 Linux 安装。
- 实际下载来源为 `https://registry.npmjs.org/`。包的 SHA512 integrity 为 `sha512-tvY+yhfKRifthbqPe/OK0AYxM5ZkvqfYq/jMYgJcEUxiRRL1Vwra3Ti5TB6AXgSWQdiPHYh/Vp6SJa9yHwMNuw==`。
- 此锁中的 pi-ai、undici 等依赖要求 Node >=22.19.0，高于工作台本身的 >=22.12.0；已实测 Node 24.21.0 和 25.8.2。DSH 主包没有单独声明 engines，不能仅据主包推断最低 Node 版本。
- 需要 Linux、Bubblewrap 及可用的用户、挂载、PID、网络命名空间。缺少依赖或隔离探测失败时拒绝运行，不退化为裸跑。生产镜像和行内主机的这些能力需要单独验收；本票没有修改生产 Dockerfile 或向其加入浏览器回归。

后续 06 票补充了 [DSH 离线部署](../deployment/DSH离线部署.md)：构建默认显式关闭 DSH，完整离线制品安装和实际运行容器 namespace 预检分别执行。该后续接入不把本页的 Ubuntu 本机结果升级为 C86/Kylin10 或行内平台验收结论。

```sh
npm ci --prefix dependencies/dsh
node dependencies/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js --version
```

固定版的真实非交互入口为 `dsh --profile headless --json <task>`，不是另一个模拟执行器。本机缓存已通过一次 `npm ci --offline` 重装；这不表示另一台离线机器已经具备所需 npm 制品。

## 执行边界

每案例从固定 head 提交创建全新快照，只提供 head 源码、merge-base diff 和不含参考答案的任务身份。个人 HOME、工作台目录、其他批次和裁判材料不挂载。案例中的 AGENTS、.env 和 Skill 文件保留为数据，不启用自动指令和 Skill 加载组件。

Bubblewrap 强制案例只读、独立 PID 和网络命名空间。DSH 仅注册 read、glob、grep；单调执行 guard 将 read 限制在案例和任务输入，glob/grep 限制在案例，不能读取运行时的其他 Skill 或临时 HOME。不注册写入、Shell、安装依赖、子代理、工作流或联网工具。平台自己的 Node 通信桥、路径 guard 与固定压缩保护是可信适配代码，不是案例或待测 Skill 脚本。

模型请求通过隔离内的 loopback HTTP 桥转到主机 Unix socket。主机只向冻结的模型接口和模型 ID 转发 Chat Completions，不转发任意地址或重定向；真实凭证留在主机。当前 DSH 模型通道使用 SSE，报告中保留已知用量及覆盖率，不假定缺失用量为零。检视模型、裁判和原有评分选择保持独立。

模型请求重试、压缩额外尝试和上下文溢出恢复均为零。固定版 DSH 原生会捕获自动压缩失败并继续下一步，因此另用继承原压缩引擎的保护层同步取消该轮；正常压缩策略和工具循环保留。此行为以真实请求记录验证，不以配置值存在代替联调。

默认整例检视预算为 10 分钟，可用现有分钟参数调整。平台监督整个 DSH 进程，超时和中断回收进程树，进程清理耗时单列。每案例串行，单例失败不能变成成功空意见；依赖、隔离和冻结条件错误停止整个批次。失败检视须新建批次，恢复只解析已有证据，不重新调用模型。

完整空意见答卷有效。未正常结束、截断、非 JSON、非法路径或越界行号不会写入完整答卷。无待测 Skill 基线不要求 Skill 加载事件。

## 证据与验证

普通批次保留 `run-config.json`、`raw/*.manifest.json`、`*.execution.json`、`*.requests.json`、脱敏 stdout/stderr 与 session JSONL。会话原件从临时 HOME 转存至批次后再清理，沿用既有运行材料备份范围。报告增加基线、DSH 身份、实际评分范围和检视/裁判用量，不改评分公式、轮次或重试。

```sh
npm run verify:dsh
E2E_DSH=1 E2E_SIMULATED_LCR=1 npm run e2e
E2E_SIMULATED_LCR=1 npm run e2e:explore
npm run check
npm run verify
```

`verify:dsh` 必须具有真实 Linux DSH 和隔离依赖，不跳过依赖缺失。它注入本地 HTTP 503、上下文溢出、压缩失败、截断和非法答卷，另验证正常压缩、工具限制、未知用量、冻结接口及 60 秒整例超时。独立的 2 秒进程契约探针使用会产生真实后代的受控 Node 程序，不将该探针冒称为 DSH。

`E2E_DSH=1` 浏览器路径使用真实 Linux DSH、真实 Git 和本地确定性模型/裁判，分别经过原始 AACR 三轮与 Martian 路由，保存配置、刷新、排队、读报告和下载。不设置该变量的既有浏览器场景仍模拟 LCR 与裁判，不验证真实 LCR 内部行为。探索模式默认 60 秒压力观察，其故障和模拟边界沿用《浏览器自动回归》。

本地回归不访问真实模型或真实 COS，不证明模型质量、行内镜像、生产资源上限或长期稳定性。最终命令结果、产物路径和未解决阻塞记录在 `.scratch/dsh-skill/issues/01-dsh-baseline.md`；不得只根据本页将工单判为完成。
