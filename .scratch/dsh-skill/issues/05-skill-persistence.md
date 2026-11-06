# 05: Skill 资产与测评证据跨容器恢复

**What to build:** 启用现有 COS 存储的工作台确认 Skill 导入成功后，容器重建仍能恢复并校验相同版本，并在原批次中查看答卷和实际加载证据。用户能分辨持久化失败和异步备份风险，而不是把当前容器里的文件误认为已经可靠保存。

**Blocked by:** 03: 指定 Skill 版本完成常规测评。03 已包含对 01 和 02 的依赖；本票不依赖 04，可与其独立推进。

**Status:** done

- [x] 沿用现有资源与运行材料两条持久化链路及各自模式，不新增独立备份系统；本地模式仍可使用，COS 模式不退化为本地成功。
- [x] COS 模式下原 ZIP、完整内容身份、文件清单及版本记录确认保存后才显示导入成功并可供新批次选择；保存失败时版本不可选，重新导入同一内容不会破坏既有版本。
- [x] 上传确认检查实际远端对象大小和摘要元数据，下载恢复时计算实际内容摘要；不将元数据确认描述为已完整下载回读验证，也不承诺所有缓存文件均已事务回滚。
- [x] 服务启动前恢复并校验 Skill 资产，原包和资源可追溯；缺失、损坏或不可变内容冲突明确阻断，不静默覆盖或用其他版本替代。
- [x] 恢复后的版本仍可在现有新建测评中选用，历史批次仍绑定原不可变身份；不自动重跑历史检视来验证恢复结果。
- [x] 批次沿用现有可备份的普通运行记录范围，脱敏事件、完整加载证明、配置身份、答卷及报告被明确纳入；临时 HOME 和工作区清理后不能失去唯一证据。
- [x] 容器重建后可通过原有页面和报告下载查看已成功备份的批次材料，恢复及查看不产生模型调用，不改变所选评分协议的恢复规则。
- [x] 运行材料沿用异步同步、失败警示与正常退出最终同步；同步失败对操作者可见，不承诺强杀、宿主故障或网络失败时最后瞬间数据零丢失。
- [x] 使用本地 COS 替身、临时存储及容器或服务重启验证完整导入与恢复路径，覆盖对象保存失败、记录保存失败、内容冲突、恢复失败、临时会话清理和原始证据可读；不访问真实 COS 或真实模型。
- [x] 完成 check、verify、相关存储契约测试、本地 e2e 和恢复 e2e:explore，记录注入方式、持续时间及模拟边界；本地模拟、Linux DSH 联调和行内镜像验收分别披露，不将未验证项标为 done。

## Comments

### Reopened: Conditional-Write Protocol Audit

Root acceptance reopened ticket05: the filesystem COS substitute assumed S3 `IfNoneMatch` support but did not prove the real boto3 HTTP request or the configured service's prevention of concurrent overwrite. Earlier passing results are historical, not current completion evidence. The repository config uses a generic S3-compatible endpoint; it does not establish Tencent-specific header support or bucket versioning constraints. Do not infer a provider or silently fall back to ordinary PUT. An exact pre-repair source snapshot is retained in `output/playwright/ticket05-evidence/protocol-audit/pre-repair/`; original pre05 remains unchanged. Real COS/model access remains prohibited. Completion is withdrawn pending protocol diagnosis, fail-closed correction, affected gates and independent two-axis re-review.

### Ticket05 Implementation And Review

