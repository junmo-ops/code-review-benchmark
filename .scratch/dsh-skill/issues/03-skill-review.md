# 03: 指定 Skill 版本完成常规测评

**What to build:** 测评人员在现有被测工具选项下选择一个已导入 Skill 版本，使用 Linux DSH 实际加载该版本并完成检视、独立评分与报告。批次始终可追溯到完整待测内容，不修改 Skill 策略，也不把未加载或受限执行当成 Skill 成绩。

**Blocked by:** 01: 无待测 Skill 基线贯通现有测评；02: 完整 Skill 包导入与不可变版本管理。

**Status:** done

- [x] 现有创建表单支持选择一个成功导入的 Skill 版本，或明确选择无待测 Skill 基线；模式互斥，切换工具或模式时不会携带无效参数，不自动创建其他组或批次。
- [x] 版本和模式经创建请求、队列、初始化、执行及报告完整传递并冻结；后端校验版本可用性、完整内容和请求一致性，不以名称或前端检查替代。
- [x] 排队和执行时使用绑定的不可变内容，后续同名 Skill 导入不能改变旧批次；冻结条件改变或内容损坏时阻断，不静默改用其他版本。
- [x] Linux DSH 实际显式加载指定 Skill 及可读取的配套资源，保持原文和检视策略；加载模式与基线采用一致任务、答卷契约、只读权限及新会话隔离。
- [x] 保存并核验实际加载证据，覆盖显式注入和工具加载的不同记录形式；不能仅凭启动参数、发出加载指令或被截断的事件投影宣称加载成功，也不要求显式注入必须产生 Skill 工具调用。
- [x] 缺少加载证据、未完整结束、格式错误和非法意见不能进入完整答卷；不借助另一个模型改写结论或修复格式后评分，完整零意见答卷仍有效。
- [x] 依赖禁用能力的 Skill 明确标为不支持并保留依据，不静默削弱后发布质量分；包中存在但未使用的脚本不被武断认定为必需能力。
- [x] 输出沿用现有答卷位置和内容契约；相同 Skill 答卷可分别使用现有原始 AACR 或 Martian 协议，评分选择和裁判行为不与 reviewer 耦合。
- [x] 普通报告、执行记录和下载包含 Skill 版本或基线模式、完整性、加载证据、脱敏原始结果及已知用量；不新增独立实验、跨批次排名或自动基线差值。
- [x] 通过现有公开 API、队列和浏览器路径验证一次 Skill 测评，并用 Linux DSH 与本地接口替身验证实际加载和重试行为；覆盖无效版本、排队后新增版本、基线切换和不支持的能力。
- [x] 按项目约定完成 check、verify、专项测试及本地 e2e，回归 LCR、服务端 Agent、Cline 和既有多模型流程；自动回归不访问真实模型或真实 COS。

## Comments

### Implementation Baseline And Boundaries

- This independent implementation session handles ticket 03 only. Tickets 01/02 and the historical regression repair were already complete. All pre-existing uncommitted changes are retained. No commit, push, publication, package or additional implementation TUI thread is authorized.
- Exact pre-03 workspace snapshot: `/tmp/dsh-ticket03-baseline-AezGER/workspace.tar`, SHA-256 `bfd8b1dc871feb4f99cbdaffdb7d072c62e4c6eedf0e1d7ff36acf01f0b205d0`; extracted tree: `/tmp/dsh-ticket03-baseline-AezGER/tree`. The archive includes tracked and nonignored untracked files. HEAD alone is not this ticket's review baseline.
- Agreed test boundaries: authenticated resource/creation APIs, durable queue and real worker, ordinary browser creation/report/download workflow, public runner CLI with real pinned Linux DSH and real Git. Only the model/judge HTTP endpoint is a loopback deterministic substitute; no simulated DSH executor, real model or real COS is used.
- Red/green evidence is retained under `output/playwright/ticket03-evidence/`. `api-red.log` rejects Skill selection before implementation; `api-green.log` proves complete identity binding survives a queue restart and later same-name import. `runtime-red.log` shows the original CLI rejection; `runtime-green-01.log` proves actual native DSH injection of the full original long Skill and readable bound resources.
- `tool-red.log` fails recovery without injection despite an actual native Skill tool result; `tool-support-green.log` verifies the full tool record and actual model-channel input, rejects truncated evidence, and prevents forbidden capability attempts from producing quality answers. `support-red.log` and `support-status-red.log` retain the earlier successful-answer / generic-recovery misclassification. `integrity-red.log` exposes non-string mode HTTP 500 and ignored batch manifest damage; `integrity-green.log` verifies both are blocked.
- `browser-red.log` records the absent Skill-mode option. `browser-green-01.log` and `output/playwright/2026-10-21T10-16-01-445Z/results.json` verify the new form, stale-field clearing, both scoring choices, actual Linux DSH loading and ordinary report downloads. This is intermediate evidence, not the final gate result.
- Final full-suite, static/build, real DSH, browser/exploration and independent two-axis review results are recorded below. Ticket 04 queue-scale/recovery and ticket 05 COS asset persistence remain out of scope.

