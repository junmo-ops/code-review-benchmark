# Historical Regression Repair

Status: done

The user explicitly approved an additional independent repair session on 2026-10-21. This supports the original five tickets and does not replace their requirements.

## Scope

- Diagnose and fix the historical full-discovery failures recorded by tickets 01 and 02: 17 failures and 51 errors. Preserve coverage; do not skip or delete tests, weaken assertions, expand baselines, or raise thresholds to obtain green results.
- Repair obsolete paths, stale fixtures, and environment assumptions against the current public behavior. Retain meaningful regression assertions. Fix production behavior only when evidence establishes a real regression or contract defect.
- Keep automated runs local and deterministic. Never call real models or real COS. Explicit local executor fixtures may test orchestration, but must not be presented as real LCR or DSH runtime verification. A missing real external artifact remains a disclosed limitation where that artifact is genuinely required.
- Investigate the existing Vite chunk warning; prefer actual lazy loading or sensible chunking, never a raised warning threshold or suppression.
- Preserve all uncommitted 01 and 02 changes and capture a pre-repair workspace snapshot for review. Do not implement 03, 04, or 05 in the repair session.

## Acceptance

- Run the full backend discovery suite with an explicit, recorded local environment, the standard regression runner, `npm run check`, and `npm run verify`. Every failure must be resolved or recorded as a concrete external blocker; failures cannot be relabeled as passed.
- Run local browser regression and exploration for affected behavior, recording fault injection, duration, and simulation boundaries. Retain real Linux DSH verification separately from LCR fixtures and browser simulation.
- Use the implement skill and its two-axis code-review process. The review may use skill-required sub-agents within this repair session. No additional implementation TUI threads.
- Report exact commands, outcomes, and evidence. Do not commit, push, publish, or package.

## Evidence

- Ticket 01 HEAD/current comparison: `output/playwright/regression-comparison-DuTyDo/`.
- Ticket 02 logs: `output/playwright/ticket02-evidence/`.
- After both implementations, orchestration `npm run verify` exited 0 but retained the chunk warning. `npm run verify:dsh` passed all 20 real-Linux-DSH tests in 339.111 seconds: `output/playwright/dsh-contract-2026-10-21T09-24-40-056Z/`.
- Integration browser run started at `output/playwright/2026-10-21T09-30-45-728Z/`; inspect its authoritative results rather than assuming completion.

## Comments

Implementation session: `01a15dcf-a05d-7572-9113-26a3839bf8af`.

The integration browser process subsequently exited 0 and all scenarios passed, including Skill import and real DSH baseline. Results: `output/playwright/2026-10-21T09-30-45-728Z/results.json`. The browser run retained explicit simulated LCR and local model/judge boundaries.

### Repair Baseline And Test Boundaries

- Pre-repair snapshot: `/tmp/dsh-regression-repair-baseline.tar`, SHA-256 `2f4678367129be0735b463ceda27f3d2ce66e70325b66acee3ee2842d0651377`; extracted to `/tmp/dsh-regression-repair-baseline`. Includes tracked and nonignored untracked files, including the uncommitted 01/02 implementation. HEAD remains `83edcd5ffca36219dcc97a655eed4112ea4d1698`; HEAD alone is not this repair's review baseline.
- Existing agreed regression boundaries are CLI lifecycle, workbench creation/planning/queue, authenticated HTTP APIs, frozen suite/tool provenance, scoring, and browser workflows. No new Skill execution, scaling, or COS asset features are implemented.
- Initial red commands: explicit local-environment `unittest test_local test_model_connection test_concurrency -v` (obsolete `backend/local.py` import), then `unittest test_model_connection test_concurrency -v` (obsolete HTTP mock and missing temporary dataset fixtures). Logs: `output/playwright/regression-repair-evidence/red-initial.log` and `red-contracts.log`. Existing full-discovery logs remain preserved, not relabeled as passing.
- Repairs align relocated `backend/src` entrypoints and project data paths; prepare real dataset files in temporary dashboards; mark LCR identity substitutes explicitly as orchestration-only fixtures. Windows bootstrap assertions now check the shared pinned requirements contract; its package test uses only temporary synthetic assets, checks the verify invocation, and additionally asserts verification failure creates no archive. No delivery package is produced.
- HTTP tests use `httpx.MockTransport` through the real client/build/send/close path and keep request count, payload, endpoint, auth, completion and sanitization assertions. Page tests check the React shell/CSP and obtain CSRF through `/api/session`, not obsolete embedded-token markup.
- First build reproduces the 873.21 kB warning (`build-before.log`). The repair mounts CreateRun only when selected, splits explicit React and UI runtimes, and leaves automatic route/component splitting intact. Final trial build (`build-02.log`) has no chunk warning; largest chunk 447.87 kB. Warning limits and quality baselines are unchanged.
- First broad targeted run: 96 tests, 5 errors; missing comparison identity fixtures and incomplete command input then repaired. Cross-module targeted run: 37 tests passed. First full-discovery run exposed a test-only nested environment patch cleanup leak introduced during the repair; corrected before the final rerun, without changing production validation. Exact final gates and independent review are pending below.