- Only ticket05 is implemented in this independent session. Existing uncommitted 01-04 work is preserved; 04 company-only 50-case acceptance is not a dependency. No real model/COS, commit, push, publish or delivery package.
- Exact pre05 source snapshot was captured before edits, including tracked and nonignored untracked working files, not HEAD: `output/playwright/ticket05-evidence/pre05/workspace.tar.gz`, SHA-256 `dab4604b75fdf0b198b256cd40006997eea2282c4829d0977cf822fd14136d63`. Original extracted tree retained; excluded ignored runtime/dependency/secret scope is documented in `baseline.md`. Early logs use the original `output/skill-persistence-05` path before intact relocation under ignored browser evidence.
- Existing resource COS connection/prefix now stores Skill original ZIPs, content manifests, immutable version records and durable confirmation records. Local mode remains local. Conditional immutable writes plus HEAD size/digest metadata confirmation precede selection; download recovery independently hashes bytes and checks extracted identity. Complete saved records with lost confirmation can be verified and finalized on retry, whereas object-only failed-import orphans are not selectable.
- Startup restores/validates originals (including differently compressed originals), all content and local immutable conflicts. Durable confirmed identities plus restored historical frozen configurations and queue references prevent missing assets from becoming an empty successful listing on a fresh root. No fallback version or model invocation.
- Ordinary `runs/` backup already includes DSH redacted raw events/sessions, full model inputs/loading proof, frozen conditions, Skill snapshot, answers, judge records and reports; the new native test verifies these survive temporary HOME/workspace cleanup and run-data restoration. Existing run-data asynchronous sync, visible errors and graceful final sync remain unchanged. UI/docs now explicitly disclose the unsynced loss window.
- Public API red/green evidence includes COS rejection (`red-import-only.log` / `green-import-02.log`), concurrent object overwrite (`red-concurrent-object.log` / `green-concurrent-object.log`), repacked-original content misbinding (`red-repacked-binding.log` / storage specialty), fresh-root missing record (`red-fresh-record-loss.log` / `green-fresh-record-loss.log`), and restored queue reference after full Skill-prefix loss (`red-queue-reference-loss.log` / `green-queue-reference-loss.log`).
- Independent Standards review: 0 findings. First Spec review found P1 fresh-root missing-record startup acceptance; fixed with red/green, then independently re-reviewed: 0 open findings. Immutable review02 archive SHA-256 `192374d717054b2d8a6dacc2b7ac33b16830c5219aaf79bbfa1f8733464139f4`, compared to exact pre05, not HEAD. Both reviewers also cleared the later test-only `LocalCOS.delete_object` fixture addendum. Reports in `review01/` and `review02/`.
- Intermediate browser failures are retained, not counted as passes: first replacement fixture removed live queue before stopping the server; second recovery request omitted the existing required profile; first COS exploration setup lacked fake-S3 delete support. These are test-harness fixes only; browser recovery now uses the ordinary UI and replacement occurs only after service exit.
- Final full/backend, DSH and integrated browser gates are running at this entry. No completion claim until their actual terminal outcomes are recorded below.

### Final Boundary Audit

- A public local-to-COS promotion probe found that existing alternate original ZIPs were not uploaded with the primary version. `red-local-cos-originals.log` reproduces loss after replacement; the small fix uploads all validated alternates before canonical version confirmation. `green-local-cos-originals.log` passes. The Spec reviewer also independently injected ZIP, record and commit promotion write failures, then retried and replaced storage, preserving both originals.
- A second Spec P1 was reproduced with a valid 8,206,287-byte ZIP containing 1,800 long shared-path files: import succeeded but its 4,221,972-byte record exceeded the 4 MiB recovery reader bound. Publication now checks the encoded envelope against the same shared bound before any version object/record/commit is saved; archive changes during saving also block publication. Public `red-record-bound.log` and `green-record-bound.log` prove rejection, nonselection, no published remote version, and a subsequent valid import.
- Final frozen business endpoint: `review04/workspace.tar.gz`, SHA-256 `cffda2beb82cd2ea024b44f758cc413e44322ba73709e589cd9712886f59ecb4`. Standards: 0 findings; Spec: 0 open findings after closing both P1s, with independent focused test rechecks. The original exact pre05 baseline remains unchanged. Reports for all rounds are retained.
- `full-unittest-01.log` (240-test discovery) and `full-unittest-final-02.log` (241-test discovery) were deliberately terminated with exit 143 after discovering the later boundary defects; they are not full-suite passes. Owned old worker/process groups were observed gone before the fresh final 242-test discovery.
- First `verify:dsh` reached its existing subprocess deadline under concurrent validation load and returned 1 with `ETIMEDOUT`; `dsh-final-01.log` and the failed results JSON are retained. No timeout threshold or test expectation was relaxed. A successful fresh final DSH command is still required before closing this ticket.

### Final Verification (2026-10-22)