### Additional Integrity Boundaries

- The 902-file accepted package reproduced an initialization failure when the full identity was passed as one command argument (`large-identity-red.log`). The queue retains the full identity and now passes its canonical SHA-256 to the CLI; initialization still verifies all actual files, metadata and original ZIP before comparing the complete identity. This is not a name-only or digest-without-content check.
- `nested-case-red.log` reproduces the misplaced model evidence file for a legal dotted case ID containing `.requests.json`. The fix replaces only the terminal evidence suffix. The same real-DSH test covers a nested Skill resource directory containing `&` and asserts the mode-specific frozen tool surface.
- `case-copy-red.log` injects a host-side write to the staged resource while actual DSH is awaiting the local model response. Previously the result could become a complete answer. The case copy is now verified both before and after execution; its durable integrity record remains pending until all postchecks succeed. Recovery rejects a pending record without making model calls, even when the DSH process exited zero.
- `edge-green.log`: all three expanded public worker/real-DSH tests passed in 55.130 seconds. The case-copy mutation is a controlled host fault, not a claim that DSH can write through its read-only mount. Baseline-mode tampering and original-batch content/manifest corruption are also blocked before model calls.
- Complete loading is checked using the matching native session identity, full injection or correlated successful native Skill tool result, and the exact full text delivered on the model channel. Truncated projected events or an existing `loaded` flag alone are insufficient. A separate fault truncates the actual persisted model input and recovery fails; restoring the original evidence recovers byte-identical results without new requests.
- Second reviewed code endpoint, superseded by the scope-denial fix below: `/tmp/dsh-ticket03-final-review-kf6xM4/tree` (archive SHA-256 `aa9b902719292a0ab6636fd30dec42b0cab4dbade484bfa03a7ffdcda241dc1b`). Both read-only review axes re-reviewed the full 03 delta against the original exact baseline: Standards 0 findings, Spec 0 findings. Agents: `/root/ticket03_standards_review` and `/root/ticket03_spec_review`. Their reports do not certify the separately running gates.

### Scope-Denial Review Fix

- A follow-up Spec audit found one P2: an allowed-name `read`/`glob`/`grep` call could receive an explicit isolation-scope denial and then return a complete empty answer. `read-scope-red.log` reproduces this with real Linux DSH reading an existing runtime file outside the permitted scope. The earlier 217-test discovery, 32-test DSH, browser and exploration passes are retained as `*-pre-read-scope.log`; they are superseded, not final acceptance for this fix.
- The guard now emits a distinct policy-denial marker. Verification requires an actual error result, correlates it to the tool call, and retains the denial plus both native sequence numbers as unsupported-capability evidence. It does not classify ordinary missing-file or invalid-target errors as a required unavailable capability.
- `read-scope-green.log`: the real-DSH regression passed in 39.959 seconds, proving both policy-denial exclusion and a valid complete answer after an ordinary missing case file. No real model or COS was contacted.
- Final reviewed endpoint: `/tmp/dsh-ticket03-reviewed-PlddNA/tree`; archive SHA-256 `1c4843037c80bd0bc6d8e39cdfae945d4fb3cf5dcfd71c3a34734edaaa7b1469`. Both axes re-reviewed this delta and its integrity/recovery interactions: Standards 0 open findings; Spec P2 closed, 0 open findings. Review is read-only and separate from the actual gates.
- The broader DSH contract harness has a 900-second verification-command budget because the earlier 32-method run took 625.456 seconds. Per-case deadlines, request/compaction/overflow retries, complexity limits, warning handling and frozen historical baselines are unchanged.

### Final Commands And Results

Full discovery, from the project root:

```bash
env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 PYTHONUTF8=1 PYTHONUNBUFFERED=1 \
  BENCH_PROJECT_ROOT="$PWD" BENCH_RUN_STORAGE_MODE=local BENCH_CASE_STORAGE_MODE=local \
  BENCH_MODEL_STORAGE_MODE=local PYTHONPATH="$PWD/backend/src:$PWD/backend/tests:$PWD" \
  .venv/bin/python -m unittest discover -s backend/tests -v
```

Other final commands:

```bash
env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 PYTHONUTF8=1 \
  BENCH_PROJECT_ROOT="$PWD" BENCH_RUN_STORAGE_MODE=local BENCH_CASE_STORAGE_MODE=local \
  BENCH_MODEL_STORAGE_MODE=local .venv/bin/python tools/check_regressions.py
npm run check
npm run verify
npm run verify:dsh
E2E_DSH=1 E2E_SIMULATED_LCR=1 npm run e2e
E2E_SIMULATED_LCR=1 npm run e2e:explore
```

