import assert from 'node:assert/strict';
import { scaledQueue } from './scaled-queue.mjs';
import { readFile, writeFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { createRun, job, waitState, state, closeDialog, upload, select } from './helpers.mjs';

async function confirmRun(page) {
  const reply = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.getByRole('dialog').last().getByRole('button', { name: '开始执行', exact: true }).click();
  const response = await reply;
  const body = await response.json();
  assert.equal(response.status(), 202, JSON.stringify(body));
  return body;
}
async function stoppedContinue({ page, fixtures }) {
  const created = await createRun(page, 'slow');
  await waitState(page, (s) => s.runs.find((r) => r.key === created.key)?.completed === 1, '主动停止前首例完成');
  const result = join(fixtures, '../runs', created.key, 'results/demo-ok.json');
  const before = await readFile(result, 'utf8');
  await page.getByRole('button', { name: '停止任务', exact: true }).click();
  await waitState(page, (s) => job(s, created.job_id)?.status === 'cancelled', '主动停止完成');
  const other = await createRun(page, 'slow');
  await waitState(page, (s) => job(s, other.job_id)?.step === 'review', '另一批次正在运行');
  await page.goto('/?run=' + created.key);
  await page.getByRole('button', { name: '继续未完成案例并评分', exact: true }).click();
  const resumed = await confirmRun(page);
  assert.equal(job(await state(page), resumed.job_id).status, 'queued', '主动停止的批次不能在其他任务运行时排队续跑');
  await waitState(page, (s) => job(s, resumed.job_id)?.status === 'succeeded' &&
    s.runs.find((r) => r.key === created.key)?.completed === 2, '主动停止后原批次续跑');
  assert.equal(await readFile(result, 'utf8'), before, '完整答卷被重复生成');
  const attempts = join(fixtures, '../runs', created.key, 'raw/demo-ok.mock-attempts.json');
  assert.equal(JSON.parse(await readFile(attempts, 'utf8')), 1, '续跑重复调用了已完成案例');
  assert.deepEqual(await readdir(join(fixtures, '../runs', created.key, 'workspaces')), []);
}
async function editSuccess(page, model) {
  await page.goto('/models');
  const card = page.locator('.ant-card').filter({ has: page.getByText('回归-success', { exact: true }) });
  await card.getByRole('button', { name: /编\s*辑/ }).click();
  await page.getByLabel('模型 ID', { exact: true }).fill(model);
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
}
async function queuedModelSnapshot({ page }) {
  const first = await createRun(page, 'slow');
  await waitState(page, (s) => job(s, first.job_id)?.step === 'review', '首批进入检视');
  await closeDialog(page);
  const second = await createRun(page, 'success');
  assert.equal(job(await state(page), second.job_id).status, 'queued');
  try {
    await editSuccess(page, 'e2e-partial-updated');
    const done = await waitState(page, (s) => job(s, second.job_id)?.status === 'succeeded', '排队任务在模型修改后完成');
    const run = done.runs.find((r) => r.key === second.key);
    assert.equal(run.model, 'e2e-success', '排队任务使用了修改后的模型');
    assert.equal(run.completed, 2);
  } finally { await editSuccess(page, 'e2e-success'); }
}
async function activeResources({ page, fixtures }) {
  const created = await createRun(page, 'slow');
  await waitState(page, (s) => job(s, created.job_id)?.step === 'review', '资源交叉操作时任务正在检视');
  await page.goto('/cases');
  const row = page.getByRole('row').filter({ hasText: '浏览器回归案例' });
  await row.getByRole('button', { name: /删\s*除/ }).click();
  await page.getByRole('dialog').getByRole('button', { name: '确认删除', exact: true }).click();
  await page.getByRole('dialog').getByText(/未结束|排队|运行|执行中/).first().waitFor();
  await closeDialog(page);
  await upload(page, '/repositories', join(fixtures, 'repository-update.zip'), '导入仓库');
  await page.getByText(/所选文件导入完成/).waitFor({ timeout: 20000 });
  const done = await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', '更新仓库后原任务完成');
  assert.equal(done.runs.find((r) => r.key === created.key).completed, 2);
  const versions = done.case_assets.repositories.find((r) => r.slug === 'e2e__demo').versions;
  assert.ok(versions.length >= 2, '没有导入真正不同的仓库版本');
}
async function failedCaseRepair({ page, fixtures }) {
  const created = await createRun(page, 'partial');
  await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', '部分失败后已有答卷评分');
  const root = join(fixtures, '../runs', created.key);
  const before = await readFile(join(root, 'results/demo-ok.json'), 'utf8');
  await writeFile(join(root, 'mock-upstream-recovered'), 'upstream recovered');
  await page.reload();
  await page.getByRole('tab', { name: '概览与案例', exact: true }).click();
  await page.getByText('分步执行、恢复与导出', { exact: true }).click();
  await select(page, '执行范围', 'demo-fail');
  await page.getByRole('switch', { name: '允许重跑所选未完成案例（可能产生费用）', exact: true }).check();
  await page.getByRole('button', { name: /检\s*视/ }).click();
  const repaired = await confirmRun(page);
  await waitState(page, (s) => job(s, repaired.job_id)?.status === 'succeeded' &&
    s.runs.find((r) => r.key === created.key)?.completed === 2, '仅补跑失败案例');
  assert.equal(await readFile(join(root, 'results/demo-ok.json'), 'utf8'), before);
  await page.reload();
  await page.getByRole('tab', { name: '概览与案例', exact: true }).click();
  await page.getByText('分步执行、恢复与导出', { exact: true }).click();
  await page.getByText(/已有部分评分，覆盖 1\/2.*评分证据不可覆盖/).first().waitFor();
  assert.equal(await page.getByRole('button', { name: /评\s*分/, exact: true }).isDisabled(), true);
  const scope = JSON.parse(await readFile(join(root, 'evaluation-scope.json'), 'utf8'));
  assert.deepEqual(scope.included, ['demo-ok'], '已保存的评分范围被补跑隐式覆盖');
  const snapshot = await state(page);
  const run = snapshot.runs.find((r) => r.key === created.key);
  const response = await page.request.post('/api/jobs', { headers: { 'X-Dashboard-Token': 'e2e-local-session' },
    data: { action: 'eval', key: created.key, profile: run.profile } });
  assert.equal(response.status(), 400, '已有评分应在提交时明确拒绝，而不是排队后失败');
  assert.match((await response.json()).error, /评分证据不可覆盖/);
  assert.equal((await state(page)).jobs.length, snapshot.jobs.length, '无效评分请求进入了执行队列');
}
async function internalEndpoints({ page }) {
  await page.goto('/models');
  await page.getByRole('button', { name: /新增模型/ }).click();
  await page.getByLabel('显示名称', { exact: true }).fill('内网模型地址验收');
  await page.getByLabel('模型 ID', { exact: true }).fill('e2e-success');
  await page.getByLabel('接口根地址（Base URL）', { exact: true }).fill('http://127.0.0.1:9/v1');
  await page.getByLabel('API Key', { exact: true }).fill('e2e-dummy');
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  const card = page.locator('.ant-card').filter({ has: page.getByText('内网模型地址验收', { exact: true }) });
  await card.getByRole('button', { name: /编\s*辑/ }).click();
  for (const invalid of ['ftp://gateway/v1', 'https://user:password@gateway/v1', 'https://gateway/v1?token=test', 'https://gateway/v1/chat/completions']) {
    await page.getByLabel('接口根地址（Base URL）', { exact: true }).fill(invalid);
    await page.getByRole('button', { name: '保存配置', exact: true }).click();
    await page.getByText(/请填写有效的 HTTP\/HTTPS 接口根地址/).waitFor();
    assert.equal((await state(page)).profiles.find((p) => p.label === '内网模型地址验收').base_url, 'http://127.0.0.1:9/v1');
  }
  await page.getByLabel('接口根地址（Base URL）', { exact: true }).fill('http://llm-gateway:8080/v1');
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  assert.equal((await state(page)).profiles.find((p) => p.label === '内网模型地址验收').base_url, 'http://llm-gateway:8080/v1');
}
export const continuity = [
  ['user-stop-continue-same-batch', stoppedContinue],
  ['queued-model-config-snapshot', queuedModelSnapshot],
  ['internal-model-endpoint-validation', internalEndpoints],
  ['running-resource-delete-and-version-update', activeResources],
  ['repair-failed-case-score-lock-feedback', failedCaseRepair],
  ['two-fifty-case-queue-and-cancel', scaledQueue],
];
