import assert from 'node:assert/strict';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { api, upload } from './helpers.mjs';

async function completedImport(page, identifier) {
  for (let attempt = 0; attempt < 100; attempt++) {
    const { task } = await api(page, '/api/cases/imports', undefined, 'GET');
    if (task?.status === 'succeeded' && (!identifier || task.id === identifier)) return task;
    if (task?.status === 'failed') throw new Error(task.message);
    await delay(200);
  }
  throw new Error('后台资源导入未完成');
}
export async function importHistory({ page, fixtures }) {
  // Hold browser progress behind the actual backend, then leave before its final poll.
  let target;
  await page.route('**/api/uploads/*/complete', async (route) => {
    const response = await route.fetch();
    target = (await response.json()).task.id;
    await route.fulfill({ response });
  });
  await page.route('**/api/cases/imports*', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (body.task && body.task.id === target && body.task.status === 'succeeded')
      body.task = { ...body.task, status: 'running', message: '验收注入：终态尚未到达浏览器' };
    await route.fulfill({ response, json: body });
  });
  let previous;
  try {
    const accepted = page.waitForResponse((reply) => /\/api\/uploads\/[a-f0-9]+\/complete$/.test(reply.url()));
    await upload(page, '/cases', join(fixtures, 'cases-50.zip'), '确认导入');
    const { task } = await (await accepted).json();
    previous = await completedImport(page, task.id);
    await page.goto('/models');
  } finally {
    await page.unroute('**/api/cases/imports*');
    await page.unroute('**/api/uploads/*/complete');
  }
  await upload(page, '/repositories', join(fixtures, 'repository-update.zip'), '导入仓库');
  await page.getByText(/所选文件导入完成/).waitFor({ timeout: 30000 });
  const latest = await completedImport(page);
  assert.notEqual(latest.id, previous.id);
  const saved = await api(page, '/api/cases/imports?id=' + previous.id, undefined, 'GET');
  assert.equal(saved.task.status, 'succeeded', '另一类资源导入覆盖了之前的终态');
  await page.goto('/cases');
  await page.getByText('本批次已导入 1 / 1 个 ZIP', { exact: true }).waitFor();
  await page.locator('input[type=file]').setInputFiles(join(fixtures, 'cases.zip'));
  await page.getByRole('button', { name: '确认导入', exact: true }).click();
  await page.getByText(/所选文件导入完成/).waitFor({ timeout: 30000 });
}
