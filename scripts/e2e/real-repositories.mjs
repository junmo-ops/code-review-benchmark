import assert from 'node:assert/strict';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { scenarios } from './scenarios.mjs';
import { api, upload, createRun, closeDialog, waitState, state, job } from './helpers.mjs';

async function suite() {
  const input = JSON.parse(await readFile(process.env.E2E_REAL_SUITE, 'utf8'));
  if (process.env.E2E_REAL_RECOVERY_ONLY === '1') {
    input.repositories = input.repositories.filter((r) => r.name === 'CherryHQ/cherry-studio');
    input.recoveryOnly = true;
  }
  return input;
}
async function importRepositories({ page, fixtures }) {
  const input = await suite();
  assert.equal(input.cases.length, 50);
  await page.goto('/repositories');
  await page.locator('input[type=file]').setInputFiles(input.repositories.map((r) => r.archive));
  await page.getByRole('button', { name: '导入仓库', exact: true }).click();
  const started = Date.now(), samples = [];
  let previous = -1;
  while (Date.now() - started < 1800000) {
    const snapshot = await state(page);
    const { task } = await api(page, '/api/cases/imports', undefined, 'GET');
    assert.notEqual(task?.status, 'failed', task?.message);
    assert.notEqual(task?.status, 'interrupted', task?.message);
    const count = snapshot.case_assets.repositories.length;
    samples.push({ timestamp: new Date().toISOString(), count, task });
    if (count !== previous) { console.log(`REAL repositories imported: ${count}/${input.repositories.length}`); previous = count; }
    if (count === input.repositories.length) break;
    await delay(2000);
  }
  await page.getByText(`本批次已导入 ${input.repositories.length} / ${input.repositories.length} 个 ZIP`, { exact: true }).waitFor({ timeout: 15000 });
  const imported = (await state(page)).case_assets.repositories.map((r) => r.slug).sort();
  assert.deepEqual(imported, input.repositories.map((r) => r.name.replace('/', '__')).sort());
  await writeFile(join(fixtures, '../../real-imports.json'), JSON.stringify(samples, null, 2));
}
async function importCases({ page }) {
  const input = await suite();
  const files = input.recoveryOnly ? [input.recovery_archive] : [input.case_archive, input.recovery_archive];
  for (const file of files) {
    await upload(page, '/cases', file, '确认导入');
    await page.getByText(/所选文件导入完成/).waitFor({ timeout: 900000 });
  }
  await page.reload();
  await page.getByRole('row').filter({ hasText: input.recoveryOnly ? 'AACR真实仓库恢复验收' : 'AACR真实仓库50例验收' }).waitFor();
}
async function workspaceCount(fixtures, key) {
  try { return (await readdir(join(fixtures, '../runs', key, 'workspaces'))).length; }
  catch (error) { if (error.code === 'ENOENT') return 0; throw error; }
}
async function verifyAnswers(fixtures, run, cases) {
  const folder = join(fixtures, '../runs', run.key);
  const files = (await readdir(join(folder, 'results'))).filter((f) => f.endsWith('.json'));
  assert.equal(files.length, cases.length);
  const scope = JSON.parse(await readFile(join(folder, 'evaluation-scope.json'), 'utf8'));
  assert.deepEqual([...scope.included].sort(), cases.map((c) => c.instance_id).sort());
  for (const item of cases) {
    const evidence = JSON.parse(await readFile(join(folder, 'raw', item.instance_id + '.workspace-evidence.json'), 'utf8'));
    assert.equal(evidence.actual_head, item.head_commit);
    assert.equal(JSON.parse(await readFile(join(folder, 'raw', item.instance_id + '.mock-attempts.json'), 'utf8')), 1);
  }
  assert.equal(await workspaceCount(fixtures, run.key), 0);
}
async function observeQueue(page, fixtures, runs, cancelled) {
  const samples = [], started = Date.now();
  while (Date.now() - started < 1800000) {
    const before = Date.now(), snapshot = await state(page);
    const counts = await Promise.all(runs.map((r) => workspaceCount(fixtures, r.key)));
    const entries = runs.map((r) => job(snapshot, r.job_id));
    samples.push({ timestamp: new Date().toISOString(), stateMs: Date.now() - before, workspaces: counts,
      completed: runs.map((r) => snapshot.runs.find((v) => v.key === r.key)?.completed ?? 0) });
    assert.equal(snapshot.worker_ready, true, '真实仓库任务期间 Worker 心跳中断');
    assert.ok(counts.reduce((a, b) => a + b, 0) <= 2, '工作区数量超过案例并发 2');
    assert.ok(entries.every((v) => !['failed', 'interrupted', 'cancelled'].includes(v.status)), JSON.stringify(entries));
    assert.equal(job(snapshot, cancelled.job_id).status, 'cancelled');
    if (entries.every((v) => v.status === 'succeeded')) return { snapshot, samples };
    await delay(1000);
  }
  throw new Error('真实仓库两轮队列超过本次 30 分钟验收窗口');
}
async function realQueue({ page, fixtures }) {
  const input = await suite();
  const first = await createRun(page, 'success', 'AACR真实仓库50例验收', 2);
  await closeDialog(page);
  const second = await createRun(page, 'success', 'AACR真实仓库50例验收', 2);
  assert.equal(job(await state(page), second.job_id).status, 'queued');
  await closeDialog(page);
  const cancelled = await createRun(page, 'success', 'AACR真实仓库恢复验收');
  await page.getByRole('button', { name: /取消排队|停止任务/ }).click();
  await waitState(page, (s) => job(s, cancelled.job_id)?.status === 'cancelled', '真实仓库第三轮取消排队');
  const observed = await observeQueue(page, fixtures, [first, second], cancelled);
  await verifyAnswers(fixtures, first, input.cases);
  await verifyAnswers(fixtures, second, input.cases);
  const a = job(observed.snapshot, first.job_id), b = job(observed.snapshot, second.job_id);
  assert.ok(Date.parse(b.started_at) >= Date.parse(a.ended_at));
  assert.equal(job(observed.snapshot, cancelled.job_id).started_at, undefined);
  await writeFile(join(fixtures, '../../real-queue.json'), JSON.stringify({ first, second, cancelled, samples: observed.samples,
    boundary: 'Original AACR 50 cases and complete local Git histories; LCR and judge simulated' }, null, 2));
}
async function continueRun(page, key) {
  await page.goto('/?run=' + key);
  await page.getByRole('button', { name: '继续未完成案例并评分', exact: true }).click();
  const response = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.getByRole('dialog').last().getByRole('button', { name: '开始执行', exact: true }).click();
  const reply = await response, body = await reply.json();
  assert.equal(reply.status(), 202, JSON.stringify(body));
  await waitState(page, (s) => job(s, body.job_id)?.status === 'succeeded', '真实仓库原批次继续完成', 900);
}
async function realRecovery({ page, fixtures, restart }, kill = false) {
  const input = await suite();
  const created = await createRun(page, 'slow', 'AACR真实仓库恢复验收');
  const snapshot = await waitState(page, (s) => s.runs.find((r) => r.key === created.key)?.completed === 1, '真实仓库首例答卷保存', 900);
  const folder = join(fixtures, '../runs', created.key, 'results');
  const file = (await readdir(folder)).find((f) => f.endsWith('.json'));
  const before = await readFile(join(folder, file), 'utf8');
  assert.equal(job(snapshot, created.job_id).status, 'running');
  if (kill) {
    await restart();
    await page.goto('/?run=' + created.key);
    await waitState(page, (s) => s.recovery_required && job(s, created.job_id)?.status === 'interrupted', '真实仓库容器重启后记录恢复');
    assert.equal((await state(page)).jobs.filter((j) => j.status === 'running').length, 0);
  } else {
    await page.getByRole('button', { name: '停止任务', exact: true }).click();
    await waitState(page, (s) => job(s, created.job_id)?.status === 'cancelled', '真实仓库主动停止');
  }
  await continueRun(page, created.key);
  assert.equal(await readFile(join(folder, file), 'utf8'), before);
  await verifyAnswers(fixtures, created, input.recovery);
}
export const realScenarios = [
  ...scenarios.slice(0, 2).map(([name, fn]) => ['setup-' + name, fn]),
  ['setup-real-repositories', importRepositories],
  ['setup-real-cases', importCases],
  ['real-two-fifty-case-queue', realQueue],
  ['real-stop-continue', realRecovery],
  ['real-container-restart', (context) => realRecovery(context, true)],
];
