# 06: DSH 离线运行依赖与行内镜像接入

**Status:** ready-for-human

**What to build:** 用户于 2026-10-22 确认补齐行内部署。流水线继续使用 `devops build --build.command="echo container_source_ready"`，Dockerfile 继续为 `backend/Dockerfile`，构建上下文为仓库根目录。不假设行内 npm 或系统软件源已经提供 DSH、Bubblewrap。把离线制品准备、镜像安装、构建检查和实际运行容器的隔离预检明确接通，而不是依赖本机安装结果。

**Blocked by:** 01、03 的本地执行接入已完成；不依赖 04 的公司真实案例清单。

## Acceptance Criteria

- [ ] 沿用固定 DSH 0.2.1-rc.2 和传递依赖锁，不改版本、不重复添加 overrides、不用模拟执行器。提供可重复的离线制品准备及校验路径，实际证明目标安装步骤不依赖外网或行内已有 DSH；仅复制主包不算完成。
- [ ] 明确离线制品如何进入当前真实流水线的构建上下文、文件清单和内容身份，不能只把缓存留在被忽略的本机 output 目录，再让 Dockerfile 引用缺失文件。不自动提交二进制制品或源码。
- [ ] Bubblewrap 及系统依赖必须匹配目标 C86/Kylin10 基础环境。核对实际 ABI、工具与包来源；不能把本机 Ubuntu 二进制复制后称作行内可用。不可取得目标镜像或匹配依赖时说明具体阻塞，保留准备与验收步骤。
- [ ] 镜像构建显式安装/验证约定的运行依赖；同步维护 `.dockerignore`，排除本机依赖、缓存、秘密与测试材料，按需纳入受控离线制品。保持流水线命令、Dockerfile 路径与既有前端、Python、LCR 工作流；未启用或未就绪的能力不能被静默宣称可用。
- [ ] 构建检查与运行时检查分开：实际平台容器中执行隔离能力预检，不调用模型/COS，不因 Dockerfile 的 USER root 或构建成功就声称 namespace 可用。不能放宽权限、要求特权容器、去掉隔离或退化裸跑来获得通过。
- [ ] 缺失、损坏、错版本、错误架构/依赖和隔离不可用均有明确失败反馈；已有非 DSH 流程不被无关检查意外破坏。目标镜像、行内镜像源和运行平台无法访问的验证不得伪报通过。
- [x] 测试优先覆盖真实安装/校验命令和预检的可观察行为，辅以本地确定性夹具；不得以 Dockerfile 字符串存在或语法转换代替真实安装、项目 lint、类型、构建、测试及运行验证。浏览器回归不加入生产 Dockerfile。
- [x] 执行 check、verify、受影响专项与全量回归；若页面/队列/恢复行为变更，执行本地 e2e，异常恢复执行 e2e:explore。禁止真实模型/COS 自动回归。交付 ZIP 只能走 `npm run package:changes`；普通测试快照或离线制品须明确区分于交付包。
- [x] 按 implement skill 做 TDD 和独立 Standards/Spec 双轴评审，使用持久保存的精确本票前工作区快照，不用 HEAD 代替既有未提交改动。完成后记录真实证据和剩余行内验收，未通过项不标 done。

## Comments

仅此部署补齐工单使用新的独立实现 session。用户未明确授权本地提交，项目仍遵守不自动提交、推送或发布。保留 01-05 及历史修复所有未提交改动；04 仍为 ready-for-human，不因为新增本票而改写其验收结果。

### Implementation And Review Checkpoint (2026-10-22)

Session `01a1626a-4197-73a3-afaa-41d89dadfef5`, only ticket06. Implementation is present; final backend/browser gates are still running at this checkpoint. **Not done.** No commit, push, publish or delivery ZIP.

