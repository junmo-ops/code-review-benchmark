# 01: 无待测 Skill 基线贯通现有测评

**What to build:** 测评人员在现有新建测评流程中选择 DSH + Skill 被测工具及无待测 Skill 基线，经现有队列完成一次 Linux 只读检视、独立选择的评分和普通批次报告。用最小案例验证完整路径，而不是先交付孤立的执行脚本或只有前端的选项。

**Blocked by:** None (can start immediately).

**Status:** done

- [x] 取得并核验固定版本的 Linux DSH 运行依赖、Node 要求和非交互调用，记录实际版本与依赖来源；Windows 包资料不能替代 Linux 运行证据，缺少依赖时报告实际阻塞，不静默换版本或用模拟执行器冒充已接通 DSH。
- [x] 现有被测工具增加 DSH + Skill，首个可用模式为显式无待测 Skill 基线；新建、保存配置、排队、检视和查看报告形成完整用户路径，不新增独立实验入口，不自动生成其他批次或调用。
- [x] 被测模型、接口、裁判和评分方法继续独立选择；同一类完整答卷分别验证原始 AACR 评分与 Martian 路由，原有协议的预算、轮次及重试不被替换。
- [x] 复用已登记且资源可用的 AACR 清单，冻结实际案例与提交身份，不把内置单例或五例标识写成工具白名单；本票使用单例验通，规模和异常专项由 04 覆盖。
- [x] 基线采用统一任务与答卷协议，每案例使用全新会话和隔离快照；不加载个人配置、其他 Skill 或仓库指令，不暴露参考答案、裁判材料或其他批次内容。
- [x] Linux 只读隔离和工具限制实际生效：禁止写入案例、执行项目或 Skill 脚本、安装依赖、子代理及检视工具联网，受控模型接口通信除外；隔离不可用时拒绝运行，不退化为裸跑。
- [x] 默认整例检视时限为 10 分钟，可按现有分钟设置调整；实际监督和终止进程树，单请求超时不冒充整例截止，清理时间单独记录。
- [x] 显式关闭 DSH 模型请求、压缩失败和上下文溢出恢复的额外重试；通过本地受控接口的请求记录验证实际行为，而非只检查配置值，正常工具循环和固定策略压缩计入预算及用量。
- [x] 完整空意见答卷有效；无效 JSON、非法意见位置、缺少完整结束及超时不能成为成功零意见。基线模式不因没有 Skill 加载事件而被错误判为失败。
- [x] 普通批次记录、报告及下载可查看基线模式、DSH 身份、脱敏原始证据和已知用量；缺失用量不当成零，检视与裁判分别统计，缓存不重复累加。
- [x] 经现有公开 API、队列和浏览器入口完成本地端到端验证，实际 Linux DSH 联调使用本地模型和裁判替身；不访问真实模型或 COS，并回归既有被测工具的创建及评分行为。
- [x] 按项目约定完成 check、verify 和本票所需专项及 e2e；环境或校验阻塞如实记录，不能以源码内容断言替代验证或在未完成验收时标为 done。

## Comments

### 2026-10-21 实现与运行依赖

- 仅实现 01，未启动其他票、未创建其他 Codex session，未提交、推送、发布或生成交付 ZIP。规格采用 `docs/DSH-Skill测评方案.md`；`.scratch/dsh-skill/spec.md` 当前不存在。
- 新 reviewer `dsh_skill` 接入现有创建、冻结、持久队列、单例检视、恢复、评分、报告和下载。显式 `skill_mode=baseline`，不接受 Skill 版本，不自动派生批次；AACR 清单复用现有登记和资源校验，不限制 pilot 标识或数量。
- 实际 Linux x86_64 / WSL2 内核 `6.6.87.2-microsoft-standard-WSL2`；Bubblewrap 0.9.0；固定 `@deepseek-ai/dsh@0.2.1-rc.2` 从官方 npm registry 下载并在 Linux 安装 615 个包，未使用 Windows 发行包或其他执行器冒充。
- 实测 Node 25.8.2 和 24.21.0。主包未单独声明 engines，锁定的 pi-ai、undici 等要求 >=22.19.0；依赖及锁在 `dependencies/dsh/`，锁 SHA256：`af778165c659aad76c695d0eef90307ad8c611b52f77a9d68310cd8b6df90ef9`。原始包 integrity 和安装方法见 [运行说明](../../../docs/DSH-Linux基线.md)。本机缓存另经一次真实 `npm ci --offline` 重装，不承诺其他机器已具备离线制品。
- 真实非交互入口是 headless JSON 流。每案例全新只读快照和临时 HOME，独立挂载/PID/网络命名空间；仅开放 read/glob/grep，并用单调 guard 限制读取范围。模型经 Unix socket 固定接口转发，凭证不进入 DSH。隔离不可用、接口或冻结依赖变化时停止整批。
- 修复实测发现的默认行为差异：仅设置压缩重试为 0 不会阻止 DSH 压缩失败后继续请求，现继承固定版原压缩引擎，在失败时同步取消；正常压缩策略不变。原始 session JSONL 脱敏转存至批次后才清理临时 HOME。

