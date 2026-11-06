# 04: AACR 50 例连续运行与异常处理

**What to build:** 测评人员可用 Skill 模式或无待测 Skill 基线运行已导入的完整 AACR 50 例清单，看到准确进度和评分范围，并能安全停止任务、识别失败及恢复已有记录。一个失败案例不抹掉其余结果，中断和恢复不暗中产生新的检视调用。

**Blocked by:** 03: 指定 Skill 版本完成常规测评。

**Status:** ready-for-human

- [ ] Skill 和基线分别创建普通批次，在正常成功专项中各自按已冻结的完整 50 例执行；核对唯一案例身份、实际提交、答卷数量及评分范围，不按显示名称猜数量，也不截断为单例或五例。
- [x] 案例串行且使用独立会话和工作区，进度和批次外层预算按实际清单计算；默认 10 分钟是每案例检视时限，不是整个 50 例批次共用的时限。
- [x] 连续执行后工作区和子进程有界并及时清理，队列心跳与页面状态更新持续工作；采样结果及观察时长如实报告，不将模拟规模验收等同于生产容量或真实模型性能。
- [x] 单例检视失败保留其原始状态、耗时及证据并继续其余案例，失败不变为成功空意见；裁判失败、重试和恢复沿用用户所选评分协议。
- [x] 报告显示全量完成率、失败、超时和实际评分或排除范围；缺失耗时或用量不伪装为零，检视及裁判统计分开，不输出自动跨批次优劣结论。
- [x] 实际超时或用户停止会回收运行进程及后代，保留终止证据；通过本地受控程序及进程观测验证，不只断言曾发送停止信号。
- [x] 隔离失效或冻结条件变化停止整个批次而非当成可忽略单例故障，不自动放宽权限、换版本或重试。
- [x] 主动停止与服务中断状态明确区分；服务重启后中断批次不自动继续，也不重复创建 DSH 检视请求，失败检视重测另建批次。
- [x] DSH 恢复只解析已有完整记录，无模型请求且不覆盖成功结果；原始记录完整但尚未转换时可恢复答卷，不完整记录仍保持失败，不能伪造零意见。
- [x] 用本地执行器、模型接口和裁判替身验证完整 50 例、故障注入、队列取消及重启；保留请求次数、实际提交、评分范围、残留进程和工作区证据，不访问真实模型或真实 COS。
- [x] 完成 check、verify、本地 e2e 和 e2e:explore，记录各项故障注入方式、持续时间与模拟边界；缺少浏览器或依赖时报告阻塞，不以场景数量或静态文件断言代替流程验收。

首项本地合成 50 例双批次已通过；公司实际已导入清单仍须行内验收，因此保留未勾选且不标 done。

## Comments

### Exact Baseline And Scope

- Independent ticket 04 session only; preserve all pre-existing uncommitted 01-03 and regression-repair changes. No commit, push, publish, delivery package, ticket 05 implementation or additional TUI thread is authorized.
- Exact pre-04 tracked and nonignored untracked snapshot: `/tmp/dsh-ticket04-baseline-z8ijOd/workspace.tar`, SHA-256 `13571510ce70e5e685114d3f903ffcaec379d6898902c1051674ced26d01955e`; extracted tree `/tmp/dsh-ticket04-baseline-z8ijOd/tree`. HEAD alone is not the review baseline.
- Agreed boundaries are the user's specified authenticated upload/creation/action APIs, durable queue and worker, browser creation/report/recovery, public runner CLI, pinned native Linux DSH and separate locally controlled process executor. No test contacts a real model or real COS.
- The supplied workspace has the 196-row AACR source list but no registered company official-positive 50-row dataset or its imported Git resources. The user subsequently confirmed that these resources exist only inside the company and are not available locally. No further local search or user-input wait is required. After local gates and review, the target status is `ready-for-human`: the actual company list identity, submitted commit ranges and scoring scope remain for inline acceptance. Synthetic 50-row runs must not be substituted for that acceptance or used to mark this ticket done.

