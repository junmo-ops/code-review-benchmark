import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { scenarios } from './scenarios.mjs';
import { continuity } from './continuity.mjs';
import { exploration } from './exploration.mjs';
import { upload, createRun, waitState, job } from './helpers.mjs';
import { importHistory } from './import-history.mjs';
async function largeRepository({ page, fixtures }) {
  const metadata = JSON.parse(await readFile(join(fixtures, '../server.json'), 'utf8'));
  assert.equal(metadata.memory_limit_bytes, 2147483648, 'Linux cgroup 未限制为 2 GiB');
  await upload(page, '/repositories', join(fixtures, 'large-repository.zip'), '导入仓库');
  await page.getByText(/所选文件导入完成/).waitFor({ timeout: 180000 });
  await upload(page, '/cases', join(fixtures, 'large-cases.zip'), '确认导入');
  await waitState(page, (s) => s.catalog.some((c) => c.datasets.some((d) => d.name === '浏览器回归大仓库')), '大仓库案例配置导入', 120);
  const created = await createRun(page, 'slow', '浏览器回归大仓库', 2);
  const snapshot = await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', '带大文件的真实 Git 工作区检视', 120);
  assert.equal(snapshot.runs.find((r) => r.key === created.key).completed, 2);
  assert.deepEqual(await readdir(join(fixtures, '../runs', created.key, 'workspaces')), []);
}
const take = (name) => exploration.find(([key]) => key === name);
export const containerScenarios = [
  ...scenarios.slice(0, 4).map(([name, fn]) => ['setup-' + name, fn]),
  ...continuity,
  ['refresh-and-import-another-resource', importHistory],
  ['container-large-repository', largeRepository],
  take('abrupt-server-exit-recovery'),
  take('bounded-queue-navigation-soak'),
];
