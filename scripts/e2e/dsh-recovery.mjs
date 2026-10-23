import assert from 'node:assert/strict';
import { readFile, writeFile, rm, stat } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { api, job, select, state, waitState } from './helpers.mjs';
import { createFullBatch, prepareLifecycle } from './dsh-lifecycle.mjs';

async function requests(data) {
  try { return JSON.parse(await readFile(join(data, 'dsh-model-requests.json'), 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return []; throw error; }
}

async function stopAtSecondCase(page, fixtures, interrupted) {
  const data = resolve(fixtures, '..');
  const before = (await requests(data)).filter((row) => row.model === 'dsh-review').length;
  const control = join(data, 'dsh-model-control.json');
  await writeFile(control, JSON.stringify({ hold_after_review_calls: before + 2 }));
  const started = Date.now();
  const created = await createFullBatch(page, 'baseline');
  await waitState(page, (s) => s.runs.find((r) => r.key === created.key)?.completed === 1 &&
    job(s, created.job_id)?.step === 'review · scale-001', 'DSH 首例完成后阻塞第二例', 120);
  const root = resolve(data, 'runs', created.key);
  const execution = join(root, 'raw/scale-001.execution.json');
  await waitFile(execution);
  await delay(1200);
  if (interrupted) await writeFile(join(data, 'interrupt-worker'), 'interrupt');
  else await page.getByRole('button', { name: '停止任务', exact: true }).first().click();
  const terminal = interrupted ? 'interrupted' : 'cancelled';
  await waitState(page, (s) => job(s, created.job_id)?.status === terminal, 'DSH 停止终态', 30);
  const record = JSON.parse(await readFile(execution, 'utf8'));
  assert.equal(record.status, 'interrupted');
  assert.equal(record.cleanup_status, 'complete');
  const command = record.command_without_credentials;
  const manifest = JSON.parse(await readFile(join(root, 'raw/scale-001.manifest.json'), 'utf8'));
  const workspace = manifest.execution_root ?? resolve(command[command.indexOf('/work') - 1], '..');
  await assert.rejects(stat(workspace), { code: 'ENOENT' });
  const saved = await readFile(join(root, 'results/scale-000.json'), 'utf8');
  await rm(control);
  await delay(1500);
  const after = (await requests(data)).filter((row) => row.model === 'dsh-review').length;
  assert.equal(after, before + 3);
  return { created, root, saved, record, milliseconds: Date.now() - started, requests: after };
}

async function waitFile(path) {
  for (let count = 0; count < 100; count++) {
    try { await stat(path); return; } catch (error) { if (error.code !== 'ENOENT') throw error; }
    await delay(100);
  }
  throw new Error(`Missing actual execution evidence: ${path}`);
}

async function recoverInBrowser(page, evidence) {
  await page.goto(`/?run=${evidence.created.key}&tab=overview`);
  await page.getByText('分步执行、恢复与导出', { exact: true }).click();
  await select(page, '执行范围', 'scale-000');
  const response = page.waitForResponse((reply) => reply.url().endsWith('/api/jobs') && reply.request().method() === 'POST');
  await page.getByRole('button', { name: '从已有输出恢复结果', exact: true }).click();
  const created = await (await response).json();
  await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', 'DSH 零调用恢复');
  assert.equal(await readFile(join(evidence.root, 'results/scale-000.json'), 'utf8'), evidence.saved);
}

export async function dshRecovery({ page, fixtures, restart }) {
  await prepareLifecycle(page, fixtures);
  const cancelled = await stopAtSecondCase(page, fixtures, false);
  await recoverInBrowser(page, cancelled);
  const interrupted = await stopAtSecondCase(page, fixtures, true);
  await restart();
  await page.reload();
  let snapshot = await waitState(page, (s) => s.recovery_required, 'DSH 重启后的恢复阻塞');
  assert.equal(job(snapshot, interrupted.created.job_id).status, 'interrupted');
  assert.equal(await page.getByRole('button', { name: '继续未完成案例并评分', exact: true }).count(), 0);
  await api(page, '/api/recovery', { confirmed: true });
  await recoverInBrowser(page, interrupted);
  await delay(2000);
  snapshot = await state(page);
  assert.equal(snapshot.jobs.filter((j) => ['running', 'queued'].includes(j.status)).length, 0);
  assert.equal((await requests(resolve(fixtures, '..'))).filter((row) => row.model === 'dsh-review').length, interrupted.requests);
  await writeFile(resolve(fixtures, '../../dsh-recovery.json'), JSON.stringify({ cancelled, interrupted }, null, 2));
}