### Evidenced Process Cleanup Fix

- The initial full rerun completed with 205 tests, 32 failures and 8 errors (`full-unittest-01.log`): most came from the repair's nested environment cleanup ordering and were fixed without touching production authorization. A separate process-tree failure remained relevant even after a later 205-test discovery run passed (`full-unittest-02.log`, 389.120 seconds); a green rerun alone was not used to dismiss it.
- The original immediate descendant assertion repeated 20 times failed 5 times and hit one `/proc` exists/read disappearance race in 40.062 seconds (`process-repeat-red.log`). Diagnostic instrumentation records the same PID/start-time identities alive immediately after the outer Bubblewrap exited and gone 50 ms later (`process-cleanup-probe.log`). The delayed diagnostic is not acceptance evidence; its script is explicitly retained only under the ignored evidence directory.
- Production cleanup now snapshots descendants using the existing process helper and waits within a 10-second cleanup budget for their exit; it does not count the outer process exit as completed cleanup. Supervised duration and cleanup duration remain separate. The original immediate assertion remains, with atomic read/error handling for disappearing `/proc` entries and no test-side wait. Repeating that assertion against the old supervisor still fails 4/20 (`process-old-supervisor.log`, 40.069 seconds); the fixed supervisor passes 20/20 (`process-repeat-green.log`, 40.312 seconds).
- A real controlled-process test injects OS signal denial after both exit 0 and timeout. Cleanup cannot return success, and execution/output evidence is retained with `cleanup_status=failed`. Test fixtures retain, kill and reap their actual processes; `python -W error::ResourceWarning -m unittest test_dsh_process -v` passes all 3 tests (`process-final-03.log`, 2.275 seconds). These are process-boundary fixtures, not real DSH/model-quality verification.
- Standards review then found a P1: retaining an exited-zero record with failed cleanup allowed later answer recovery. The public CLI test first reproduced it (`recovery-cleanup-red-exact.log`, 18.916 seconds), then passed after recovery rejects explicit non-complete cleanup (`recovery-cleanup-green.log`, 17.948 seconds). It uses real Linux DSH and a loopback-only complete-answer model, verifies no extra requests, and proves byte-identical recovery for legacy records without the new field. Legacy compatibility is explicit: the old supervisor only finalized exited records after cleanup returned successfully.
- Intermediate 206-test/21-test-DSH and browser passes are retained as `*-pre-recovery.log`; they are not final-state gates. The final suite retains the original 20 DSH-suite tests plus the two added cleanup/evidence regression tests.

### Independent Review

Both read-only axes compare the precise pre-repair snapshot, not HEAD, and exclude the pre-existing 01/02 work. Review tasks are `/root/repair_standards_review` and `/root/repair_spec_review`; both independently re-reviewed the final process/recovery changes. No additional implementation TUI thread was created.

#### Standards

Prior P1 is closed: recovery rejects explicit incomplete cleanup, while missing-field compatibility matches the pre-repair persistence contract. The public CLI regression checks failed-cleanup rejection, no added model requests and byte-identical legacy recovery. Signal-denial fixtures are reaped. No remaining documented-standard violations, actionable baseline smells or concrete cleanup-safety findings.

#### Spec

No remaining findings. The cleanup and recovery fixes address evidenced ticket-01 contract defects, not ticket-04 lifecycle functionality. Meaningful immediate process, CLI, compatibility and no-new-request assertions are retained; no skips, weakened assertions, baseline growth, threshold increases or 03-05 implementation.

Review summary: Standards 0 open findings (one P1 resolved); Spec 0 findings. Reviews do not independently certify gate outcomes; the actual final command results are recorded separately below.

### Browser Assertion Repair

The first post-recovery integrated browser run failed in `error-isolation-and-delete-feedback`: the old singular hidden-state locator encountered both old-page error renderings during SPA navigation. The failed result is retained at `output/playwright/2026-10-21T09-57-28-754Z/results.json`, with `e2e-strict-locator-failed.log`. The assertion now polls all matches within the unchanged 15-second default deadline and requires zero matching DOM nodes. It does not use `.first()`, hide an error, or relax the assertion. Both independent review axes re-reviewed this exact harness delta and found no issue. Backend and frontend production code did not change for this adjustment.