### Red/Green And Review Fixes

- Logs below are under `output/playwright/ticket04-evidence/`. `status-red.log` reproduces malformed output displayed as recoverable rather than failed; `status-frozen-green.log` validates failure state, honest elapsed records and frozen-condition postchecks even after a failed HTTP request (3 tests, 55.115 seconds).
- `queue-red.log` observes live controlled sandbox descendants and reproduces both cancellation workspace leakage under the outer five-second grace and a CLI continuing after worker death. `queue-green-01.log` passes both public API/real-worker probes in 60.726 seconds. Interrupted cleanup no longer performs unnecessary expensive dependency hashing before releasing its temporary directory; a worker-ownership observer interrupts orphaned CLI execution rather than allowing further paid work.
- `frozen-failure-red.log` proves an HTTP failure previously bypassed Skill integrity postchecks. Raw execution status and timing are retained alongside a separate failure outcome; isolation, cleanup confirmation and frozen-condition errors stop the entire queue workflow.
- Independent Standards review of the exact delta found 0 issues. Spec review found two P1s: between-stage configuration drift could become the new baseline, and restoring the old config could recover a stopped-condition case into a quality answer. Both were fixed and independently re-reviewed with 0 open findings. Agents: `/root/ticket04_standards_review`, `/root/ticket04_spec_review`.
- `between-stage-red.log` and `stopped-recovery-red.log` reproduce the two P1s using native DSH and loopback responses. `review-fixes-green.log` passes their public CLI regressions plus malformed-answer and failed-Skill-condition cases: 4 tests, 59.897 seconds. Initial configuration is now retained and verified before routing, at configure and after execution; recovery preserves valid existing answers first, then rejects stopped-condition evidence.
- Intermediate native scale logs `browser-scale-01.log`, `browser-scale-02.log` and `e2e-final-01.log` were invalidated by implementation/review source updates while their batches were frozen. They retain their observed fail-closed stops but are not successful scale evidence. `browser-recovery-01.log` was similarly invalidated before the second-case execution record was created.
- `lifecycle-final-01.log` also exposed a test-fixture-only environment lifetime error: the controlled cleanup-denial flag leaked into later tests. Its context now lives through fixture teardown; this was not a production cleanup failure. Failed and superseded logs are retained, not relabeled as passes.

### Final Validation

Final gates are in progress. Actual final exit codes, counts, observation durations, residual process/workspace evidence and simulation boundaries will be appended after completion. No final pass or ticket completion is claimed here.

### User Pause: 2026-10-21 11:45 UTC

The user explicitly paused this session. No further implementation or new validation is authorized until resumed. The paragraphs above describe the work in progress at that time; this section is the authoritative pause state. Status remains `ready-for-agent`, not done: an actual local defect still needs fixing before handoff. Once all local gates pass, the user-directed target is `ready-for-human` for company-only real-list acceptance, not done. Ticket 05 is independent and is not handled here.

All owned command processes have ended. At pause, the latest full-discovery command returned 1, browser regression returned 0 and lifecycle specialty returned 1. Process cleanup inspection found no live unittest runner, e2e runner/server, owned queue worker, DSH CLI, controlled executor, namespace or Playwright browser process. No additional test group was started after the pause instruction. No commit, push, publication, package or extra TUI thread was created.

#### Completed Local Evidence

Evidence logs are under `output/playwright/ticket04-evidence/` unless stated otherwise.

