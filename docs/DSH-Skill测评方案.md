# 在现有被测工具中接入 DSH + Skill

## 2026-10-23 运行方式修订

使用者在真实行内 POD 验证非特权 Bubblewrap namespace 创建被拒绝后，明确选择方案 2。新增显式 `pod` 模式：DSH 在现有 POD 内运行，不要求额外 Bubblewrap、RPM 或 namespace 权限；只保留软件工具与路径限制，不声称操作系统级只读、文件或网络隔离。原 `sandbox` 模式保留。两者均冻结运行模式、禁止失败自动降级，更改模式须新建批次；页面、答卷、执行记录和报告必须披露实际模式。此修订优先于下文早期的强制隔离条款，见 [ADR 0002](adr/0002-explicit-pod-dsh-execution.md)及[部署操作单](../deployment/DSH部署操作单.md)。

## Problem Statement

测评人员希望评估不同代码检视 Skill 的执行效果，但当前工作台没有可在 Linux 上运行、支持用户完整 Skill 包的 DSH 被测工具。已有 Cline + Skill 仅是固定 Skill、Mac 单例适配，不能代替这一需求。

测评人员需要继续使用现有的新建测评流程、已配置模型、AACR 可选案例清单、裁判、评分方法和报告，而不是为了测 Skill 进入另一套实验产品。尤其必须支持用户已经导入的 AACR 官方正样本 50 例，不能把用于接入试跑的单例或五例清单当成功能上限。

同一份 Skill 可能随时被修改，执行器还可能自动加载个人规则、其他 Skill 或仓库指令。若没有不可变版本、明确加载证据和受控上下文，测评结果就无法可靠对应到具体待测对象；隐藏重试、失败伪装成零意见及不可恢复资产也会扭曲结果。

## Solution

在现有“新建测评”的“被测工具”中增加 DSH + Skill。选中后，每个测评批次选择一个已导入的 Skill 版本，或选择“不加载 Skill（基线）”。不同 Skill 和无待测 Skill 基线分别创建普通批次，不自动生成其他分组或额外调用。

首期面向 Linux、AACR 和静态只读检视。复用所有已登记且资源可用的 AACR 清单，包括已有 50 例；继续独立选择被测模型、裁判模型与评分方法。默认每案例检视时限为 10 分钟，案例串行执行，DSH 输出统一完整答卷并由所选现有协议评分。

工作台导入完整 Skill ZIP，保存原包和不可变版本，显式加载并留证；隔离额外指令、参考答案和其他运行上下文，保留失败、用量与脱敏原始证据。Skill 资产与测评记录接入现有本地和 COS 持久化、恢复机制，不另建备份或实验系统。

## User Stories