### 验证结果

| 验证 | 实际结果与证据 |
| --- | --- |
| `npm run check` | 通过：Python 复杂度、前端/Node lint（零警告容忍）、TypeScript；未扩大历史基线或提高阈值。 |
| `npm run verify` | 通过：项目 check、9 项 Node/本地 npm 镜像契约测试、生产构建和 diff 检查。Vite 既有大 chunk 提示仍在，未调高阈值或宣称行内镜像通过。 |
| `npm run verify:dsh` | 19 项全部通过，真实 Linux DSH + 本地 HTTP 替身；[证据](../../../output/playwright/dsh-contract-2026-10-21T08-20-32-536Z/results.json)，运行约 357 秒，含独立 60 秒超时注入。 |
| `E2E_DSH=1 E2E_SIMULATED_LCR=1 npm run e2e` | 最终整轮通过；[结果](../../../output/playwright/2026-10-21T08-29-39-042Z/results.json)、`dsh-baseline.json`、两份下载报告与桌面/移动截图位于同目录。 |
| `E2E_SIMULATED_LCR=1 npm run e2e:explore` | 通过；[结果](../../../output/playwright/2026-10-21T08-20-30-035Z/results.json)。压力观察 60 秒，主动停止/强杀与重启、响应丢失、503、状态损坏等均为隔离本地注入。 |
| 标准后端回归 `tools/check_regressions.py` | HEAD 与当前代码同环境各 28 项通过；见下述基线比较。 |

专项不只检查配置：真实 HTTP 请求验证 503 和已被 DSH 识别为 CONTEXT_WINDOW_EXCEEDED 的错误各只调用一次；压缩失败时恰好为正常请求加一次压缩请求，没有恢复请求；正常工具循环加压缩成功共 4 次，合成已知用量 194150 Token 正确累计。压缩压力通过长输出和合成 usage 注入，不代表实际模型容量或质量。

另验证空答卷、截断、非法 JSON、路径与 head 行号、未知/空缓存用量、禁止写入/执行/委派/联网、隐藏参考与运行时文档读取、缺隔离零调用、恢复完整答卷字节不变且零新增调用。整例超时使用真实 DSH 与延迟 70 秒的本地接口，在 60 秒截止终止；独立 2 秒进程契约探针观测真实受控 Node 父子进程并验证无存活后代，不将该探针冒充 DSH。

浏览器经公开 API 与持久队列实际保存配置、刷新、检视、评分和下载。相同案例/提交和同类完整答卷分别走原始 AACR 三轮与 Martian 一轮，保留 180 秒裁判请求预算及原始重试配置；两批会话身份不同，未额外创建组。新用例还回归 LCR、Cline、服务端 Agent 的创建/评分选择；既有浏览器 LCR 与裁判仍为显式模拟。

完整浏览器回归最初暴露测试准备缺陷：此前缺提交场景留下了未结束的 `missing.zip` 上传批次。新场景现等待页面挂载，通过公开“结束本批次”按钮清理再导入；没有绕过生产导入校验。最终完整流程已重跑通过。

### 全量失败的最小归因

没有直接将全量失败归为历史。以实现前固定点 `83edcd5ffca36219dcc97a655eed4112ea4d1698` 建临时 detached HEAD worktree，复用同一 venv 和前端产物，显式设置各自 `BENCH_PROJECT_ROOT`/源码导入路径与本地存储环境，确认加载的 service/cloud 确为各自工作区源码。使用同一标准回归及同一 `python -m unittest discover -s backend/tests -v`。临时 worktree 和其环境软链接已清理，未改动主工作区的 HEAD。

- HEAD 全量 169 项：17 failures、51 errors；当前全量 188 项：同样 17 failures、51 errors，新加 19 项通过。
- 逐项比较失败用例 ID 和异常原因，只归一化项目路径、临时夹具目录和随机 CSP style-nonce：新增失败 0，消失失败 0，原因变化 0。不是仅比较数量。[比较记录](../../../output/playwright/regression-comparison-DuTyDo/comparison.json)；同目录保留两轮原始日志、命令、退出码及标准 28 项结果。
- 已复现的基线原因包括：18 项缺现有 LCR Linux 二进制；旧 `backend/local.py`、`backend/data/`、根 `cloud.py` 路径；临时根目录未准备案例导致清单验证先失败；旧 HTTP 调用/认证页面预期。未在本票扩大修复这些既有问题。
- 全量发现测试**仍为失败**，不能宣称项目全量测试通过；本票的标准入口与专项已通过、既有测试无新增失败，故不把相同基线失败作为 01 的新增阻塞或阻断后续依赖。