| Command / Boundary | Actual Outcome Before Pause | Evidence |
| --- | --- | --- |
| `npm run check` | Passed Python complexity, frontend/Node lint with zero warnings, Hooks and TypeScript | `check-post-harness.log` |
| `npm run verify` | Passed check, 9 Node contracts, production build and diff checks | `verify-final-02.log` |
| Standard regressions | 38 tests passed, 29.706 seconds | `regressions-final.log` |
| `npm run verify:dsh` | 34 tests passed, 717.594 test seconds; command 717.816 seconds | `dsh-final.log`; `output/playwright/dsh-contract-2026-10-21T11-17-58-556Z/results.json` |
| Integrated browser, including full native DSH scale | 16 scenarios passed, 1679.770 cumulative scenario seconds, browser errors 0 | `e2e-final-02.log`; `output/playwright/2026-10-21T11-15-43-943Z/results.json` |
| Browser exploration | 35 scenarios passed, 476.895 cumulative scenario seconds, browser errors 0 | `explore-final-02.log`; `output/playwright/2026-10-21T11-19-56-941Z/results.json` |
| Full backend discovery | 227 tests completed in 1439.060 seconds, **1 failure** in the full 50-case judge-resume specialty; not passed | `full-unittest-final.log` |
| Lifecycle specialty | 7 tests completed in 699.870 seconds, **1 failure** in the same judge-resume path; not passed | `lifecycle-final.log`; `lifecycle-specialty-final/` |
| New missing-judge-usage regression | Red: reproduces the audit `TypeError` below; no fix applied yet | `judge-usage-red.log` |

Native scale evidence uses the actual pinned Linux DSH, real Git and sandbox, with a synthetic imported dataset of **50 unique case IDs across two fixed local commit ranges**. It does not use the controlled executor and does not represent the company's official selection or actual model quality. Each mode separately created one ordinary full 50-row batch. All 50 answers, unique session IDs, unique workspaces, exact answer commit identities and score scope were checked; all staged workspaces were removed. Each batch had 100 actual loopback review HTTP requests and 150 original-protocol judge requests (3 rounds). The per-case 10-minute budget was retained, with 53 queue workflow steps and 690-second outer supervision for each review step, not a shared ten-minute batch budget.

| Mode | Batch | Observation | Samples | Peak CLI-Tree Processes / RSS | Heartbeat-Unready Samples |
| --- | --- | --- | --- | --- | --- |
| Baseline | `aacr-20261021112022074-d5dbe860` | 712.636 seconds | 632 | 4 / 248000 KiB | 0 |
| Skill | `aacr-20261021113219694-efb6a3bb` | 687.970 seconds | 571 | 4 / 248784 KiB | 0 |

Full samples, requests, ranges, session IDs and workspace paths are in `output/playwright/2026-10-21T11-15-43-943Z/dsh-50-lifecycle.json`; screenshots are `dsh-50-baseline.png` and `dsh-50-skill.png`. RSS is sampled only for the CLI process tree, not the entire host, browser, queue server or judge. These bounded tiny-repository observations are not production capacity or real-model performance certification.

The separate **controlled-executor** 50-row fault run actually submitted all 50 frozen ID/repository/base/head tuples. It retained 47 complete answers, 2 review failures and 1 real supervised timeout; an HTTP 503 review failure did not retry, malformed output did not become an empty answer, and subsequent cases ran. The timeout is induced by a 70-second loopback response delay against the configured one-minute case budget. Original judging first receives a 503 (10-second protocol retry), then a 400 (pause without retry); explicit judge resumption completed the three rounds but audit serialization then failed as described below. This is not a native DSH result. Its `boundary.json`, full observed HTTP payloads, raw execution/request records and process/workspace observations are under `lifecycle-specialty-final/`.

Controlled cancellation and actual worker `SIGKILL` probes observed the real sandbox executor and descendant PIDs, then verified no live descendants and no temporary workspace. Explicit queue cancellation and service interruption remained distinct; restart/acknowledgement did not replay review calls. Native browser cancellation and service-interruption/restart probes each used a complete frozen 50-row batch, stopped during the second case and preserved the first answer. Their bounded create-to-observation windows were 47625 ms and 47363 ms; raw process cleanup took 0.015 and 0.017 seconds. Exactly 3 review requests occurred per stopped batch (2 completed first-case requests plus the held second-case request), 6 total; recovery made no new requests. See `output/playwright/2026-10-21T11-19-56-941Z/dsh-recovery.json`.