1. As a benchmark operator, I want to select DSH + Skill under 被测工具 in the existing creation dialog, so that I can use the familiar evaluation workflow.
2. As a benchmark operator, I want to select an existing review model and endpoint independently of DSH, so that I can keep the model fixed while changing a Skill.
3. As a benchmark operator, I want to choose the judge model independently, so that the review executor does not dictate who evaluates its answer.
4. As a benchmark operator, I want to retain the existing 评分方法 choices, so that switching the tested tool does not silently change scoring semantics.
5. As a benchmark operator, I want to choose my registered AACR 50-case dataset, so that Skill evaluation uses the same cases as my existing runs.
6. As a benchmark operator, I want dataset size to come from the validated case list, so that display names or pilot defaults do not truncate the evaluation.
7. As a benchmark operator, I want to use one-case or five-case pilots when convenient, so that I can check integration before spending time on a larger run.
8. As a Skill author, I want to upload a complete 待测 Skill 包 with its referenced resources, so that the evaluation does not reduce my Skill to one prompt file.
9. As a Skill author, I want the original archive and complete content identity retained, so that I can recover the exact material that was evaluated.
10. As a Skill author, I want changed content imported as a new immutable Skill 版本, so that later edits cannot change the meaning of an old result.
11. As a benchmark operator, I want each 测评批次 to select one Skill version, so that its answer and usage have one unambiguous tested object.
12. As a benchmark operator, I want an explicit 无待测 Skill 基线 option, so that I can evaluate DSH without the candidate Skill using the same task contract.
13. As a benchmark operator, I want a selected Skill to be explicitly loaded with auditable evidence, so that I measure Skill 执行效果 rather than accidental discovery.
14. As a benchmark operator, I want baseline runs identified explicitly, so that a legitimate baseline is not rejected for lacking Skill-loading events.
15. As a benchmark operator, I want personal rules, unrelated Skills and automatic repository instructions excluded, so that hidden context does not contaminate the comparison.
16. As a benchmark operator, I want each case to start with a fresh session and isolated workspace, so that memory does not leak across cases or batches.
17. As a data owner, I want reference answers, judge material and other workspaces inaccessible to DSH, so that the reviewer cannot obtain the scoring targets.
18. As a Skill author, I want my Skill text and review strategy preserved, so that the evaluated behavior belongs to my original Skill.
19. As a benchmark operator, I want a common task and structured-answer contract, so that both Skill and baseline answers can use existing scorers.
20. As a platform operator, I want read-only inspection enforced beyond prompt instructions, so that a Skill cannot modify the case or execute arbitrary project code.
21. As a platform operator, I want dependency installation, Skill scripts, sub-agents and reviewer web access disabled, so that the first release has an explicit capability boundary.
22. As a Skill author, I want required but unavailable capabilities reported as unsupported, so that a silently weakened run is not presented as my Skill's quality.
23. As a platform operator, I want the batch to stop when the explicitly selected runtime mode is unavailable or its frozen conditions change, so that sandbox failure never silently falls back to POD execution.
24. As a benchmark operator, I want a configurable per-case wall-clock limit with a ten-minute default, so that long-running reviews have a predictable boundary.
25. As a benchmark operator, I want the full selected case list reflected in progress and batch budgeting, so that fifty cases are not treated as one ten-minute task.
26. As a benchmark operator, I want hidden DSH request and recovery retries disabled, so that a failed attempt is not silently converted into extra cost.
27. As a benchmark operator, I want normal tool turns and configured context compaction counted in time and usage, so that valid review activity remains visible.
28. As a benchmark operator, I want a complete zero-finding answer accepted, so that finding no actionable defect is distinguished from failing to review.
29. As a benchmark operator, I want malformed, interrupted, timed-out or unloaded-Skill answers recorded separately, so that they cannot masquerade as successful empty answers.
30. As a benchmark operator, I want a case-level review failure to preserve evidence and allow remaining cases to proceed, so that one failed case does not erase the batch.
31. As a platform operator, I want changes to frozen conditions to stop the batch, so that one result set does not mix incompatible runtime or Skill versions.
32. As a benchmark operator, I want cancellation to stop the review process tree and retain logs, so that stopping a task does not leave paid activity running unnoticed.
33. As a benchmark operator, I want a service restart to leave the run interrupted rather than automatically resume it, so that restarting infrastructure cannot trigger hidden model calls.
34. As a benchmark operator, I want review recovery to parse existing evidence without model calls, so that inspecting or recovering records cannot rerun the review.
35. As a benchmark operator, I want failed reviews retested in a new batch, so that the original attempt and its evidence remain intact.
36. As a benchmark operator, I want judge budgets, rounds and retries to follow the selected scoring protocol, so that adding DSH does not redefine an existing evaluator.
37. As a benchmark operator, I want ordinary reports to show completion, failures, timeouts and actual scoring scope, so that I can understand which cases contributed to a score.
38. As a benchmark operator, I want known token usage and its coverage disclosed separately for review and judging, so that missing usage is not presented as zero cost.
39. As a benchmark operator, I want raw answers, loading evidence and redacted events available with the run, so that I can investigate unexpected findings and failures.
40. As a platform operator, I want invalid or unsafe archives rejected before use, so that importing a Skill cannot escape its asset directory or exhaust storage without bounds.
41. As a platform operator, I want COS-mode imports to become selectable only after persistence is confirmed, so that a reported successful import can survive container replacement.
42. As a platform operator, I want Skill versions restored and verified before service availability, so that restored runs do not reference missing or corrupted assets.
43. As a platform operator, I want DSH evidence included in the existing run backup scope, so that temporary session cleanup does not remove the only loading proof.
44. As a platform operator, I want synchronization failures and the asynchronous backup window disclosed, so that operators do not mistake periodic backups for zero-loss durability.
45. As an existing benchmark user, I want LCR, server Agent, Cline and multi-model behavior unchanged, so that adding a tested tool does not break established workflows.
46. As an evaluator of Skill effects, I want to create separate runs with matched models, cases, permissions, budgets and scoring, so that I can make a meaningful controlled comparison without automatic grouping.
47. As a platform operator, I want deterministic local regression tests without real models or COS, so that routine validation does not incur external charges or expose secrets.
48. As a platform operator, I want Linux runtime and sandbox readiness verified explicitly, so that documentation from a Windows distribution is not mistaken for a usable Linux installation.

