import assert from 'node:assert/strict';
import { reportSummaryScenario } from './report-summary.mjs';
import { join } from 'node:path';
import { writeFile, readFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { api, state, waitState, upload, closeDialog, createRun, job } from './helpers.mjs';

async function models({ page }) {
  await page.goto('/models');
  await page.getByRole('button', { name: /新增模型/ }).click();
  await page.getByLabel('显示名称', { exact: true }).fill('自动回归新增模型');
  await page.getByLabel('模型 ID', { exact: true }).fill('e2e-success');
  await page.getByLabel('接口根地址（Base URL）', { exact: true }).fill('http://127.0.0.1:9999/v1');
  await page.getByLabel('API Key', { exact: true }).fill('e2e-dummy');
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  const card = page.locator('.ant-card').filter({ has: page.getByText('自动回归新增模型', { exact: true }) });
  await card.getByRole('button', { name: /编\s*辑/ }).click();
  await page.getByLabel('显示名称', { exact: true }).fill('自动回归修改模型');
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  await page.reload();
  const edited = page.locator('.ant-card').filter({ has: page.getByText('自动回归修改模型', { exact: true }) });
  await edited.getByRole('button', { name: /删\s*除/ }).click();
  await page.getByRole('dialog').getByRole('button', { name: /删\s*除/ }).click();
  await waitState(page, (s) => !s.profiles.some((p) => p.label === '自动回归修改模型'), '模型删除');
}
async function theme({ page }) {
  await page.goto('/');
  const toggle = page.getByRole('switch', { name: '暗色主题' });
  assert.equal(await toggle.getAttribute('aria-checked'), 'false');
  await toggle.click();
  assert.equal(await toggle.getAttribute('aria-checked'), 'true');
  await page.reload();
  assert.equal(await toggle.getAttribute('aria-checked'), 'false');
  for (const url of ['/jobs', '/experiments', '/comparisons', '/guide']) {
    await page.goto(url);
    await page.getByText('服务已连接', { exact: true }).waitFor();
  }
}
async function repositories({ page, fixtures }) {
  const files = ['repository.zip', 'repository-copy.zip'].map((name) => join(fixtures, name));
  await page.goto('/repositories');
  let release;
  let observed;
  const received = new Promise((resolve) => { observed = resolve; });
  const held = new Promise((resolve) => { release = resolve; });
  await page.route('**/api/uploads/*/chunks/0', async (route) => {
    observed();
    await held;
    await route.continue().catch(() => {});
  });
  await page.locator('input[type=file]').setInputFiles(files);
  await page.getByRole('button', { name: '导入仓库', exact: true }).click();
  await Promise.race([received, delay(15000).then(() => { throw new Error('上传分块请求未发出'); })]);
  await page.reload();
  release();
  await page.unroute('**/api/uploads/*/chunks/0');
  await page.getByText(/发现未完成上传/).waitFor();
  await page.locator('input[type=file]').setInputFiles(files);
  await page.getByRole('button', { name: '继续上传并导入', exact: true }).click();
  await waitState(page, (s) => s.case_assets?.repositories?.some((r) => r.slug === 'e2e__demo'), '仓库导入');
  await page.getByText(/本批次已导入 2 \/ 2/).waitFor({ timeout: 30000 });
  await page.reload();
  await page.getByText('e2e/demo', { exact: true }).first().waitFor();
  assert.equal(await page.locator('input[type=file]').isEnabled(), true);
}
async function cases({ page, fixtures }) {
  await upload(page, '/cases', join(fixtures, 'cases.zip'), '确认导入');
  await waitState(page, (s) => s.catalog.some((c) => c.datasets.some((d) => d.name === '浏览器回归案例')), '案例导入');
  await page.reload();
  const row = page.getByRole('row').filter({ hasText: '浏览器回归案例' });
  await row.getByRole('button', { name: /编\s*辑/ }).click();
  await page.getByLabel('案例集名称', { exact: true }).fill('浏览器回归案例-已修改');
  await page.getByRole('dialog').getByRole('button', { name: /确\s*定/ }).click();
  await page.getByRole('row').filter({ hasText: '浏览器回归案例-已修改' }).waitFor();
  await row.getByRole('button', { name: /编\s*辑/ }).click();
  await page.getByLabel('案例集名称', { exact: true }).fill('浏览器回归案例');
  await page.getByRole('dialog').getByRole('button', { name: /确\s*定/ }).click();
  await page.getByRole('row').filter({ hasText: '浏览器回归案例' }).waitFor();
}
async function errors({ page, fixtures }) {
  await upload(page, '/cases', join(fixtures, 'missing.zip'), '确认导入');
  await page.getByText(/案例提交校验失败|缺少案例提交/).first().waitFor({ timeout: 30000 });
  // Navigate via the SPA menu, not a full reload, to detect cross-page errors.
  await page.getByRole('menuitem', { name: /仓库管理/ }).click();
  await page.waitForURL('**/repositories');
  await page.getByText(/案例提交校验失败|缺少案例提交/).waitFor({ state: 'hidden' });
  await page.getByRole('row').filter({ hasText: 'e2e/demo' }).getByRole('button', { name: /删\s*除/ }).click();
  await page.getByRole('dialog').getByRole('button', { name: '确认删除', exact: true }).click();
  await page.getByRole('dialog').getByText(/案例集.*引用|案例集.*使用/).first().waitFor();
  await page.getByRole('dialog').getByRole('button', { name: /取\s*消/ }).click();
}
async function workflow({ page }) {
  const created = await createRun(page, 'success');
  const done = await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', '完整检视并评分');
  const run = done.runs.find((r) => r.key === created.key);
  assert.equal(run.completed, 2);
  assert.equal(run.metrics.f1, 1);
  await page.reload();
  await page.getByRole('tab', { name: '中文报告', exact: true }).click();
  await page.getByText(/模拟裁判结果/).waitFor();
}
async function queue({ page, fixtures }) {
  const first = await createRun(page, 'slow');
  await waitState(page, (s) => job(s, first.job_id)?.step === 'review', '进入长任务检视');
  await closeDialog(page);
  const second = await createRun(page, 'success');
  const queued = await state(page);
  assert.equal(job(queued, second.job_id).status, 'queued');
  await page.getByRole('button', { name: /取消排队|停止任务/ }).click();
  await waitState(page, (s) => job(s, second.job_id)?.status === 'cancelled', '取消排队');
  // Import must stay available while a benchmark is running.
  await upload(page, '/repositories', join(fixtures, 'repository.zip'), '导入仓库');
  await waitState(page, (s) => job(s, first.job_id)?.status === 'succeeded', '运行中的任务完成');
}
async function partial({ page }) {
  const created = await createRun(page, 'partial');
  const done = await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', '部分失败继续评分');
  const run = done.runs.find((r) => r.key === created.key);
  assert.equal(run.completed, 1);
  assert.equal(run.metrics.f1, 1);
  assert.match(job(done, created.job_id).message, /部分案例/);
  const log = await api(page, `/api/log?job=${created.job_id}`, undefined, 'GET');
  assert.match(log.text, /MOCK judge/);
}
async function retry({ page }) {
  const created = await createRun(page, 'retry');
  const done = await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', '504 自动重试');
  assert.equal(done.runs.find((r) => r.key === created.key).completed, 2);
  const log = await api(page, `/api/log?job=${created.job_id}`, undefined, 'GET');
  assert.match(log.text, /自动重试/);
}
async function recovery({ page, fixtures }) {
  const created = await createRun(page, 'slow');
  await waitState(page, (s) => s.runs.find((r) => r.key === created.key)?.completed === 1, '首个案例答卷保存');
  const saved = join(fixtures, '../runs', created.key, 'results/demo-ok.json');
  const before = await readFile(saved, 'utf8');
  await writeFile(join(fixtures, '../interrupt-worker'), 'interrupt');
  await waitState(page, (s) => job(s, created.job_id)?.status === 'interrupted' && s.recovery_required, 'Worker 中断持久化');
  await page.reload();
  await page.getByRole('button', { name: '继续未完成案例并评分', exact: true }).click();
  const response = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.getByRole('dialog').last().getByRole('button', { name: '开始执行', exact: true }).click();
  const resumed = await (await response).json();
  await waitState(page, (s) => job(s, resumed.job_id)?.status === 'succeeded' &&
    s.runs.find((r) => r.key === created.key)?.completed === 2 && !s.recovery_required,
    '明确确认后继续未完成案例');
  assert.equal(await readFile(saved, 'utf8'), before);
}
async function deletion({ page }) {
  await page.goto('/cases');
  await page.getByRole('row').filter({ hasText: '浏览器回归案例' }).getByRole('button', { name: /删\s*除/ }).click();
  await page.getByRole('dialog').getByRole('button', { name: '确认删除', exact: true }).click();
  await waitState(page, (s) => !s.catalog.some((c) => c.datasets.some((d) => d.name === '浏览器回归案例')), '案例删除');
  await page.goto('/repositories');
  await page.getByRole('row').filter({ hasText: 'e2e/demo' }).getByRole('button', { name: /删\s*除/ }).click();
  await page.getByRole('dialog').getByRole('button', { name: '确认删除', exact: true }).click();
  await waitState(page, (s) => !s.case_assets.repositories.some((r) => r.slug === 'e2e__demo'), '历史批次保留但仓库可删除');
  const history = await state(page);
  assert.ok(history.runs.length >= 4);
  const scored = history.runs.find((r) => r.metrics?.f1 === 1);
  await page.goto(`/?run=${scored.key}&tab=report`);
  await page.getByText(/模拟裁判结果/).waitFor();
}
export const scenarios = [
  ['theme-and-navigation', theme], ['model-crud', models], ['repository-refresh', repositories], ['case-crud', cases],
  ['error-isolation-and-delete-feedback', errors], ['full-workflow', workflow], ['report-summary-and-details', reportSummaryScenario],
  ['queue-cancel-and-import-during-run', queue], ['partial-failure-scoring', partial],
  ['504-retry', retry], ['worker-interruption-resume', recovery], ['resource-deletion-history', deletion],
];