Exploration also injected response loss after real local upload persistence (chunk recovery 5003 ms, completion recovery 4465 ms, one request each), delayed/lost responses, JSON/HTML 503s, malformed state/storage, expired uploads, worker interruption and local server restart. Its queue/navigation observation lasted 60 seconds with 84 iterations. These are local process/browser/network simulations, not power-loss, kernel-crash, real COS, remote-provider cancellation, inline mirror or production-image acceptance. Scenario counts do not imply absence of defects.

#### Current Failure And Remaining Implementation

The new full-50 fault run exposed an existing AACR audit bug in `backend/src/suites/aacr/evaluator.py::_save_audit`: the SDK can serialize unknown usage as `prompt_tokens=None`, `completion_tokens=None`, `total_tokens=None`; the current `sum(...get(key, 0))` then raises `TypeError: unsupported operand type(s) for +: 'int' and 'NoneType'`. Scores/round files and requests were retained, but the explicit resume job ended failed while writing its audit, so report, final scope and subsequent recovery assertions in this specialty were not completed.

No production fix has been made for this bug. `backend/tests/test_aacr_scoring_resume.py` now has a red public-pipeline regression `test_missing_judge_usage_is_unknown_not_zero_and_does_not_fail_scoring`, plus test-client usage overrides. This regression was added after the 227-test discovery process constructed its suite; the next fresh discovery should include 228 tests if no further test methods are added. Its expected semantics are: keep unknown usage unknown (not zero), retain truthful coverage, and do not change judge rounds, request timeout, retry delays or replay behavior.

Resume work must fix that audit aggregation without broadening historical complexity baselines or changing scoring protocols. If changing the suite's exact source identity requires updating a pinned compatibility revision, review that deliberately; do not rewrite frozen batch configs or relax identity checks merely to reuse the old runs. The completed native known-usage scale runs above precede this still-unimplemented audit fix. Re-run the affected full-50 lifecycle specialty, full discovery, static/build gates and appropriate native/browser regressions after the fix; verify zero-call recovery assertions that were not reached. Earlier intermediate logs, including `full-unittest-pre-local-route.log`, are not successful final acceptance.

The last independently reviewed snapshot is `/tmp/dsh-ticket04-final-review-8RpghU/tree`, archive SHA-256 `032310a6535a4b38aacf8906753e577f4314c3ab9934939e9d87276911bf4adc`; both axes had 0 open findings at that snapshot after the two P1 fixes and local HTTP recovery fix. The subsequently discovered unknown-usage audit defect and new red regression have not received a fixing/re-review cycle. Preserve the exact original pre-04 baseline above for the eventual two-axis final review; do not compare only against HEAD.

### User Resume: 2026-10-22

The user explicitly resumed this same implementation session. The pause above remains a historical record, not a current prohibition. Only ticket 04 is being handled; no commit, publication, package or ticket 05 work is authorized. Fresh evidence is under `output/playwright/ticket04-resume-evidence/`; no old process handle was reused.

The host restart removed the old `/tmp` snapshots. Before any resumed source changes, the workspace was archived as `pre-resume-workspace.tar`, SHA-256 `695d215e63e8a7d47ce1eeda685f5a492c6dd82f6bbd75dff6a31377c139d538`. With parent approval, the Standards reviewer reconstructed the original pre04 **file contents**, never from HEAD: 33 successful literal patches in 31 exec calls / 61 file operations were strictly reversed, then fully replayed forward. The resulting 468-file baseline replay matches all 475 endpoint file bytes. All 46 retained original full-file reads match, as do all 13 DSH source hashes in the latest pre04 native configuration. The reconstructed baseline content manifest SHA-256 is `eafe47c9940ed5e6086a2dddff39e7fc748e8c5b3567d235243e7d1bca1156ec`. Scripts, original structured events, parsed patches, manifests and checks are retained in `baseline-reconstruction/`; review baseline is its `baseline-tree/`. This is content evidence only: original archive byte identity, ordering, ownership, permissions and times cannot be certified, and unrecorded external edits cannot be ruled out by replay alone.