## Implementation Decisions

- 将 DSH 作为新的 reviewer 接入现有被测工具注册、能力匹配、后端白名单及命令路由，不新建独立实验产品或更改现有多模型编排。
- 扩展现有创建请求及冻结配置，明确记录被测工具、Skill 模式及不可变版本；这些信息须贯穿请求、队列、初始化、执行、恢复和报告，不能只在前端保存一个选择值。
- Skill 版本选择与显式基线模式互斥。后端验证被引用版本已成功导入且内容完整，不以展示名称或前端校验代替引用与内容验证。
- 一个普通批次仅使用一个 Skill 版本，或显式基线模式。选择 DSH 不自动创建其他批次，不对案例数量乘出额外实验组。
- 模型、接口、裁判和评分方法沿用现有配置角色。首期仅在 AACR 能力范围启用 DSH，其他专项不因增加选项而被宣称支持。
- 案例范围复用内置和动态注册清单，包括已导入 50 例。冻结实际清单身份和提交范围，以现有资源验证结果为准，不按名称或固定 pilot 标识推断可用性。
- 在现有资源管理范围补充完整 Skill ZIP 的导入、版本登记和选用。一个包对应一个 Skill 及其配套资源，不直接引用可变服务端目录。
- 保留原 ZIP、逐文件清单和完整内容身份，区分压缩包字节摘要与解包内容摘要。同名内容变化属于新版本；历史批次不随新导入内容变化。
- 安全解包限制路径、符号链接、重复条目、文件类型、条目数及解压大小；导入不执行包内代码。具体上限沿用或在现有上传配置模式下明确，不凭空承诺任意大小。
- 提供相同的任务与统一 JSON 答卷契约，不修改 Skill 正文、检视策略或最终结论，不使用另一个模型将不兼容答卷改写为可评分答卷。
- 沿用快照检视上下文，提供 head 代码及 merge-base diff；参考答案、裁判材料、个人目录和其他批次内容不能成为检视输入。
- 显式加载指定 Skill 并验证实际加载证据。不能仅凭发出了加载指令判定已加载，也不能将显式注入模式没有工具调用事件误判为加载失败。
- 基线模式明确不加载待测 Skill，但采用相同任务、答卷协议和权限设置。配置、证据及报告须区分基线和加载失败。
- 隔离个人配置、其他 Skill 与仓库指令的自动注入，禁止跨案例和跨批次记忆；不为去掉指令而改写案例仓库内容。
- 每案例使用全新会话和隔离工作区。固定并记录执行器版本、Skill 身份、模型、输入、预算、权限、重试及上下文压缩配置。
- 两种模式均限制可用工具，不开放项目或 Skill 脚本执行、安装依赖、子代理和检视工具联网能力。sandbox 模式另要求 Linux 只读及 namespace 隔离实际生效；pod 模式只能保证软件检查路径，不能声称阻止进程级读写或联网。受控模型及裁判接口通信不属于检视工具联网。
- 依赖禁用能力的 Skill 标记不支持并留证，不在静默削弱后发布质量分。不能只凭包中存在脚本推断它必须执行脚本。
- 所选运行模式不可用或冻结条件变化时停止整个批次，不自动切换模式。sandbox 的隔离保证不能用提示词、只读文件标志或软件 guard 代替；pod 须明确披露无额外操作系统级沙盒。
- 案例串行执行，每案例默认检视总时限 10 分钟，沿用现有分钟配置。由平台实现整例监督与进程树终止，单请求超时不替代整例截止；清理耗时单列。
- 批次预算、任务进度与外层监督按真实案例清单计算，不把五十例共用一个案例时限，也不沿用五例规模的固定估算。
- 关闭平台 DSH 检视重跑及其内部模型请求、压缩失败和上下文溢出恢复的额外重试。正常工具循环与固定策略的上下文压缩仍计入时间和用量；选定提供方的实际行为必须验证。
- 仅完整结束、符合答卷契约且满足当前模式证据要求的结果成为完整答卷。空意见是有效结果，缺失、无效或失败不是空意见。
- 意见沿用现有答卷的位置与内容契约，使用仓库相对路径和 head 版本的合法起止行号；非法路径、无效行号或空内容不能作为有效意见进入评分。
- 单例检视失败保留证据并继续其余案例；系统级错误停止整个批次。用户停止及服务中断不自动续跑，失败检视重测另建批次。
- DSH 检视恢复只解析已有完整记录，不发起模型请求；已有成功结果不被恢复操作改写。裁判的评分与恢复继续遵循用户所选协议。
- 评分选择保持与 reviewer 正交，复用现有 AACR 原始评分或 Martian 等可用协议。裁判轮次、预算、匹配和重试不统一替换为先前讨论的专用一对一方案。
- 沿用普通批次报告、执行记录及下载，增加 DSH、Skill 版本或基线模式、加载证据、完整性和实际用量。披露完成率、失败、超时及实际评分范围，不新增自动跨批次主表或排名。
- 检视和裁判耗时、用量分开，缓存不重复累加，缺失用量不解释成零消耗，已知用量不冒充完整账单。未匹配参考不直接宣称为事实误报。
- Skill 资产沿用现有资源持久化原则：本地模式本地落盘；COS 模式确认对象和版本记录保存后才显示导入成功，失败版本不可供新批次选择。
- 上传确认和恢复校验使用准确口径：沿用现有远端大小及摘要元数据确认，下载恢复时验证实际内容摘要；不可变内容冲突阻断，不静默覆盖或继续使用。
- 启动服务前补齐 Skill 资产恢复与验证。普通批次材料沿用已有运行备份范围，脱敏会话和加载证明须明确进入该范围，不只保留在临时 HOME。
- 资源与运行材料的存储模式继续独立处理，不另建备份系统。运行材料沿用异步备份、失败警示与正常退出最终同步，不承诺强杀或网络故障时零丢失。

