import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { createRun, state, job, waitState, upload, closeDialog } from './helpers.mjs';

function processMemory(root) {
  if (process.platform === 'win32') return null;
  const result = spawnSync('ps', ['-axo', 'pid=,ppid=,rss='], { encoding: 'utf8', timeout: 3000 });
  if (result.status !== 0) return null;
  const rows = result.stdout.trim().split('\n').map((line) => line.trim().split(/\s+/).map(Number));
  const owned = new Set([root]);
  let previous = 0;
  while (owned.size !== previous) {
    previous = owned.size;
    for (const [pid, parent] of rows) if (owned.has(parent)) owned.add(pid);
  }
  const processes = rows.filter(([pid]) => owned.has(pid));
  return { processes: processes.length, rssBytes: processes.reduce((sum, row) => sum + row[2] * 1024, 0) };
}
async function workspaces(fixtures, key) {
  try { return (await readdir(join(fixtures, '../runs', key, 'workspaces'))).length; }
  catch (error) { if (error.code === 'ENOENT') return 0; throw error; }
}
async function observe(page, fixtures, runs, cancelled) {
  const started = Date.now(), samples = [];
  const server = JSON.parse(await readFile(join(fixtures, '../server.json'), 'utf8'));
  while (Date.now() - started < 180000) {
    const before = Date.now(), snapshot = await state(page);
    const counts = await Promise.all(runs.map((r) => workspaces(fixtures, r.key)));
    const entries = runs.map((r) => job(snapshot, r.job_id));
    samples.push({ milliseconds: Date.now() - started, stateMs: Date.now() - before,
      workspaces: counts, memory: server.containerized ? null : processMemory(server.pid) });
    assert.equal(snapshot.worker_ready, true, '长任务期间 Worker 心跳不应停止');
    assert.ok(snapshot.jobs.filter((j) => ['running', 'cancelling'].includes(j.status)).length <= 1);
    assert.ok(counts.every((n) => n <= 2), '工作区数量超过设置的案例并发');
    assert.equal(job(snapshot, cancelled.job_id).status, 'cancelled');
    assert.ok(entries.every((j) => !['failed', 'interrupted', 'cancelled'].includes(j.status)), JSON.stringify(entries));
    if (entries.every((j) => j.status === 'succeeded')) return { snapshot, samples, server };
    await delay(500);
  }
  throw new Error('两轮 50 例队列在 180 秒本地模拟预算内未结束');
}
export async function scaledQueue({ page, fixtures }) {
  await upload(page, '/cases', join(fixtures, 'cases-50.zip'), '确认导入');
  await waitState(page, (s) => s.catalog.some((c) => c.datasets.some((d) => d.name === '浏览器回归50例')), '50 例案例集导入');
  const first = await createRun(page, 'success', '浏览器回归50例', 2);
  await closeDialog(page);
  const second = await createRun(page, 'success', '浏览器回归50例', 2);
  assert.equal(job(await state(page), second.job_id).status, 'queued');
  await closeDialog(page);
  const cancelled = await createRun(page, 'success');
  await page.getByRole('button', { name: /取消排队|停止任务/ }).click();
  await waitState(page, (s) => job(s, cancelled.job_id)?.status === 'cancelled', '取消第三轮排队');
  const { snapshot, samples, server } = await observe(page, fixtures, [first, second], cancelled);
  for (const run of [first, second]) {
    assert.equal(snapshot.runs.find((r) => r.key === run.key).completed, 50);
    const folder = join(fixtures, '../runs', run.key);
    assert.equal((await readdir(join(folder, 'results'))).filter((f) => f.endsWith('.json')).length, 50);
    const scope = JSON.parse(await readFile(join(folder, 'evaluation-scope.json'), 'utf8'));
    assert.equal(scope.included.length, 50);
    assert.equal(new Set(scope.included).size, 50);
    assert.equal(await workspaces(fixtures, run.key), 0);
  }
  const firstJob = job(snapshot, first.job_id), secondJob = job(snapshot, second.job_id);
  assert.ok(Date.parse(secondJob.started_at) >= Date.parse(firstJob.ended_at), '排队顺序或串行执行不正确');
  assert.equal(job(snapshot, cancelled.job_id).started_at, undefined, '已取消的排队任务曾被执行');
  await writeFile(join(fixtures, '../../scaled-queue.json'), JSON.stringify({ samples, first, second, cancelled,
    model: 'deterministic simulation', repository: 'tiny local fixture', memoryLimitEnforced: server.memory_limit_bytes === 2147483648 }, null, 2));
}