All logs below are under `output/playwright/ticket05-evidence/`. The final business source is unchanged since review04; subsequent tracker edits only record completion.

| Gate | Actual Result | Evidence |
| --- | --- | --- |
| `npm run check` | Passed Python complexity, frontend/Node zero-warning lint, Hooks and TypeScript | `check-final-02.log` |
| `npm run verify` | Passed check, 9 Node contracts, production build and diff checks | `verify-final-03.log` |
| Full backend discovery | **242 tests passed**, 1530.883 unittest seconds, process exit 0 | `full-unittest-final-03.log`; `full-discovery-final-03/` |
| Storage/resource/long-task contracts | **42 tests passed**, 77.240 seconds | `storage-contracts-final-03.log`; `storage-native-final-03/` |
| Standard regressions | **38 tests passed**, 29.682 seconds | `regressions-final-01.log` |
| `npm run verify:dsh` | **34 tests passed**, 703.932 unittest seconds, exit 0 | `dsh-final-02.log`; `output/playwright/dsh-contract-2026-10-22T04-29-34-860Z/` |
| Integrated `npm run e2e` | **16 scenarios passed**, no browser errors, exit 0 | `e2e-final-01.log`; `output/playwright/2026-10-22T03-58-43-310Z/` |
| Integrated `npm run e2e:explore` | **35 scenarios passed**, no browser errors, exit 0 | `explore-final-01.log`; `output/playwright/2026-10-22T03-58-44-476Z/` |
| Final COS replacement exploration | **1 COS workflow plus 4 shared setup scenarios passed**, no browser errors, exit 0 | `explore-cos-final-03.log`; `output/playwright/2026-10-22T04-12-33-583Z/` |

The DSH wrapper separately reports 677.989 seconds; this counter and the unittest timer are retained as reported, not equated or normalized. The failed initial wrapper deadline remains disclosed above.

Commands used the isolated local environment from ticket04: `env -i` with PATH/HOME/LANG, `PYTHONUTF8=1`, `PYTHONUNBUFFERED=1`, `BENCH_PROJECT_ROOT=$PWD`, case/run/model storage modes `local`, and `PYTHONPATH=$PWD/backend/src:$PWD/backend/tests:$PWD`; storage tests explicitly switch their temporary resource/run modes to COS against `LocalCOS`, never the real endpoint. Full discovery used `.venv/bin/python -m unittest discover -s backend/tests -v`; storage specialty used `-m unittest -v test_skill_persistence test_skill_assets test_long_tasks test_case_deletion`. Browser commands used `E2E_DSH=1 E2E_SIMULATED_LCR=1`; full e2e also set `E2E_DSH_SCALE=1`; COS specialty set `E2E_SKILL_COS=1` and `--only=skill-cos-persistence`.

### Fault Windows And Acceptance Boundaries