## Testing Decisions

- 主测试边界采用现有公开工作台 API、持久队列与浏览器流程：导入资源、选择 DSH 和 Skill 或基线、保存或启动批次、执行完整清单、选择现有评分、读取报告。优先验证这一完整生命周期，不为测试新增业务接口。
- 测试只断言可观察行为和持久化证据，例如队列任务数、实际案例身份、完成状态、答卷、评分范围、调用次数与残留进程；不通过检查源码字符串、私有函数调用顺序或配置文本存在来冒充功能验证。
- 复用已有浏览器上传与创建流程、五十例串行队列专项和恢复专项。验证保存配置不调用模型、一个工具选择不暗中生成基线或其他 Skill 组，条件字段在切换工具时正确清理或验证。
- 对已导入 AACR 五十例使用本地确定性执行器和裁判替身，核对清单冻结、每例实际提交、五十份答卷、实际评分范围、进度及工作区清理；模拟范围不能被报告为真实模型五十例质量验收。
- 专项契约测试复用现有 Cline + Skill 的答卷完整性和加载证据先例，以及服务端 Agent 的范围校验、脱敏、未知用量与无重复创建先例，覆盖显式注入加载证明、基线、空答卷、缺失结束、格式错误、越界位置和失败状态。
- 对 reviewer 与进程运行契约做必要的故障注入：使用会产生后代进程的本地受控程序，验证超时、停止、重启、进程树回收、失败证据与无自动重跑；不能仅断言发过终止信号。
- Linux DSH 与本地模型接口替身联调，核对实际重试次数、压缩恢复行为、整例截止和实际加载日志。只看 patch 或提供方配置值不是生效证明。
- sandbox 隔离专项须真实尝试写入、读取其他目录和联网；pod 专项验证真实 DSH 工具/路径拒绝、完整 Skill 加载、模式冻结、独立会话、模型通道和进程树清理，不把软件拒绝等同于 OS 隔离。两种模式都验证受限能力的状态归类，不访问真实模型服务。
- Skill 资产测试复用现有资源导入及存储契约先例，使用临时目录和本地 COS 替身覆盖安全解包、内容摘要、不可变版本、保存失败不可选、恢复校验、内容冲突及启动失败。
- 持久化测试复用现有运行备份与长任务恢复先例，验证 DSH 脱敏事件和加载证明进入可恢复材料，临时会话清理后仍可读取，恢复不产生新模型调用。
- 针对相同 DSH 完整答卷分别走现有原始评分和 Martian，验证配置冻结、协议预算和恢复语义未被 reviewer 替换；同时回归 LCR、服务端 Agent、Cline 与现有多模型创建和执行路由。
- 测试保留单例失败和评分排除范围，不把失败算成有效零意见。各测试说明模拟边界，不以通过场景数量推导系统无问题。
- 实施后执行项目 check 和 verify；前端、上传、队列和恢复变更必须执行本地 e2e，异常恢复与稳定性排查执行 e2e:explore，记录故障注入方式、持续时间及模拟边界。依赖、浏览器、lint、类型或构建失败必须如实阻断交付。
- 自动回归不得调用真实模型或真实 COS；Linux DSH 实际运行、浏览器本地模拟与行内镜像验收分别报告。浏览器回归不加入生产镜像构建。