- Exact pre06 source worktree (all tracked and non-ignored untracked files, preserving file metadata) is `output/playwright/ticket06/pre06/worktree.tar`, SHA256 `c7a84d2d1d9d07c461c4fbcd0c28e169c4bfa65497b4a9805a2eb354848c6e10`; status and binary tracked diff are alongside. Existing ignored host installations were not modified or mistaken for portable deployment artifacts. Existing 01-05 source content is preserved; HEAD was not used as this ticket's baseline.
- Public CLI boundaries were confirmed by the user: prepare/verify/stage/install commands and build/runtime preflight commands. Red/green logs cover missing inputs (`red-missing`/`green-missing`), corruption/lock binding (`red-integrity`/`green-integrity`), system host/preflight refusal (`red-preflight-system`/`green-preflight-system`), and disabled-image/runtime-start behavior (`red-container-gating`/`green-container-gating`), all under `output/playwright/ticket06/`.
- `prepare-npm` really fetched the fixed lock into a new cache (`green-prepare-02.log`, 60.152 s). Initial preparation failed on npm's double-loading of `/dev/null` as global/user config; the corrected command uses distinct config paths, without changing dependency versions or overrides. The explicit preparation test is networked npm-only, not a routine model/COS regression.
- Actual network-namespace installation used `bwrap --unshare-net` and `npm ci --offline --ignore-scripts --include=optional --engine-strict`: **615 locked packages**, DSH **0.2.1-rc.2**, real ripgrep **15.0.0**, Node **25.8.2**. Removing the optional grep payload caused npm to install only 614, and the installer explicitly refused publication of node_modules (`deployment-02.log`). These are Ubuntu/glibc2.39 results, not target Node22/Kylin certification.
- npm-only controlled input is staged at the real root-context path `deployment/offline/dsh/` (not output), deliberately git-ignored. `npm.json` SHA256 is `33692416807824f1aeb6c0f15ca5f4b6c2cb7df31078d373bbdd6021b2b6951b`. Local Docker engine COPY/export of the actual root context passed, preserving that identity and excluding host dependencies/tests/evidence (`build-context-01.log`, 9.412 s). No system.json or target RPMs exist: this is explicitly an incomplete preparation artifact, not a delivery package or working production context.
- Independent review01 used immutable source archive SHA256 `315091ef84f62828b3dc1deef1b0882eddd56c5193ab754b69e2a96b9d192ee0`; diff SHA256 `55c145ca6f994d732b295b1fcc0cc308b40bac770d8cd96c368cc9d490e58d5c`. Standards had 0 findings. Spec found P1 RPM signature diagnostics embedding source paths, wrongly preventing installation after staging. Public CLI relocation regression reproduced it (`red-rpm-relocation.log`), then passed after separating stable package identity from diagnostics (`green-rpm-relocation.log`). OS-release/RPM/yum are explicit external-boundary fixtures in this test, not real signed Kylin packages.
- Independent review02 confirmed P1 closure and **Standards 0 / Spec 0 actionable open findings**. Immutable source SHA256 `7e8725d7f77b274580f07d96d88354899389366d3661fff3a9e3bada432cdbed`; diff SHA256 `665cd8ebaa3ec7de5d4b14c060153925918924dc993a1065d4ac5ee0658a699c`. Reports remain in `review01/` and `review02/`; Spec independently reran the fixture against the immutable endpoint. Both axes keep external acceptance separate from code findings.
- `backend/Dockerfile` retains the existing path, root context, frontend/Python/LCR workflow and echo command. Default DSH=0 is an explicit disabled state, not an installation/readiness claim. The deployment guide separates installation activation (build arg if supported, otherwise an approved one-line ARG default change in the same Dockerfile), inherited runtime switch, and runtime namespace preflight. Actual platform build-arg support is unconfirmed; neither activation route bypasses required artifact injection.

### External Acceptance Blockers