`judge-usage-red.log` freshly reproduces the null-usage audit error. The fix aggregates only exact nonnegative integer fields, retains actual zero and partial known values, omits unknown/invalid fields, and records complete-request coverage. It changes neither judge rounds, 180-second request timeout, 10/30-second retries nor replay. `scoring-green-01.log` passes all 17 scoring-resume tests, including all-null and mixed/zero/invalid usage. The registry updates only the pinned `current` evaluator hash to `0c498a21f5c354af1b39c58fc6fe117968def8a471a51073c017350e1b0440ce`; the historical `previous` set is unchanged and direct pre-fix `ac4dd...` frozen identities remain explicitly rejected. Both narrow review axes report 0 findings.

Final Spec review confirmed a separate P2 against the whole-batch freeze requirement: a process-local scorer identity mismatch after normal init/check previously produced ordinary `ValueError`, attempted all 50 review stages and eval, and lacked `dsh-system-error.json` (40.537-second authenticated full50 queue/real-worker probe; zero HTTP calls; source files unchanged). A dedicated regression injects this mismatch only in the test-only controlled CLI wrapper. `suite-identity-red.log` reproduces the absent marker (21.057 seconds); `suite-identity-green.log` passes (12.038 seconds), confirming the first review fails closed, exactly one review attempt, no eval/model calls, no answer and the frozen50 retained. The production resolver maps its validation `ValueError` to `IsolationError` only for DSH; other reviewers retain their existing error semantics. `check-02.log` passes after this fix. Fresh long gates and final whole04 reviews are pending at this entry; earlier passing runs do not certify the resumed endpoint.

#### Historical Pause Commands And Handles

At the 2026-10-21 pause there was no running command or agent handle to resume. Completed handles were: full discovery `85237` exited 1; integrated e2e `50297` exited 0; lifecycle specialty `66713` exited 1. The following commands were the saved next steps, conditional on explicit user resumption, which was given on 2026-10-22. These old handles must not be reused; fresh outcomes are recorded separately below.

```bash
env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 PYTHONUTF8=1 PYTHONUNBUFFERED=1 \
  BENCH_PROJECT_ROOT="$PWD" BENCH_RUN_STORAGE_MODE=local BENCH_CASE_STORAGE_MODE=local \
  BENCH_MODEL_STORAGE_MODE=local PYTHONPATH="$PWD/backend/src:$PWD/backend/tests:$PWD" \
  .venv/bin/python -m unittest -v \
  test_aacr_scoring_resume.AACRScoringResumeTests.test_missing_judge_usage_is_unknown_not_zero_and_does_not_fail_scoring

# After implementation, use new log/evidence paths; preserve the failed logs above.
env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 PYTHONUTF8=1 PYTHONUNBUFFERED=1 \
  BENCH_PROJECT_ROOT="$PWD" BENCH_RUN_STORAGE_MODE=local BENCH_CASE_STORAGE_MODE=local \
  BENCH_MODEL_STORAGE_MODE=local PYTHONPATH="$PWD/backend/src:$PWD/backend/tests:$PWD" \
  .venv/bin/python -m unittest -v test_dsh_lifecycle

env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 PYTHONUTF8=1 PYTHONUNBUFFERED=1 \
  BENCH_PROJECT_ROOT="$PWD" BENCH_RUN_STORAGE_MODE=local BENCH_CASE_STORAGE_MODE=local \
  BENCH_MODEL_STORAGE_MODE=local PYTHONPATH="$PWD/backend/src:$PWD/backend/tests:$PWD" \
  .venv/bin/python -m unittest discover -s backend/tests -v

npm run check
npm run verify
npm run verify:dsh
E2E_DSH=1 E2E_DSH_SCALE=1 E2E_SIMULATED_LCR=1 npm run e2e
E2E_DSH=1 E2E_SIMULATED_LCR=1 npm run e2e:explore
```