- Final COS browser workflow lasted **105.959 seconds**. Resource `record.json` PUT denial lasted **3.650 seconds** and produced a failed, unselectable import; a forced run-queue-manifest PUT denial and visible error lasted **0.441 seconds**, then explicit successful sync preceded replacement. These are bounded injected windows, not a long-network-outage durability guarantee. `skill-persistence.json` retains exact observations.
- After stopping the old service, the test removed resource, run, queue and case/repository local storage and restarted through the real resource/run restore functions using filesystem S3 objects. Both original and Martian historical reports/answers/full raw proof remained readable through ordinary APIs/UI/download; new creation could select the restored version. Actual loopback model/judge request count was **8 before replacement and 8 after restore, viewing, explicit recovery and save-only creation**. No historical review was rerun.
- Storage contracts additionally inject object/record/confirmation failures, concurrent immutable conflicts, actual downloaded corruption, missing objects/records, local immutable conflicts, wrong repack content, whole-prefix loss with restored queue references, oversized metadata and local-to-COS promotion. Native DSH evidence was compared after temporary HOME/workspace removal, ordinary run backup, replacement and zero-call parsing recovery; normal synchronizer `stop()` final sync is covered. Failed caches/objects are not claimed to be transactionally rolled back.
- Full browser regression separately used the **actual pinned Linux DSH and real sandbox** with synthetic imported 50-case Git fixtures, not company resources. Baseline and Skill batches each completed/scored **50/50**, with 50 unique sessions/workspaces, confirmed workspace cleanup, 100 loopback review requests and 150 original-protocol judge requests (3 rounds). Per-case budgets stayed 10 minutes, with 53 queue steps; no shared ten-minute batch budget. Evidence in `dsh-50-lifecycle.json`.
- Full exploration included a **60-second** navigation/queue soak (45 iterations), lost chunk/completion responses after persistence (**6.248 / 2.857 seconds**, one request each), cancellation/restart and recovery. Native DSH stop windows were **58.716 / 54.059 seconds**, cleanup complete, with no extra calls after the respective 3/6 request observations. These bounded fixtures do not prove long-term or fault-free production operation.
- **Not verified:** real COS/provider behavior (including conditional-write support and remote cancellation), real model quality/billing, host power loss/kernel failures, inline npm/Python mirrors, company actual official50 resources, Docker/production image acceptance or a real container rebuild. The tested boundary is service restart plus deletion/recovery of confirmed data using a local COS substitute. No browser tests were added to Dockerfile. Ticket04 remains ready-for-human; its internal-list acceptance is not claimed by ticket05.
- No lint/type/build/test requirement was bypassed, no historical complexity baseline or budget threshold expanded, and no package, commit, push or publication was made. Temporary ZIPs emitted by existing backend tests are isolated fixture artifacts, not delivery packages. All owned validation commands and test services/workers exited; final process observations and source-scope checks are retained. This session stops after ticket05.

### Conditional-Write Correction And Independent Review

This section supersedes the earlier LocalCOS-only conditional-write acceptance, not the preserved historical results. Correction evidence lives under `output/playwright/ticket05-evidence/protocol-audit/`.

- The repository uses a generic S3-compatible endpoint and the existing boto3 connection. No Tencent-specific header, provider switch, bucket-versioning restriction, dependency override or deployment configuration change was introduced. Official source distinctions and links are retained in `protocol-sources.md`.
- Before saving any Skill asset, the same configured client/bucket performs isolated UUID-key probes under the Skill prefix: serial duplicate creation must reject, concurrent writers must yield exactly one winner, and GET must preserve that winner's actual bytes, length and digest. Creation requires HTTP 200; conflict requires HTTP 412 plus `PreconditionFailed`. Ignored headers, rejected headers, modeled non-atomic writes, unexpected HTTP 202, retention failure or cleanup failure block saving/restoring without ordinary PUT fallback. Probe keys are separate from asset keys. Successful capability checks are cached per storage instance. Startup without any Skill records does not probe or block existing non-Skill COS workflows.
- Real boto3/botocore **1.43.116** clients use the ordinary client factory against a loopback HTTP S3 service, not a mocked SDK. Public upload/startup tests cover compliant, ignored, rejected, racy and HTTP-202 services, plus forced concurrent record misses from independent clients. The compliant server records one HTTP 200 and one HTTP 412, and the winner's body remains unchanged. Sanitized traces preserve the transmitted `If-None-Match: *` and its SigV4 signed-header name, never credentials/signatures/tokens. Failure-mode tests assert zero asset PUTs and no selectable failed version.
- `red-wire-contract.log` proves the old implementation incorrectly published on an ignoring service despite the SDK sending the header. Spec review additionally found P2 acceptance of HTTP 202; `red-202.log` reproduces publication before the exact-status correction. Final green `http-final-04.log` passes **6 tests in 11.173 seconds**, with eight sanitized server traces in `http-final-evidence/`.
- Exact pre-repair snapshot SHA-256 remains `a21f7dffe04d209576fb335e222dc3c6946f07fb4d782822f4eda1afbdaaca32`; original exact pre05 is unchanged. Corrective review02 freezes the final business source at SHA-256 `ca818190cb8898244e0ed0476840e39d3752f8165e4c84f778081e8a92114666`. Standards: **0 findings**. Spec: **0 open findings**, P2 closed with independent real-SDK negative and positive test rechecks. Reports and exact-baseline diff are retained in `review02/`; subsequent changes only record tracker evidence.
- A finite runtime probe is compatibility evidence at that time, not proof of arbitrary-server linearizability, key-specific routing or future configuration stability. Stable atomic create-only S3 semantics remain a deployment prerequisite. Privileged external unconditional writes/deletes are outside this guarantee. Probe cleanup deletes only its own current keys; versioned buckets may retain historical probe versions/delete markers. No real internal endpoint/provider, real COS, real models or physical version purge was verified or claimed.
- Fresh COS browser replacement took **105.795 seconds**: resource record PUT denial lasted **3.662 seconds**, run-backup PUT denial **0.402 seconds**; versions failed visibly/unselectably, explicit successful sync preceded service replacement, and model/judge calls stayed **8 before / 8 after** restore, viewing and save-only creation. Evidence: `explore-cos-final-02.log`, `output/playwright/2026-10-22T04-58-44-576Z/skill-persistence.json`. This remains a filesystem substitute plus service restart, not actual COS or container-image acceptance.
- Fresh full exploration passed **35 scenarios**, including a **60-second / 101-iteration** queue/navigation soak, lost chunk/complete responses after local persistence (**4.478 / 4.044 seconds**, one request each), native DSH cancellation/restart with complete cleanup (**44.055 / 46.843 seconds**, calls fixed at **3 / 6**) and zero-call recovery. Evidence: `explore-final-03.log`, `output/playwright/2026-10-22T05-04-16-049Z/`. These bounded fault injections and counts do not imply production is fault-free.