- `docker pull csbase.registry.cmbchina.cn/paas/cmb-python-3.12:c86-kylin10-v1` failed with registry EOF (`target-pull.log`). Actual production Dockerfile build with DSH=1 failed resolving the Node base image manifest with EOF (`production-build-attempt.log`); no production image was produced. No actual target image digest/ABI, matching trusted RPM closure, target Node22/native ABI or inline npm/Python mirror result is certified.
- System preparation really rejects this Ubuntu host before creating RPMs (`system-host-rejected.log`); enabled installation really rejects missing system.json before any install (`enabled-missing-system.log`). No Ubuntu bwrap was copied as a purported Kylin artifact. The guide records exact target preparation, repository/signature/ABI checks and offline installation commands for the unavailable environment.
- Local host runtime preflight passed actual namespaces as UID1000 with Bubblewrap0.9.0, but it is **not the platform container** (`host-runtime-preflight.log`). Namespace denial fixture prevents actual container prepare from reaching service/storage preparation. No privileged container, capability/security-policy relaxation, setuid or unsandboxed fallback was used.
- No access to the real pipeline artifact-injection mechanism. Required chain is checkout -> approved complete artifact injection into that checkout -> verify approved identities -> unchanged echo -> original root-context Dockerfile build. Local staging/export does not prove Git-ignored files reach CI. If the platform cannot inject artifacts or preserve an uploaded complete context, deployment remains blocked.
- All actual target/platform checks above require inline human evidence. Ticket04 remains ready-for-human and independent. Ticket06 must not be marked done merely because local checks or source reviews pass.

### Final Local Verification (2026-10-22)

**Ready-for-human, not done.** Only this deployment ticket was implemented. Final source behavior is the immutable review02 endpoint; subsequent changes only record ticket/progress evidence. Installation activation, runtime enablement and actual isolation readiness remain distinct. The first six acceptance items stay unchecked because the real target/pipeline evidence is incomplete, not because local success is being treated as target certification.

| Gate | Actual Terminal Result | Evidence Under `output/playwright/ticket06/` |
| --- | --- | --- |
| `npm run check` | Passed, exit 0; Python complexity, frontend/script lint, React Hooks and TypeScript gates executed | `check-01.log`, `check-02.log`, `check-03.log` |
| `npm run verify` | Passed, exit 0; includes actual lint/type/build, Node tests and diff checks, not substitute scans | `verify-01.log`, `verify-final-02.log`; post-closeout rerun recorded below |
| Final full backend discovery | **261 collected, OK, 2 explicit skips**, 1678.947 s, exit 0 | `full-unittest-final-02.log`, `full-native-final/` |
| Earlier full discovery | 256 collected, OK, 1 skip, 1694.009 s, exit 0; collected before the final added contracts, not used instead of final discovery | `full-unittest-01.log`, `full-native/` |
| Deployment/container specialty | **18 collected, OK, 2 explicit skips**, 44.442 s, exit 0 | `deployment-final-03.log` |
| Actual Docker context export | 1 passed, 9.412 s, exit 0; scratch COPY/export only, not production image build | `build-context-01.log` |
| Standard regressions | **38 passed**, 29.828 s, exit 0 | `regressions-01.log` |
| Final full default `npm run e2e` | **15 passed**, browser errors 0, exit 0; real local DSH baseline/Skill paths enabled, LCR/judge/local storage finite substitutes | `e2e-final-03.log`; `output/playwright/2026-10-22T07-46-25-776Z/results.json` |
| `npm run e2e:explore` | **35 passed**, browser errors 0, exit 0 | `explore-01.log`; `output/playwright/2026-10-22T07-30-20-174Z/results.json` |

Full discovery used `env -i` with PATH/HOME/LANG, PYTHONUTF8/PYTHONUNBUFFERED, project root/PYTHONPATH, case/run/model storage modes `local`, `DSH_OFFLINE_TEST_BUNDLE=$PWD/deployment/offline/dsh` and a distinct `DSH_EVIDENCE_ROOT`. Command: `.venv/bin/python -m unittest discover -s backend/tests -v`. Final specialties: `-m unittest -v test_dsh_deployment test_dsh_context test_container` and `tools/check_regressions.py`. The two skips are deliberately opt-in networked npm preparation and Docker context export; each was actually run separately with its explicit switch earlier. They are not hidden passed tests or missing Kylin acceptance. Browser runs used `E2E_DSH=1 E2E_SIMULATED_LCR=1` and no real credentials/models/COS.