The company official 50-row list remains for inline human acceptance even after all local gates pass. Confirm its exact frozen identity, actual submitted commit ranges, answer count and score/exclusion scope through the ordinary creation/queue/report workflow. Do not replace it with the synthetic fixture, mark done, access real models/COS automatically, or implement ticket 05 here.

### Final Local Handoff: 2026-10-22

All resumed local gates below exited 0. The source endpoint reviewed by both agents is `output/playwright/ticket04-resume-evidence/review-endpoint-tree/`, archive SHA-256 `ba740f890a7d0ef7012bf32ad10edef39e0d3b97981e265e5fe6de6c357591a3`. Both Standards and Spec reviewed the complete ticket04 content delta against the reconstructed pre04 baseline, not HEAD, and report **0 open findings**. Spec closed the new P2 frozen-scorer failure; prior P1 fixes, local HTTP recovery and usage audit were retained. The only later edits are ticket/progress handoff documentation; runtime and test sources stayed byte-identical through the final native runs.

Logs in this table are under `output/playwright/ticket04-resume-evidence/`.

| Command / Boundary | Fresh Final Outcome | Evidence |
| --- | --- | --- |
| `npm run check` | Passed actual Python complexity, zero-warning frontend/Node lint, Hooks and TypeScript | `check-02.log` |
| `npm run verify` | Passed check, 9 Node contracts, production build and diff checks | `verify-final-02.log` |
| Standard regressions | 38 tests, 29.710 seconds, passed | `regressions-final-02.log` |
| Full backend discovery | 230 tests, 1502.205 seconds, passed | `full-unittest-final-01.log`; `full-discovery/` |
| Lifecycle specialty | 8 tests, 758.985 seconds, passed | `lifecycle-final-01.log`; `lifecycle-specialty/` |
| `npm run verify:dsh` | 34 tests, 747.827 unittest seconds, passed; wrapper wall-clock counter 722.311 seconds is separately retained, not equated to the test timer | `dsh-final-01.log`; `output/playwright/dsh-contract-2026-10-22T03-06-04-094Z/results.json` |
| `E2E_DSH=1 E2E_DSH_SCALE=1 E2E_SIMULATED_LCR=1 npm run e2e` | 16 scenarios passed, 1646.083 cumulative scenario seconds; 0 browser errors | `e2e-final-01.log`; `output/playwright/2026-10-22T03-06-01-757Z/results.json` |
| `E2E_DSH=1 E2E_SIMULATED_LCR=1 npm run e2e:explore` | 35 scenarios passed, 461.689 cumulative scenario seconds; 0 browser errors | `explore-final-01.log`; `output/playwright/2026-10-22T03-06-05-275Z/results.json` |

#### Native Full50

The actual pinned Linux DSH separately completed baseline50 and Skill50 after both resumed source fixes, with all 50 unique frozen case IDs, answer commit identities and 50-case score scope validated in each batch. Each had 100 loopback review HTTP requests and 150 original-protocol judge requests, 50 unique sessions and 50 unique temporary workspaces, all removed. Serial case execution retained the per-case 10-minute limit, 53 workflow steps and 690-second review supervision. These are synthetic IDs across two fixed tiny local Git ranges, not the company's official selection and not evidence of real-model quality or production capacity.

| Mode | Batch | Observation | Samples | Peak CLI-Tree Processes / RSS | Heartbeat-Unready |
| --- | --- | --- | --- | --- | --- |
| Baseline | `aacr-20261022031035234-6b205776` | 699.087 seconds | 646 | 4 / 248536 KiB | 0 |
| Skill | `aacr-20261022032219828-6adaf9b6` | 671.673 seconds | 574 | 4 / 249092 KiB | 0 |

