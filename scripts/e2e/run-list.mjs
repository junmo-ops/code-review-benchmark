import assert from 'node:assert/strict';
import { cp, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { state } from './helpers.mjs';

export async function runPagination({ page, fixtures }) {
  const snapshot = await state(page);
  const source = snapshot.runs.find((run) => run.metrics?.f1 === 1);
  assert.ok(source, '分页验收需要已完成的离线测评');
  const root = join(fixtures, '..');
  const labelsPath = join(root, 'ui-state/display-names.json');
  const labels = JSON.parse(await readFile(labelsPath, 'utf8').catch(() => '{}'));
  for (let index = 0; index < 12; index++) {
    const key = `pagination-${String(index).padStart(2, '0')}`;
    await cp(join(root, 'runs', source.key), join(root, 'runs', key), { recursive: true });
    labels[key] = `分页验证-${String(index).padStart(2, '0')}`;
  }
  await writeFile(labelsPath, JSON.stringify(labels));
  await page.goto('/');
  await page.getByPlaceholder('搜索批次或模型').fill('分页验证');
  await page.getByText('测评批次 · 12', { exact: true }).waitFor();
  assert.equal(await page.locator('.ant-table-tbody > tr[data-row-key]').count(), 10);
  await page.locator('.ant-pagination-next button').click();
  await page.getByRole('button', { name: '分页验证-00', exact: true }).waitFor();
  assert.equal(await page.locator('.ant-table-tbody > tr[data-row-key]').count(), 2);
  await page.getByRole('button', { name: '分页验证-00', exact: true }).click();
  await page.waitForURL((url) => url.searchParams.get('run') === 'pagination-00');
}

export async function listPollingRecovery({ page }) {
  await page.goto('/');
  await page.getByRole('columnheader', { name: '批次 / 案例集' }).first().waitFor();
  await page.waitForResponse((response) => response.url().includes('/api/runs?') && response.ok());
  const before = await page.locator('.ant-table-tbody > tr[data-row-key]').count();
  let failed = false;
  await page.route('**/api/runs?**', (route) => {
    if (failed) return route.continue();
    failed = true;
    return route.fulfill({ status: 503, json: { error: '列表故障注入' } });
  });
  try {
    await page.getByText('列表故障注入', { exact: true }).waitFor({ timeout: 10000 });
    assert.equal(await page.locator('.ant-table-tbody > tr[data-row-key]').count(), before);
    await page.getByText('列表故障注入', { exact: true }).waitFor({ state: 'hidden', timeout: 10000 });
  } finally {
    await page.unroute('**/api/runs?**');
  }
}