### Interrupted And Failed Browser Attempts

- Initial integrated e2e with `E2E_DSH_SCALE=1` ended **143**, without final results.json, during the native 50-case scenario (`e2e-01.log`, output `2026-10-22T07-14-51-064Z`). It is **not a passing full/native50 run**. Its baseline evidence and partial subsequent run remain preserved. The orchestration thread confirmed no user pause/manual-stop request and no orchestration termination signal; signal origin was not established and is not attributed to the user. Only after its command handle was terminal did this agent verify the orphan backend's exact PID/runtime and gracefully stop that round's own residual server/tasks. The two backend-discovery processes were left alive and both later finished exit 0.
- Next default e2e ended **1** at Skill re-import: blank page and `Failed to fetch dynamically imported module .../assets/Skills-BaXJCtKk.js`, followed by the 15 s upload-input wait timeout (`e2e-final-02.log`, output `2026-10-22T07-36-30-755Z`, screenshot/trace/results retained). Trace records HTTP200 for that asset and the built file exists; this does not establish a root cause. No frontend/server behavior was edited or timeout/retry relaxed to mask it.
- Fresh standalone `--only=skill-version-import` passed, exit 0 (`e2e-skill-diagnostic.log`, output `2026-10-22T07-45-47-179Z`). Fresh complete default e2e then passed 15 with no browser errors, as recorded above. The single observed module-load failure remains an unexplained intermittent risk; those reruns do not prove it cannot recur. Native full50 completion from the interrupted round is not claimed; company official50 acceptance remains ticket04's separate boundary.

### Exploration Fault Windows And Limits

- The queue/navigation soak ran **60 seconds / 74 iterations** (61.560 s scenario including setup), with local simulated LCR/judge only; `soak.json` records timings and memory observations. This bounded soak and the number of scenarios do not imply absence of production faults or long-term stability.
- Skill upload response loss was injected **after local persistence** by completing the first chunk/complete request and aborting the browser response: **5.959 s / 2.651 s**, exactly one request each, then reload/recovery (`skill-recovery.json`). No real COS was used.
- Native DSH cancellation and server-restart probes held the loopback model after the first completed case of a synthetic imported50 list: **62.230 s / 55.098 s** observed windows, cumulative request counts fixed at **3 / 6** through recovery, actual cleanup complete in **0.029 s / 0.015 s** (`dsh-recovery.json`). No historical review rerun or real model call occurred. These are local recovery probes, not actual platform container replacement, company official cases or target namespace acceptance.

All test command handles were polled to their actual terminal states. Final PID audit showed no remaining ticket-owned discovery/browser/server/DSH processes. No commit, push, publication, delivery ZIP, baseline expansion, dependency-version change, override or privilege/security relaxation was performed. Precise pre06 and both immutable review snapshots remain persistent. Remaining work is human target/pipeline acceptance described above and in `deployment/DSH离线部署.md`; stop after this ticket.

### Orchestration Closeout

The implementation session ended with a model-capacity system error after its documentation update. Its final recorded `npm run verify` command nevertheless completed with exit 0 (`verify-closeout-03.log`); orchestration read that actual log, the final backend/deployment results and both review reports. This is not a successful production-image certification, nor a claim that the interrupted session supplied a normal final response. No implementation was moved into another ticket/session.

Local code and command handoff are ready; target acceptance remains blocked by the actual Kylin images, matching trusted Bubblewrap RPM closure, approved artifact injection into CI and runtime namespace policy. The staged npm-only inputs are not a complete deployable offline bundle. Keep ready-for-human rather than done; the existing echo command remains unchanged, and DSH stays explicitly disabled until a complete, approved enabled build is performed. No commit or push was authorized or executed.