Full samples, ranges, session/workspace identities and exact counts: `output/playwright/2026-10-22T03-06-01-757Z/dsh-50-lifecycle.json`. Screenshots: `dsh-50-baseline.png`, `dsh-50-skill.png`. RSS covers only the sampled CLI process tree, not the entire host, browser, server or judge.

#### Faults, Recovery And Boundaries

The separate controlled-executor full50 test now reaches every post-resume assertion: exactly 50 submitted ID/repo/base/head tuples, 47 complete answers, 2 failed reviews and 1 actual supervised timeout; exact score scope 47/50 with the three incomplete cases excluded, not scored zero. Faults are a single review HTTP503 (no retry), malformed JSON, and a 70-second loopback response delay against a one-minute deadline. The timeout record is 59.975 supervised seconds with 0.014-second confirmed cleanup. Original judging injects HTTP503 (at least 9.5 seconds before its fixed 10-second retry), then HTTP400 (pause); explicit eval resumes 3 rounds using 143 total requests (141 successful judgments plus 2 faults), unchanged 180-second request timeout and 10/30-second retry delays. Unknown review usage stays `{}` with coverage0/1; final judge audit completes with `{}` and coverage0/143. Existing and reconstructed complete answers remain byte-identical through recovery, with zero extra requests; failed/timeout records cannot recover and retry is rejected. Artifacts: `lifecycle-specialty/lifecycle-dsh-lifecycle-rq9vmq0p/`.

Controlled queue cancellation and actual worker SIGKILL each retain full50, only 2 review requests, observed executor/descendant PIDs, complete cleanup, no live observed descendants and no remaining workspace; restart/ack does not replay. Evidence is in the two `controlled-stopped` fixture directories under `lifecycle-specialty/`, including before/after process observations. Cleanup-confirmation denial stops after one request; frozen scorer identity substitution stops before any request, at the first review, without eval. The controlled executor emits test events and is explicitly **not native DSH**; the real queue, CLI process, sandbox, supervision and HTTP are exercised.

Fresh native browser stop and service-interruption/restart probes each freeze all50, complete the first case and stop while the second request is held. Observation windows are 48330 ms and 45575 ms; raw cleanup is 0.015 and 0.014 seconds. There are exactly 3 review requests per stopped batch, 6 cumulative, and no additional call after restart or successful-answer recovery. See `output/playwright/2026-10-22T03-06-05-275Z/dsh-recovery.json`. Upload-response loss is injected after real local persistence: chunk recovery4571 ms / completion2630 ms, one affected request each (`skill-recovery.json`). Queue/navigation soak lasts60 seconds /85 iterations (`soak.json`). Other exploration injects delayed/lost responses, malformed JSON/state/storage, JSON/HTML503, expired uploads and local process restarts. Scenario totals do not establish absence of defects.

All tests use loopback deterministic models/judges and local storage, never real model or COS. Local browser and static/build validation do not certify inline npm/Python mirrors, production images, remote-provider cancellation, power loss or kernel crashes. No browser regression was added to Dockerfile. No historical complexity baseline, threshold or frozen batch configuration was relaxed. No delivery archive, commit, push, publication, new implementation thread or ticket05 work was performed; temporary source ZIPs inside existing unit tests are fixture output only. All validation command handles have exited; the final process observation and workspace checks retain cleanup evidence.

#### Human Acceptance Still Required

Status is **ready-for-human**, not done. Inside the company, select the actual imported official-positive50 dataset and its Git resources through the ordinary UI/API, create one baseline batch and one selected-Skill batch, and verify the frozen list digest and all 50 ID/repository/base/head tuples, actual submitted ranges, answer count, score/exclusion scope, serial budgets, isolation/session/workspace cleanup and stop/restart/recovery semantics. This explicit real-list acceptance is the remaining gap. Do not infer it from synthetic50 results, search for internal resources locally again or automatically run paid model/COS regressions.
