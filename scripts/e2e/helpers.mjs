import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';

export async function api(page, path, data, method = 'POST') {
  const reply = await page.request.fetch(path, {
    method,
    headers: { 'X-Dashboard-Token': 'e2e-local-session' },
    ...(data === undefined ? {} : { data }),
  });
  const body = await reply.json();
  assert.ok(reply.ok(), `${path}: ${reply.status()} ${JSON.stringify(body)}`);
  return body;
}
export async function state(page) {
  return api(page, '/api/state', undefined, 'GET');
}
export async function waitState(page, predicate, description, seconds = 90) {
  const deadline = Date.now() + seconds * 1000;
  while (Date.now() < deadline) {
    const snapshot = await state(page);
    if (predicate(snapshot)) return snapshot;
    await delay(300);
  }
  throw new Error(`等待超时：${description}`);
}
export async function select(page, label, option) {
  const input = page.getByLabel(label, { exact: true });
  await page.locator('.ant-select').filter({ has: input }).locator('.ant-select-selector').click();
  const id = await input.getAttribute('aria-controls');
  const dropdown = page.locator('.ant-select-dropdown').filter({ has: page.locator(`[id="${id}"]`) });
  await dropdown.locator('.ant-select-item-option-content').filter({ hasText: option }).first().click();
}
export async function upload(page, url, file, button) {
  await page.goto(url);
  await page.locator('input[type=file]').setInputFiles(file);
  await page.getByRole('button', { name: button, exact: true }).click();
}
export async function closeDialog(page) {
  const dialogs = page.getByRole('dialog');
  if (await dialogs.count()) {
    await dialogs.last().getByRole('button', { name: 'Close', exact: true }).click();
  }
}
export async function createRun(page, model, dataset = '浏览器回归案例', caseJobs = 1) {
  await page.goto('/');
  await page.getByRole('button', { name: /新建测评/ }).first().click();
  await select(page, '案例集', 'AACR');
  await select(page, '案例范围', dataset);
  await select(page, '被测模型', `回归-${model}`);
  await select(page, '裁判模型', '回归-success');
  if (caseJobs !== 1) {
    await page.getByText('高级参数', { exact: true }).click();
    await page.getByLabel('案例并发', { exact: true }).fill(String(caseJobs));
  }
  const response = page.waitForResponse((reply) => reply.url().endsWith('/api/jobs') && reply.request().method() === 'POST');
  await page.getByRole('button', { name: '创建并开始', exact: true }).click();
  const reply = await response;
  const body = await reply.json();
  assert.equal(reply.status(), 202, JSON.stringify(body));
  return body;
}
export function job(snapshot, id) {
  return snapshot.jobs.find((entry) => entry.id === id);
}