### Correction Verification And Closeout (2026-10-22)

All following results were rerun against the corrective review02 business source. Logs are under `output/playwright/ticket05-evidence/protocol-audit/`.

| Gate | Actual Result | Evidence |
| --- | --- | --- |
| `npm run check` | Passed complexity, zero-warning lint, Hooks and TypeScript | `check-final-03.log` |
| `npm run verify` | Passed check, 9 Node contracts, production build and diff checks | `verify-01.log` |
| Full backend discovery | **248 tests passed**, 1523.324 unittest seconds, exit 0 | `full-unittest-01.log`; `full-native/`; `full-http/` |
| Storage/resource/long-task contracts | **48 tests passed**, 86.070 seconds, exit 0 | `storage-contracts-01.log` |
| Standard regressions | **38 tests passed**, 29.812 seconds, exit 0 | `regressions-01.log` |
| Real boto3 HTTP specialty | **6 tests passed**, 11.173 seconds, exit 0 | `http-final-04.log`; `http-final-evidence/` |
| Integrated `npm run e2e` | **15 scenarios passed**, no browser errors, exit 0 | `e2e-final-01.log`; `output/playwright/2026-10-22T04-58-45-750Z/` |
| Integrated `npm run e2e:explore` | **35 scenarios passed**, no browser errors, exit 0 | `explore-final-03.log`; `output/playwright/2026-10-22T05-04-16-049Z/` |
| COS replacement exploration | **1 COS workflow plus 4 setup scenarios passed**, no browser errors, exit 0 | `explore-cos-final-02.log`; `output/playwright/2026-10-22T04-58-44-576Z/` |

Full discovery reused the isolated `env -i` local-storage environment documented above and includes the actual pinned Linux DSH tests, including the Skill evidence backup/restore contract. HTTP tests explicitly use temporary loopback endpoint configuration and ordinary boto3 clients; other storage tests use `LocalCOS`. Browser regression/exploration used `E2E_DSH=1 E2E_SIMULATED_LCR=1`; COS specialty additionally used `E2E_SKILL_COS=1` and `--only=skill-cos-persistence`. This fresh 15-scenario regression did **not** set `E2E_DSH_SCALE`; the earlier 16-scenario scale pass and standalone `verify:dsh` 34-test wrapper remain historical results, not newly rerun commands. No DSH production source changed during protocol correction.

Ticket05 is closed after correction, full discovery and both independent reviews; ticket04 remains ready-for-human and is not a dependency. Original pre05 and pre-repair snapshots remain intact. No real COS/model, internal mirror, production image, real container rebuild or company official50 acceptance is implied. No delivery package, commit, push or publication was made, and existing 01-04 changes were preserved.
