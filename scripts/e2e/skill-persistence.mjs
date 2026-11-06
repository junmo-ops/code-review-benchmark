import assert from 'node:assert/strict';
import { readFile, writeFile, rm, readdir, stat } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { api, upload, job, select, waitState } from './helpers.mjs';
import { dshSkill, form } from './dsh-skill.mjs';

async function synchronize(page, data, failure = false) {
  const id = Date.now();
  await writeFile(join(data, 'storage-command.json'), JSON.stringify({ id }));
  for (let i = 0; i < 200; i++) {
    try {
      const reply = JSON.parse(await readFile(join(data, 'storage-response.json'), 'utf8'));
      if (reply.id === id) break;
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (i === 199) throw new Error('本地 COS 同步测试门超时');
    await delay(100);
  }
  const snapshot = await api(page, '/api/state', undefined, 'GET');
  assert.equal(snapshot.run_storage.status, failure ? 'error' : 'ready');
}

async function requestCount(data) {
  try { return JSON.parse(await readFile(join(data, 'dsh-model-requests.json'), 'utf8')).length; }
  catch (error) { if (error.code === 'ENOENT') return 0; throw error; }
}

async function failedImport(page, fixtures, remote) {
  const started = Date.now();
  await writeFile(join(remote, 'fault.json'), JSON.stringify({ put: '/record.json' }));
  await upload(page, '/skills', join(fixtures, 'skill.zip'), '导入 Skill');
  await page.getByText(/未能可靠保存到 COS/).first().waitFor();
  assert.equal((await api(page, '/api/skills', undefined, 'GET')).versions.length, 0);
  await writeFile(join(remote, 'fault.json'), '{}');
  await page.getByRole('button', { name: '结束本批次', exact: true }).click();
  return { fault: 'resource-record-put-denied', milliseconds: Date.now() - started };
}

async function inspectRestored(page, data, entry) {
  const root = join(data, 'runs', entry.key);
  const artifact = await api(page, `/api/artifact?key=${entry.key}&path=results/demo-ok.json`, undefined, 'GET');
  assert.deepEqual(JSON.parse(artifact.text), entry.answer);
  await page.goto(`/?run=${entry.key}&tab=report`);
  await page.getByText(entry.cfg.skill_version, { exact: false }).first().waitFor();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载完整报告', exact: true }).click();
  const raw = await readFile(await (await download).path(), 'utf8');
  assert.ok(raw.includes(entry.cfg.skill_version));
  const files = await readdir(join(root, 'raw'));
  const sessions = files.filter((name) => name.includes('.session-'));
  assert.ok(sessions.length > 0);
  for (const name of [...sessions, 'demo-ok.model-inputs.json', 'demo-ok.skill-integrity.json']) {
    const reply = await api(page, `/api/artifact?key=${entry.key}&path=raw/${name}`, undefined, 'GET');
    assert.ok(reply.text.length > 0);
    assert.ok(!reply.text.includes('dsh-local-only'));
  }
  const execution = JSON.parse(await readFile(join(root, 'raw/demo-ok.execution.json'), 'utf8'));
  const command = execution.command_without_credentials;
  await assert.rejects(stat(resolve(command[command.indexOf('/work') - 1], '..')), { code: 'ENOENT' });
  await page.goto(`/?run=${entry.key}&tab=overview`);
  await page.getByText('分步执行、恢复与导出', { exact: true }).click();
  await select(page, '执行范围', 'demo-ok');
  const response = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '从已有输出恢复结果', exact: true }).click();
  const recovered = await (await response).json();
  await waitState(page, (s) => job(s, recovered.job_id)?.status === 'succeeded', 'COS 恢复后原批次零调用恢复');
  assert.deepEqual(JSON.parse(await readFile(join(root, 'results/demo-ok.json'), 'utf8')), entry.answer);
}

export async function skillPersistence({ page, fixtures, restart }) {
  const started = Date.now();
  const data = resolve(fixtures, '..');
  const remote = resolve(data, '../local-cos');
  const failure = await failedImport(page, fixtures, remote);
  await dshSkill({ page, fixtures });
  const versions = (await api(page, '/api/skills', undefined, 'GET')).versions;
  const entries = JSON.parse(await readFile(resolve(fixtures, '../../dsh-skill.json'), 'utf8'));
  const calls = await requestCount(data);
  await writeFile(join(remote, 'fault.json'), JSON.stringify({ put: '/manifests/state/queue.json' }));
  const faultStarted = Date.now();
  await synchronize(page, data, true);
  await page.reload();
  await page.getByText(/COS 运行数据同步失败/).first().waitFor();
  const faultDuration = Date.now() - faultStarted;
  await writeFile(join(remote, 'fault.json'), '{}');
  await synchronize(page, data);
  // Replace only confirmed resource/run/queue data. Fixtures and call counters are test instrumentation.
  await restart(async () => {
    for (const name of ['skill-assets', 'runs', 'ui-state', 'case-assets', 'case-repositories', 'datasets', 'repos']) {
      await rm(join(data, name), { recursive: true, force: true });
    }
  });
  await page.goto('/skills');
  await page.getByText('COS 保存确认后可选', { exact: false }).waitFor();
  assert.deepEqual((await api(page, '/api/skills', undefined, 'GET')).versions, versions);
  for (const entry of entries) await inspectRestored(page, data, entry);
  await form(page, '案例集原始评分', versions[0]);
  const response = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
  const created = await (await response).json();
  await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', '恢复后版本仍可新建');
  await delay(2000);
  assert.equal(await requestCount(data), calls);
  await writeFile(resolve(fixtures, '../../skill-persistence.json'), JSON.stringify({
    milliseconds: Date.now() - started, failure, runBackupFaultMilliseconds: faultDuration,
    callsBeforeReplacement: calls, callsAfterRestoreAndView: await requestCount(data),
    boundary: 'Filesystem S3 substitute ignoring conditional headers, append-only Skill publications, native Linux DSH, loopback models/judges, service restart and confirmed-data replacement; no real COS or models, no Docker image acceptance.',
  }, null, 2));
}