### 本 Session 评审与边界

Standards：以 HEAD 和未跟踪新增文件为范围，自审复杂度、Hook/lint、现有适配器模式、凭证与路径边界；高复杂度 service/cloud 仅作接入所需分解，未改冻结基线。剩余阻断性新发现 0。

Spec：按本票逐项自审，压缩失败续跑、运行时文档可读、非法物理行号等发现均经行为测试修复；仅交付显式基线，不包含其他票功能。上述为实现阶段自审，不替代 code-review skill 要求的独立双轴评审。

用户进一步明确允许同票评审代理后，已读取 code-review skill，固定比较点为 `83edcd5ffca36219dcc97a655eed4112ea4d1698`，以工作区差异及全部未跟踪新增源码为范围并行启动 Standards / Spec 两个独立只读评审代理。未创建其他 ticket 的实现 session，也未自动提交。暂回 `ready-for-agent`，待评审处理与相关复验完成后再结票。

无真实模型、真实 COS、生产镜像或行内平台验收；不能从场景数推导无问题。50 例 DSH 规模、完整强杀恢复与长期生命周期专项仍由 04 覆盖，Skill 包及资产事项仍在各自工单。本票未遗留实际 Linux DSH 依赖阻塞。

### 编排复核：最终验收尚未关闭

实现 session：`01a15d51-0740-7b13-81fb-cfe287865be1`。上述通过记录对应后续独立评审之前的代码，不能单独证明最终状态。独立评审发现读取路径边界问题；实现 session 已修改 `scripts/dsh/scope.mjs` 并通过新增的真实 DSH 回归。最终修改后尚需重跑项目门禁、完整 DSH 专项和相关浏览器流程，并收齐两轴评审的关闭记录。

该 session 随后因平台系统错误结束，恢复同一 session 后再次遇到系统错误，尚未完成最终重验。因此保留 `ready-for-agent`，不将其视为 03 的已完成依赖。02 没有前置依赖，可继续独立实现。未调用真实模型或真实 COS，未提交代码。

### 编排复验：评审后代码

实现仍全部来自上述独立 session。编排 session 在 02 结束、工作区无其他修改进程时，对最终集成代码补做验收，未修改业务实现：

- `npm run verify` 退出 0，check、类型检查、9 项 Node 契约和构建通过；既有 Vite chunk 警告仍在。
- `npm run verify:dsh`：20 项全部通过，339.111 秒，包含评审新增的 read/glob/grep 读取边界专项。证据：`output/playwright/dsh-contract-2026-10-21T09-24-40-056Z/results.json`。
- `E2E_DSH=1 E2E_SIMULATED_LCR=1 npm run e2e`：整轮退出 0，包含完整 Skill 资产导入与真实 Linux DSH 的双评分基线。证据：`output/playwright/2026-10-21T09-30-45-728Z/results.json`。
- Standards 原重复夹具发现已由独立代理复核关闭；其后确认的路径问题，最终代码使用评审要求的原生物理路径解析，并通过真实 DSH 三工具专项。Spec 原报告只发现该 P1，无其他实质问题；其最终代理复审被平台错误中断，未虚称最终复审通过。

用户已批准新增独立历史回归修复 session `01a15dcf-a05d-7572-9113-26a3839bf8af`，先解决全量门禁再关闭 01/02 验收、启动 03。当前仍不标 done。完整范围和边界见 `.scratch/dsh-skill/regression-repair.md`。

### 最终结票

上述历史状态由本段取代，原失败和中断证据保留。已核验用户授权修复 session 的最终产物：全量 207 项、标准回归 38 项、真实 Linux DSH 22 项全部通过；check/verify 通过且原 chunk 警告已消除。整轮浏览器 14 项及 explore 34 项通过，分别见 `output/playwright/2026-10-21T10-00-12-009Z/results.json` 与 `output/playwright/2026-10-21T09-57-29-905Z/results.json`。命令和原始日志见 `.scratch/dsh-skill/regression-repair.md`。

原 Spec 唯一读取边界发现已落实原生物理路径解析，真实 DSH 三工具回归持续通过；Standards 原重复夹具发现已独立复核关闭。修复 session 发现的清理及恢复缺陷另经红绿测试、独立双轴评审和最终门禁关闭，没有将原 session 的平台错误解释为通过。不存在未处理的本票评审发现。

因此 01 的本地接入验收完成，标记 done。真实模型、COS、生产及行内镜像仍未验收，也不是本票本地自动验收的替代结论；50 例与 Skill 模式保持原后续工单范围。未提交、推送或打包。