## Out of Scope

- 独立的 Skill 实验入口、自动多 Skill 分组、自动无 Skill 基线批次、跨批次主表、差值、排名及自动对照条件校验。
- 改造现有多模型实验，或支持多模型与多 Skill 的二维组合。
- 新增专用一对一评分器，或因 reviewer 选择而改变旧协议的裁判轮次、预算、匹配、重试及历史结果。
- Skill 自主发现或选择能力测评；加载后执行效果是本期目标。
- 非 AACR 案例集、多轮状态评估、可执行修复验证，以及将单例或五例作为数量白名单。
- 运行项目或 Skill 脚本、依赖安装、子代理、Reviewer 联网工具和未显式选择/未记录运行模式的执行；pod 进程不受 OS 网络隔离这一限制须明确披露。
- 修改待测 Skill 正文、检视策略或结论，模型重写格式失败的答卷，以及静默降级受限 Skill 后评分。
- 失败检视自动重跑、服务重启自动续跑、在原批次重测失败检视；所选裁判协议自身已有的显式恢复不属于这一排除。
- 重复检视采样、置信区间、显著性判断、自动普遍优劣结论及完整官方榜单复现。
- Git 在线拉取、Skill 市场、自动版本更新、可变目录引用及新的独立备份系统。
- 强杀瞬间零丢失保证、完整计费推断、真实模型或真实 COS 自动回归、自动提交或推送代码。

## Further Notes

- 本规格依据最终确认的被测工具范围与领域术语，替代早先的独立实验、多组自动编排、固定一对一评分及只支持单例或五例的讨论。既有 ADR 的受控上下文、完整 Skill 版本和持久化原则继续适用。
- 用户提供的 DSH 资料是 Windows 发行包，内含版本为 @deepseek-ai/dsh 0.2.1-rc.2。Linux 发行依赖、Node 要求、离线可用性、非交互运行及沙箱仍需核验；不得静默换版本或将 Windows 文档当成 Linux 验收结论。
- 包内资料显示默认模型请求最多重试五次，另有压缩与溢出恢复；headless 未确认提供整例时限开关。实施必须通过实际 Linux 联调验证关闭重试与外层终止，不能只复制配置示例。
- 当前已登记清单的内容和资源可用性是执行依据。“官方正样本 50 例”是用户现有清单，不等于已经证明完整官方数据集、官方固定抽样或真实模型五十例测评通过。
- 当前仍仅完成设计与规格整理，尚未改业务代码、运行 Linux DSH 或完成浏览器业务验收。项目缺少 .venv，check 和 verify 在启动阶段被阻断，不得称作已通过。
- 测试边界拟沿用上述现有入口，已提交本轮核对，不将未收到反馈视为确认；功能需求不重新访谈。
- 项目 issue tracker 已配置为本地 Markdown，约定见 docs/agents/issue-tracker.md。规格发布时写入对应功能的本地 spec.md，并设置 Status: ready-for-agent，不附加额外分诊。本文件仍为规格资料，尚未在本地 tracker 创建对应规格文件，不代表已经发布。
