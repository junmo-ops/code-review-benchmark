# 02: 完整 Skill 包导入与不可变版本管理

**What to build:** Skill 作者和测评人员在工作台现有资源管理范围中导入完整待测 Skill 包，查看可用版本、资源和内容身份，并保留可追溯的原包。该本地资源管理路径可独立演示，不以 DSH 运行环境准备完成为前提，也不提前声称已能执行 Skill 检视。

**Blocked by:** None (can start immediately).

**Status:** done

- [x] 本地模式提供完整的上传、导入进度、成功或失败反馈、版本列表及版本详情；一个包对应一个 Skill 及其引用资源，不只提取主说明，也不引用可变的服务端目录。
- [x] 完成导入前验证 Skill 元数据和包结构，保留原 ZIP、逐文件清单及完整内容身份；压缩包字节摘要与解包内容身份分开记录。
- [x] 内容变化形成新的不可变版本，已有版本的内容不被同名导入覆盖；展示名称不充当版本身份，重复导入不能损坏已有可用内容。
- [x] 解包拒绝路径穿越、绝对路径、符号链接、重复或冲突条目及不支持的文件类型，限制文件数量和解压大小；采用结构化 ZIP 元数据及现有安全导入模式，不执行包内内容。
- [x] 主说明及引用资源的内容保持原样；包中存在脚本不直接等于该 Skill 必须执行脚本，导入不擅自改写检视策略或替换资源。
- [x] 不完整、失败或内容不一致的版本不作为可用版本发布；通过后端验证资产和引用，不仅依赖前端表单校验。
- [x] 本票只完成本地资产流程；在 COS 模式尚未接入时明确拒绝或提示当前不支持，不把仅本地落盘显示成已持久化成功，后续由 05 补齐。
- [x] 用临时目录、合法资源包和危险包通过现有资源 API 及浏览器入口验证导入、失败反馈、不可变版本和原包追溯；不依赖 DSH、真实模型或真实 COS。
- [x] 按项目约定完成 check、verify、本票专项及上传页面 e2e；导入异常恢复排查运行 e2e:explore 并记录注入方式、持续时间及模拟边界，未通过时保留阻塞而非标为 done。

## Comments

### 2026-10-21: Local Asset Implementation, Acceptance Still Gated

仅实施本票。01 的未提交内容全部保留；没有提交、推送、发布或打交付 ZIP。资源页为 `/skills`，不增加批次选择或执行 Skill 的能力。

开始前工作区快照为 `/tmp/dsh-ticket02-baseline.tar`，SHA-256 `1b7c01759953aa0c3cf6aa7f484399df06b0af4fdcc6d4e856d73e0eaa4c7858`；展开在 `/tmp/dsh-ticket02-baseline`。两轴评审均对比该快照而非 HEAD，排除了既有 01 修改。本票差异清单记录在 `output/playwright/ticket02-evidence/ticket-delta.json`。

实现复用现有 `/api/uploads/*` 分块上传和 `/api/cases/imports?id=...` 后台任务，新增只读 `/api/skills`、版本详情、按清单下载资源及原包。发布使用暂存目录校验后原子移动；版本身份来自逐文件路径、大小和 SHA-256 的确定性 JSON 清单。主说明和资源字节不变，脚本只作为资源保存；同内容重打包保留各原 ZIP。列表、详情和下载校验资产，损坏版本不可用且不能被同名重导静默覆盖。

安全限制：ZIP 默认 32 MiB、解压 64 MiB、2000 条目、主说明 1 MiB；复用 MiB 环境配置模式，受公共上传上限约束。ZIP 元数据经 `zipfile` 检查，YAML 经 SafeLoader 校验，本地 Markdown 链接经 Mistune AST 核对。原包允许根目录或单一外层 Skill 目录；不取用可变服务端目录。COS 资源模式后端拒绝导入和版本读取，页面明确提示不支持。

TDD 边界为已确认的公开资源 API 与浏览器。先得到上传类型拒绝、缺失引用资源、重复元数据、重打包原包遗漏等 red，再实施转绿。Spec 评审复现 manifest 可更改主说明或元数据而保持版本 ID 的 P2；公开 API 三种损坏用例先失败，修复后通过。最终 Standards 0 项，Spec 0 项剩余发现；评审由两个独立子代理完成，没有另开 TUI session。移动页截图暴露的顶部标题与控件重叠已通过仅移动断点的布局修复。

本地验证记录目录为 `output/playwright/ticket02-evidence/`：