### Final Commands

From the project root, the final full discovery uses only this explicitly selected environment:

```bash
env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 PYTHONUTF8=1 PYTHONUNBUFFERED=1 \
  BENCH_PROJECT_ROOT="$PWD" BENCH_RUN_STORAGE_MODE=local BENCH_CASE_STORAGE_MODE=local \
  BENCH_MODEL_STORAGE_MODE=local PYTHONPATH="$PWD/backend/src:$PWD/backend/tests:$PWD" \
  .venv/bin/python -m unittest discover -s backend/tests -v
```

The standard regression command is:

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

Output is retained under `output/playwright/regression-repair-evidence/`. Final backend, DSH, standard regression and browser runs use the `*-final.log` names. The final browser-harness adjustment additionally reruns check/verify as `check-browser-final.log` and `verify-browser-final.log`. Failed, diagnostic and superseded runs remain separate and are not counted as passes.

### Final Results And Boundaries

All required final command processes exited 0. Local runtime: Linux x86_64, Python 3.12.3, Node v25.8.2, Bubblewrap 0.9.0, fixed DSH 0.2.1-rc.2. No dependency overrides, package version changes, quality baseline changes or threshold increases were made by this repair.

| Gate | Final Outcome | Evidence |
| --- | --- | --- |
| Full backend discovery | 207 tests passed, 423.807 seconds; no failures/errors/skips reported | `full-unittest-final.log` |
| Standard regressions | 38 tests passed, 29.737 seconds | `regressions-final.log` |
| `npm run check` | Passed Python complexity, frontend/Node lint with zero warning tolerance, Hooks and TypeScript | `check-final.log`, `check-browser-final.log` |
| `npm run verify` | Passed check, 9 Node contracts, production build and staged/unstaged diff checks; no chunk warning | `verify-final.log`, `verify-browser-final.log` |
| `npm run verify:dsh` | 22 tests passed, 386.238 test seconds; 403.567 seconds for the verification command | `dsh-final.log`; `output/playwright/dsh-contract-2026-10-21T09-57-27-583Z/results.json` |
| Integrated browser regression | 14 scenarios passed, 185.273 cumulative scenario seconds; browser errors 0 | `e2e-final.log`; `output/playwright/2026-10-21T10-00-12-009Z/results.json` |
| Browser exploration | 34 scenarios passed, 357.914 cumulative scenario seconds; browser errors 0 | `explore-final.log`; `output/playwright/2026-10-21T09-57-29-905Z/results.json` |

The original historical discovery failures are resolved under the explicit local test environment. Restored import paths discover the actual tests rather than loader-error placeholders; coverage was not deleted or skipped. LCR identity/execution orchestration tests use named local substitutes, while Windows identity tests still hash real temporary runtime files. The unavailable external LCR Linux executable remains a limitation for **real LCR runtime validation**; these results do not claim that runtime has been installed or certified.

The build's largest chunk is 447.87 kB, below the unchanged 500 kB warning threshold. The actual browser trace also confirms delayed CreateRun asset fetching, not only a source-content assertion (`lazy-network.json` and its referenced trace). Browser screenshots, downloads and traces are retained alongside each authoritative result.

Exploration injects delayed/lost responses, JSON and HTML 503s, malformed state/local storage, expired uploads, worker interruption and abrupt local server restart. The final bounded queue/navigation observation lasts 60 seconds, 96 iterations (`soak.json`). Skill upload faults drop a response only **after the real local API persists**: the chunk-response recovery takes 5272 ms and complete-response recovery 2007 ms; each affected request is sent exactly once (`skill-recovery.json`). This is a single-machine browser/network fault model, not power-loss, kernel-crash, real COS or production-platform certification. Existing 50-case queue scenarios use local LCR fixtures, not ticket-04 DSH scale verification. Counts do not imply the project is free of defects.

The DSH-specific run and integrated DSH browser paths use the actual fixed Linux runtime and real Git, with loopback-only deterministic model/judge responses. Other browser execution/scoring and COS storage behavior use explicit local fixtures. No real model, real COS, production image, inline npm mirror acceptance or remote publishing was performed. Local browser acceptance and static/project gates are separate evidence.

01/02 uncommitted work is retained; only the documented cleanup/recovery defect fixes alter their DSH implementation. No 03-05 functionality was implemented. No commit, push, publish or delivery package was created; archive-generation regression tests use only disposable synthetic temporary roots. This independent repair session is complete and stops here; original ticket closure and remaining implementation sessions are not undertaken by this repair.
