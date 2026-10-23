import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFile, readdir, writeFile, stat } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { api, job, select, upload, waitState, state } from './helpers.mjs';

async function importFile(page, fixtures, url, name, button) {
  await page.goto(url);
  const finish = page.getByRole('button', { name: '结束本批次', exact: true });
  if (await finish.isVisible()) await finish.click();
  await upload(page, url, join(fixtures, name), button);
  await page.getByText(/所选文件导入完成/).waitFor();
}

function processes(parent) {
  if (!parent) return [];
  const output = spawnSync('ps', ['-eo', 'pid=,ppid=,rss=,args='], { encoding: 'utf8' });
  assert.equal(output.status, 0);
  const rows = output.stdout.trim().split('\n').map((line) => {
    const [pid, ppid, rss, ...args] = line.trim().split(/\s+/);
    return { pid: Number(pid), ppid: Number(ppid), rssKiB: Number(rss), command: args.join(' ') };
  });
  const ids = new Set([parent]);
  for (let size = 0; size !== ids.size;) {
    size = ids.size;
    for (const row of rows) if (ids.has(row.ppid)) ids.add(row.pid);
  }
  return rows.filter((row) => ids.has(row.pid));
}

export async function createFullBatch(page, mode, version) {
  await page.goto('/');
  await page.getByRole('button', { name: /新建测评/ }).first().click();
  for (const [label, value] of [['案例集', 'AACR'], ['案例范围', 'DSH 本地50例'],
    ['被测模型', 'DSH 本地review'], ['裁判模型', 'DSH 本地judge'], ['被测工具', 'DSH + Skill']]) {
    await select(page, label, value);
  }
  await select(page, 'Skill 模式', mode === 'skill' ? '加载指定 Skill 版本' : '不加载 Skill（基线）');
  if (mode === 'skill') await select(page, 'Skill 版本', version.id.slice(0, 12));
  await select(page, '评分方法', '案例集原始评分');
  const response = page.waitForResponse((reply) => reply.url().endsWith('/api/jobs') && reply.request().method() === 'POST');
  await page.getByRole('button', { name: '创建并开始', exact: true }).click();
  const reply = await response;
  const created = await reply.json();
  assert.equal(reply.status(), 202, JSON.stringify(created));
  return created;
}

async function observe(page, created, evidence) {
  const start = Date.now();
  let snapshot;
  while (Date.now() - start < 1800000) {
    snapshot = await state(page);
    const current = job(snapshot, created.job_id);
    const run = snapshot.runs.find((r) => r.key === created.key);
    evidence.push({ milliseconds: Date.now() - start, status: current?.status, step: current?.step,
      completed: run?.completed, total: run?.total, workerReady: snapshot.worker_ready,
      processes: processes(current?.pid) });
    if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(current?.status)) break;
    await delay(1000);
  }
  assert.equal(job(snapshot, created.job_id)?.status, 'succeeded', JSON.stringify(job(snapshot, created.job_id)));
  assert.equal(job(snapshot, created.job_id).total_steps, 53);
  const run = snapshot.runs.find((r) => r.key === created.key);
  assert.equal(run.total, 50);
  assert.equal(run.completed, 50);
  assert.equal(run.scored_cases, 50);
  assert.ok(evidence.some((row) => row.completed > 0 && row.completed < 50 && row.workerReady));
  assert.ok(evidence.filter((row) => row.completed > 0 && row.completed < 50).every((row) => row.workerReady));
  assert.ok(evidence.every((row) => row.processes.filter((p) => p.command.startsWith('/node --import')).length <= 1));
  return run;
}