All final command processes exited 0. Logs below are under `output/playwright/ticket03-evidence/`; failed and superseded logs remain separate. Static lint/type/build verification is not substituted with syntax conversion, content assertions or browser simulation.

| Gate | Final Outcome | Evidence |
| --- | --- | --- |
| Full backend discovery | 218 tests passed, 688.591 seconds; no failures, errors or skips reported | `full-unittest-final.log` |
| Standard regressions | 38 tests passed, 29.663 seconds | `regressions-post-review.log` |
| `npm run check` | Python complexity, frontend/Node lint with zero warning tolerance, Hooks and TypeScript passed | `check-final.log`; closeout rerun below |
| `npm run verify` | Check, 9 Node contracts, production build and diff checks passed; no chunk warning | `verify-final.log`; closeout rerun below |
| `npm run verify:dsh` | 33 test methods passed, 650.507 test seconds; command 650.732 seconds | `dsh-final.log`; `output/playwright/dsh-contract-2026-10-21T10-38-43-388Z/results.json` |
| Integrated browser regression | 15 scenarios passed, 256.600 cumulative scenario seconds; browser errors 0 | `e2e-final.log`; `output/playwright/2026-10-21T10-38-10-295Z/results.json` |
| Browser exploration | 34 scenarios passed, 345.539 cumulative scenario seconds; browser errors 0 | `explore-final.log`; `output/playwright/2026-10-21T10-38-11-465Z/results.json` |

### Validation Boundaries And Closeout

- Real runtime verification uses pinned Linux DSH 0.2.1-rc.2, Node v25.8.2, real Git, Bubblewrap isolation and loopback-only deterministic model/judge responses. The DSH suite also includes API/queue assertions and explicitly controlled process-tree fixtures; not every test method is a separate DSH invocation. No simulated executor substitutes for the actual Skill-loading runs.
- The 11 new test methods cover complete version binding, invalid/corrupt requests, real queued initialization with a 902-file package, post-enqueue same-name imports, full long-text injection and resources, native tool loading, truncated/missing loading evidence, damaged batch/case copies, baseline-mode tampering, unsupported capabilities and scope denials, ordinary missing files, malformed/truncated/illegal answers, and actual HTTP 503 request counts. Existing retry, overflow, compaction, timeout, process cleanup and recovery contracts remain in the full DSH run.
- Browser evidence exercises the actual creation form, mode/tool switching, Skill selection, configuration-only save, real DSH workflow, independent original AACR and Martian scoring, ordinary reports and report downloads. Screenshots, traces, downloaded reports and frozen run evidence are beside the final results. Same answer comments pass both existing scoring routes without changing judge rounds or retry policy.
- Exploration injects delayed/lost responses, JSON/HTML 503s, malformed state/local storage, expired uploads, worker interruption and abrupt local server restart. The final bounded queue/navigation observation is 60 seconds with 98 iterations (`soak.json`). Skill response-loss faults drop replies only after real local API persistence: chunk recovery 5262 ms and completion recovery 3538 ms; each affected request is sent exactly once (`skill-recovery.json`).
- These are bounded single-machine process/browser/network faults, not power loss, kernel crash, real COS, production stability or model-quality certification. Scenario counts do not imply the project is free of defects. Existing fifty-case browser queue fixtures use simulated LCR, not ticket 04 DSH-scale acceptance.
- LCR, server Agent, Cline and existing multi-model creation/routing regressions remain covered by full discovery, standard regressions and local orchestration fixtures. This does not certify the unavailable real LCR Linux artifact, macOS Cline runtime or an external Agent deployment. Local browser acceptance and project static/build gates are separate from inline mirror and production-image acceptance, neither of which was performed.
- Both independent review axes have 0 open findings. The one confirmed Spec P2 was fixed and re-reviewed, then all final gates above were rerun. Documentation now describes selected-version execution without changing the historical ticket 01 baseline page.
- All pre-existing uncommitted work is preserved. No commit, push, publication, delivery package or additional implementation TUI thread was created. Synthetic archives made by tests remain temporary fixtures, not delivery ZIPs. Tickets 04/05 were not implemented. Ticket 03 stops after the closeout verification below.

### Final Closeout Verification

The documentation-only closeout reruns `npm run check` and `npm run verify` both exited 0 (`check-closeout.log`, `verify-closeout.log`). Lint, Hooks, TypeScript, Python complexity, Node contracts and production build passed; no chunk warning was emitted. `git diff --check` also exited 0.

Both axes reviewed the final README, ticket evidence and progress row with no remaining findings. A byte comparison against `/tmp/dsh-ticket03-reviewed-PlddNA/tree` confirmed that only those three documentation files changed after the reviewed source snapshot; no production or test code changed during final verification. This comparison is a scope audit, not a replacement for the actual project checks or runtime tests above.

Ticket 03 is complete. No required command remains running, and work stops here without starting 04 or 05.