- `npm run check` 通过（`check-final.log`）；Python 复杂度、前端/Node lint、Hooks 和 TypeScript 均通过，未提高阈值或修改历史基线。
- `npm run verify` 退出 0（`verify-final.log`）；仍输出既有 Vite 大 chunk 警告，不能称为无警告交付。
- `test_skill_assets.py` 10 项通过（`skill-api-final.log`）；临时目录覆盖合法包、原字节/CRLF、同名变化、重复 ZIP、危险路径/链接/类型/冲突、限额、缺失引用、元数据及内容损坏、落盘失败后重试、COS 拒绝和只读角色。
- `.venv/bin/python tools/check_regressions.py` 38 项通过（`regressions-final.log`），已将本票 API 专项纳入标准回归。
- 本票独立浏览器专项通过，真实本地 API、ZIP 和持久化，无 DSH、模型或 COS；桌面、390px 页面及版本详情截图见 `output/playwright/2026-10-21T09-15-59-462Z/`。该专项也在不设置 LCR 模拟变量时独立通过。
- 裸 `npm run e2e` 在旧 LCR 初始化场景因缺少 `lcr/dist/lcr-linux-x64` 失败（`e2e.log`）；裸 explore 同因失败，主动终止该无效长跑（`explore.log`），不伪报通过。
- `E2E_SIMULATED_LCR=1 npm run e2e` 全部 13 项通过，浏览器错误 0（`e2e-simulated.log`；`output/playwright/2026-10-21T09-13-23-334Z/results.json`）。这是既有本地确定性 LCR 身份、执行和裁判替身，不是真实 LCR/DSH 或模型质量验收。
- `E2E_SIMULATED_LCR=1 npm run e2e:explore` 34 项通过、浏览器错误 0，场景执行累计 354.855 秒（`explore-simulated.log`；`output/playwright/2026-10-21T09-13-24-478Z/results.json`）。含 60 秒本地队列/导航 soak（103 次循环）；这不证明项目没有问题。
- Skill 恢复故障通过浏览器网络路由注入：真实本地 API 已保存分块后丢失响应，刷新续传完成，4359 ms；已创建后台导入后丢失 complete 响应，刷新恢复结果，3573 ms。两种受影响请求均实际只发 1 次，无重复分块或导入重放；见同目录 `skill-recovery.json`。这是单机浏览器响应丢失，不模拟断电、内核崩溃、真实 COS 或模型故障。公开 API 专项另通过将版本发布目录替换为文件注入落盘失败，再移除故障验证重试；失败期间无可用版本。
- 全发现测试已实际运行：198 项，413.644 秒，17 failures、51 errors（`full-unittest.log`）。与已有 01 的 188 项日志逐项比较，归一化项目根路径、临时目录、随机 nonce 和生成的 bundle 文件名后，新增/移除/改变的失败签名均为 0（`historical-failure-comparison.json`）。这是历史日志对比，不冒充对精确本票快照的新一轮全测试；全发现测试并未通过。

保留 `ready-for-agent`，不虚标 done：全发现测试仍失败，旧 LCR 二进制缺失，以及既有构建 chunk 警告均如实保留；本票不越界修复其他票或历史环境。静态验证、浏览器本地替身、真实执行器及行内镜像验收是不同证据，未进行真实模型、真实 COS 或行内镜像验收。

已启动独立临时数据目录的本地预览 `http://127.0.0.1:5203/skills`（API 8803），开发进程 PID 67789，数据在 `output/playwright/ticket02-preview/runtime`，日志在其上级 `dev.log`。预览没有启动任何测评任务，也不改动用户运行数据。

### 最终结票

历史失败与警告已由用户明确授权的独立修复 session 处理，原记录保留，不再代表当前门禁。编排已读取实际最终日志和结果：全量 207 项、标准回归 38 项（含本票 10 项 API）全部通过；check/verify 通过且无 chunk 警告；集成浏览器 14 项通过，包含本票真实本地资源流程；explore 34 项通过，保留响应丢失注入与 60 秒观察证据。参见 `.scratch/dsh-skill/regression-repair.md` 及其最终产物路径。

本票独立双轴评审已无剩余发现，资源导入不依赖真实 LCR。最终 LCR 流程使用明确的本地替身，不声称安装或验收真实 LCR；本票并无此运行依赖。无真实模型、真实 COS 或行内镜像验收。现在本地资产验收门禁全部满足，标记 done；COS 持久化仍留给 05，不借此宣称已实现。未提交、推送或打包。