async function inspectRun(fixtures, run, mode) {
  const root = resolve(fixtures, '../runs', run.key);
  const config = JSON.parse(await readFile(join(root, 'run-config.json'), 'utf8'));
  const rows = (await readFile(join(root, 'dataset.jsonl'), 'utf8')).trim().split('\n').map((line) => JSON.parse(line));
  assert.equal(rows.length, 50);
  assert.equal(new Set(rows.map((row) => row.instance_id)).size, 50);
  assert.deepEqual(config.selected_instances, rows.map((row) => row.instance_id));
  assert.equal(config.timeout_minutes, 10);
  assert.equal((await readdir(join(root, 'results'))).filter((name) => name.endsWith('.json')).length, 50);
  const sessions = new Set();
  const workspaces = new Set();
  let requests = 0;
  for (const row of rows) {
    const answer = JSON.parse(await readFile(join(root, 'results', `${row.instance_id}.json`), 'utf8'));
    assert.equal(answer.instance_id, row.instance_id);
    assert.equal(answer.base_commit, row.base_commit);
    assert.equal(answer.head_commit, row.head_commit);
    assert.equal(answer.skill.mode, mode);
    if (mode === 'skill') assert.equal(answer.skill.loaded, true);
    sessions.add(answer.session_id);
    const execution = JSON.parse(await readFile(join(root, 'raw', `${row.instance_id}.execution.json`), 'utf8'));
    assert.equal(execution.cleanup_status, 'complete');
    assert.ok(execution.process_timeout_seconds > 590);
    const command = execution.command_without_credentials;
    const manifest = JSON.parse(await readFile(join(root, 'raw', `${row.instance_id}.manifest.json`), 'utf8'));
    const workspace = manifest.execution_root ?? resolve(command[command.indexOf('/work') - 1], '..');
    workspaces.add(workspace);
    await assert.rejects(stat(workspace), { code: 'ENOENT' });
    const records = JSON.parse(await readFile(join(root, 'raw', `${row.instance_id}.requests.json`), 'utf8'));
    assert.equal(records.length, 2);
    requests += records.length;
  }
  assert.equal(sessions.size, 50);
  assert.equal(workspaces.size, 50);
  const audit = JSON.parse(await readFile(join(root, 'judge-audit.json'), 'utf8'));
  assert.equal(audit.eval_rounds, 3);
  assert.equal(audit.requests.length, 150);
  return { config, rows, requests, judgeRequests: audit.requests.length, sessions: [...sessions], workspaces: [...workspaces] };
}

export async function prepareLifecycle(page, fixtures) {
  await importFile(page, fixtures, '/repositories', 'repository.zip', '导入仓库');
  await importFile(page, fixtures, '/cases', 'dsh-cases-50.zip', '确认导入');
  await importFile(page, fixtures, '/skills', 'skill.zip', '导入 Skill');
  return (await api(page, '/api/skills', undefined, 'GET')).versions.find((v) => v.name === 'browser-review');
}

export async function dshLifecycle({ page, fixtures }) {
  const version = await prepareLifecycle(page, fixtures);
  const evidence = { boundary: 'Real pinned Linux DSH; synthetic imported 50-row Git dataset; loopback model/judge only.', batches: [] };
  for (const mode of ['baseline', 'skill']) {
    const created = await createFullBatch(page, mode, version);
    const samples = [];
    const run = await observe(page, created, samples);
    const records = await inspectRun(fixtures, run, mode);
    const report = await api(page, `/api/artifact?key=${created.key}&path=report.zh-CN.md`, undefined, 'GET');
    assert.match(report.text, /完整答卷 50\/50/);
    assert.match(report.text, /实际评分 50\/50/);
    await page.goto(`/?run=${created.key}&tab=overview`);
    await page.getByText('50/50 份完整答卷已保存', { exact: true }).waitFor();
    await page.screenshot({ path: resolve(fixtures, `../../dsh-50-${mode}.png`), fullPage: true });
    evidence.batches.push({ mode, key: created.key, samples, ...records });
    await writeFile(resolve(fixtures, '../../dsh-50-lifecycle.json'), JSON.stringify(evidence, null, 2));
  }
  await waitState(page, (s) => !s.jobs.some((j) => ['running', 'cancelling'].includes(j.status)), '全量批次停止');
}
