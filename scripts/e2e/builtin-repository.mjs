import assert from 'node:assert/strict';
import { readFile, writeFile, rm, stat } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { api, job, select, waitState } from './helpers.mjs';
import { form, importResource } from './dsh-skill.mjs';

async function create(page, version) {
  await form(page, '案例集原始评分', version);
  await select(page, '案例范围', 'pilot1.jsonl');
  const response = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
  const reply = await response;
  assert.equal(reply.status(), 202);
  const created = await reply.json();
  const snapshot = await waitState(page, (s) => ['failed', 'succeeded'].includes(job(s, created.job_id)?.status), '内置案例仓库准备', 120);
  return { created, snapshot, status: job(snapshot, created.job_id).status };
}

export async function builtinRepository({ page, fixtures }, injectFailure = false) {
  const started = Date.now();
  await importResource(page, fixtures, '/repositories', 'repository.zip', '导入仓库');
  await importResource(page, fixtures, '/cases', 'dsh-case.zip', '确认导入');
  await importResource(page, fixtures, '/skills', 'skill.zip', '导入 Skill');
  const version = (await api(page, '/api/skills', undefined, 'GET')).versions[0];
  const data = resolve(fixtures, '..');
  for (const name of ['repos', 'case-assets/objects', 'case-repositories/objects']) {
    await rm(join(data, name), { force: true, recursive: true });
  }
  let faultMilliseconds = 0;
  const control = resolve(data, '../local-cos/fault.json');
  if (injectFailure) {
    const faultStarted = Date.now();
    await writeFile(control, JSON.stringify({ get: '.bundle' }));
    try {
      const failed = await create(page, version);
      assert.equal(failed.status, 'failed');
      await assert.rejects(stat(join(data, 'runs', failed.created.key, 'run-config.json')), { code: 'ENOENT' });
    } finally { await writeFile(control, '{}'); }
    faultMilliseconds = Date.now() - faultStarted;
  }
  const { created, snapshot, status } = await create(page, version);
  assert.equal(status, 'succeeded', JSON.stringify(job(snapshot, created.job_id)));
  await stat(join(data, 'repos/e2e__demo/.git'));
  const cfg = JSON.parse(await readFile(join(data, 'runs', created.key, 'run-config.json'), 'utf8'));
  assert.equal(cfg.skill_version, version.id);
  assert.equal(snapshot.runs.find((r) => r.key === created.key).completed, 0);
  const checked = await api(page, '/api/jobs', { action: 'check', key: created.key,
    profile: snapshot.runs.find((r) => r.key === created.key).profile });
  const checkedState = await waitState(page, (s) => ['failed', 'succeeded'].includes(job(s, checked.job_id)?.status), '实际 DSH 提交检查', 120);
  assert.equal(job(checkedState, checked.job_id).status, 'succeeded', JSON.stringify(job(checkedState, checked.job_id)));
  await assert.rejects(stat(join(data, 'dsh-model-requests.json')), { code: 'ENOENT' });
  await writeFile(resolve(fixtures, '../../builtin-repository.json'), JSON.stringify({
    key: created.key, milliseconds: Date.now() - started, faultMilliseconds,
    boundary: 'Built-in selection ID with isolated fixture case bytes, real Git bundle, filesystem S3 substitute, native DSH freeze/check, zero model calls; no real COS or Kylin image.',
  }, null, 2));
}
