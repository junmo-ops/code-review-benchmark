import assert from 'node:assert/strict';
import { join } from 'node:path';
import { writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { state, waitState, upload, createRun, job } from './helpers.mjs';
import { scenarios as regression } from './scenarios.mjs';
import { journeys } from './journeys.mjs';
import { continuity } from './continuity.mjs';
import { importHistory } from './import-history.mjs';
import { skillUploadRecovery } from './skill-assets.mjs';
import { dshRecovery } from './dsh-recovery.mjs';
import { skillPersistence } from './skill-persistence.mjs';

async function draft(page, name) {
  await page.getByRole('button', { name: /新增模型/ }).click();
  await page.getByLabel('显示名称', { exact: true }).fill(name);
  await page.getByLabel('模型 ID', { exact: true }).fill('e2e-success');
  await page.getByLabel('接口根地址（Base URL）', { exact: true }).fill('http://127.0.0.1:9999/v1');
  await page.getByLabel('API Key', { exact: true }).fill('e2e-dummy');
}
function latch() {
  let release;
  const promise = new Promise((resolve) => { release = resolve; });
  return { promise, release };
}
async function staleSave({ page }) {
  await page.goto('/models');
  const held = latch(), received = latch();
  await page.route('**/api/profiles', async (route) => {
    const reply = await route.fetch();
    received.release();
    await held.promise;
    await route.fulfill({ response: reply });
  });
  try {
    await draft(page, '慢响应旧表单');
    await page.getByRole('button', { name: '保存配置', exact: true }).click();
    await received.promise;
    await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
    await draft(page, '重开后新表单');
    held.release();
    await page.waitForResponse((r) => r.url().endsWith('/api/profiles'));
    await delay(500);
    assert.equal(await page.getByRole('dialog').count(), 1, '旧保存响应关闭了新表单');
    assert.equal(await page.getByLabel('显示名称', { exact: true }).inputValue(), '重开后新表单');
  } finally { held.release(); await page.unroute('**/api/profiles'); }
}
async function staleTestError({ page }) {
  await page.goto('/models');
  const held = latch(), received = latch();
  await page.route('**/api/profiles/test', async (route) => {
    received.release(); await held.promise;
    await route.fulfill({ status: 503, json: { error: '旧表单连通性失败' } });
  });
  try {
    await draft(page, '旧测试表单');
    await page.getByRole('dialog').getByRole('button', { name: '测试连通性', exact: true }).click();
    await received.promise;
    await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
    await draft(page, '新测试表单');
    held.release();
    await page.waitForResponse((r) => r.url().endsWith('/api/profiles/test'));
    await delay(300);
    assert.equal(await page.getByRole('dialog').getByText('旧表单连通性失败', { exact: true }).count(), 0, '旧请求错误污染新表单');
  } finally { held.release(); await page.unroute('**/api/profiles/test'); }
}
async function editedDuringTest({ page }) {
  await page.goto('/models');
  const held = latch(), received = latch();
  await page.route('**/api/profiles/test', async (route) => {
    received.release(); await held.promise;
    await route.fulfill({ status: 503, json: { error: '旧字段连通性失败' } });
  });
  try {
    await draft(page, '测试时修改字段');
    await page.getByRole('dialog').getByRole('button', { name: '测试连通性', exact: true }).click();
    await received.promise;
    await page.getByLabel('模型 ID', { exact: true }).fill('e2e-changed');
    held.release();
    await page.waitForResponse((r) => r.url().endsWith('/api/profiles/test'));
    await delay(300);
    assert.equal(await page.getByText('旧字段连通性失败', { exact: true }).count(), 0);
  } finally { held.release(); await page.unroute('**/api/profiles/test'); }
}
async function deleteFailure({ page }, html = false) {
  await page.goto('/models');
  await page.route('**/api/profiles/*', (route) => route.request().method() === 'DELETE'
    ? route.fulfill(html ? { status: 503, contentType: 'text/html', body: '<h1>Unavailable</h1>' }
      : { status: 503, json: { error: '注入删除暂不可用' } }) : route.continue());
  try {
    const card = page.locator('.ant-card').filter({ has: page.getByText('回归-success', { exact: true }) });
    await card.getByRole('button', { name: /删\s*除/ }).click();
    await page.getByRole('dialog').getByRole('button', { name: /删\s*除/ }).click();
    await page.getByRole('dialog').getByText(html ? /HTTP 503.*非 JSON/ : '注入删除暂不可用', { exact: true }).waitFor({ timeout: 3000 });
    assert.equal(await page.getByRole('dialog').getByRole('button', { name: /取\s*消/ }).isEnabled(), true);
  } finally { await page.unroute('**/api/profiles/*'); }
}
async function staleStorage({ page, fixtures }, expired = false) {
  await page.goto('/repositories');
  await page.evaluate((value) => {
    localStorage.removeItem('bench.upload.batch.v1.repository');
    localStorage.setItem('bench.upload.session.v1.repository', value);
  }, expired ? JSON.stringify({ id: 'a'.repeat(32), filename: 'old.zip', size: 1, fingerprint: 'b'.repeat(64), status: 'receiving' }) : '{broken-json');
  try {
    await upload(page, '/repositories', join(fixtures, 'repository.zip'), '导入仓库');
    await page.getByText(/所选文件导入完成/).waitFor({ timeout: 8000 });
  } finally {
    await page.evaluate(() => {
      localStorage.removeItem('bench.upload.session.v1.repository');
      localStorage.removeItem('bench.upload.batch.v1.repository');
    });
  }
}
async function lostCompletion({ page, fixtures }) {
  await page.goto('/repositories');
  await page.evaluate(() => localStorage.removeItem('bench.upload.batch.v1.repository'));
  let once = true;
  await page.route('**/api/uploads/*/complete', async (route) => {
    if (!once) return route.continue();
    once = false;
    await route.fetch();
    await route.abort('connectionreset');
  });
  try {
    await upload(page, '/repositories', join(fixtures, 'repository.zip'), '导入仓库');
    await page.getByText(/Failed to fetch|连接中断|网络/).first().waitFor();
    await page.locator('input[type=file]').setInputFiles(join(fixtures, 'repository.zip'));
    await page.getByRole('button', { name: '继续上传并导入', exact: true }).click();
    await page.getByText(/所选文件导入完成/).waitFor({ timeout: 8000 });
  } finally { await page.unroute('**/api/uploads/*/complete'); }
}
async function lostChunk({ page, fixtures }) {
  await page.goto('/repositories');
  await page.evaluate(() => {
    localStorage.removeItem('bench.upload.session.v1.repository');
    localStorage.removeItem('bench.upload.batch.v1.repository');
  });
  let once = true;
  await page.route('**/api/uploads/*/chunks/*', async (route) => {
    if (!once) return route.continue();
    once = false; await route.fetch(); await route.abort('connectionreset');
  });
  try {
    await upload(page, '/repositories', join(fixtures, 'repository.zip'), '导入仓库');
    await page.getByText(/上传连接中断/).first().waitFor();
    await page.reload();
    await page.locator('input[type=file]').setInputFiles(join(fixtures, 'repository.zip'));
    await page.getByRole('button', { name: '继续上传并导入', exact: true }).click();
    await page.getByText(/所选文件导入完成/).waitFor({ timeout: 10000 });
  } finally { await page.unroute('**/api/uploads/*/chunks/*'); }
}
async function duplicateSave({ page }) {
  await page.goto('/models');
  await draft(page, '重复点击验证');
  const button = page.getByRole('button', { name: '保存配置', exact: true });
  await button.dblclick();
  await waitState(page, (s) => s.profiles.some((p) => p.label === '重复点击验证'), '双击保存完成');
  assert.equal((await state(page)).profiles.filter((p) => p.label === '重复点击验证').length, 1);
}
async function jsonRecovery({ page }) {
  await page.goto('/models');
  let failures = 2;
  await page.route('**/api/state', (route) => failures-- > 0
    ? route.fulfill({ status: 503, contentType: 'text/html', body: '<h1>Unavailable</h1>' }) : route.continue());
  try {
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await page.getByText(/HTTP 503.*非 JSON/).waitFor();
    await page.getByText(/HTTP 503.*非 JSON/).waitFor({ state: 'hidden', timeout: 15000 });
    await page.getByText('服务已连接', { exact: true }).waitFor();
  } finally { await page.unroute('**/api/state'); }
}
async function malformedState({ page }) {
  await page.goto('/models');
  let once = true;
  await page.route('**/api/state', (route) => {
    if (!once) return route.continue();
    once = false;
    return route.fulfill({ status: 200, json: { unexpected: 'gateway response' } });
  });
  try {
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await page.getByText(/工作台状态响应格式无效/).waitFor({ timeout: 10000 });
    await page.getByText(/工作台状态响应格式无效/).waitFor({ state: 'hidden', timeout: 10000 });
    await page.getByText('服务已连接', { exact: true }).waitFor();
  } finally { await page.unroute('**/api/state'); }
}
async function multiTab({ page }) {
  const second = await page.context().newPage();
  try {
    await page.goto('/models'); await second.goto('/models');
    await draft(page, '多标签模型');
    await page.getByRole('button', { name: '保存配置', exact: true }).click();
    await waitState(page, (s) => s.profiles.some((p) => p.label === '多标签模型'), '多标签新增');
    const card = second.locator('.ant-card').filter({ has: second.getByText('多标签模型', { exact: true }) });
    await card.getByRole('button', { name: /编\s*辑/ }).click();
    await second.getByLabel('显示名称', { exact: true }).fill('多标签已修改');
    await second.getByRole('button', { name: '保存配置', exact: true }).click();
    await page.getByText('多标签已修改', { exact: true }).waitFor({ timeout: 10000 });
  } finally { await second.close(); }
}
async function abruptRestart({ page, fixtures, restart }) {
  const created = await createRun(page, 'slow');
  await waitState(page, (s) => s.runs.find((r) => r.key === created.key)?.completed === 1, '宕机前保存首例');
  const answer = join(fixtures, '../runs', created.key, 'results/demo-ok.json');
  const before = await (await import('node:fs/promises')).readFile(answer, 'utf8');
  await restart();
  await page.reload();
  await waitState(page, (s) => s.recovery_required && job(s, created.job_id)?.status === 'interrupted', '强制终止后恢复任务记录');
  await delay(1000);
  assert.equal((await state(page)).jobs.filter((j) => j.status === 'running').length, 0, '重启后不得自动重放收费任务');
  await page.getByRole('button', { name: '继续未完成案例并评分', exact: true }).click();
  const response = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.getByRole('dialog').last().getByRole('button', { name: '开始执行', exact: true }).click();
  const resumed = await (await response).json();
  await waitState(page, (s) => job(s, resumed.job_id)?.status === 'succeeded', '真实进程重启后完成续跑');
  assert.equal(await (await import('node:fs/promises')).readFile(answer, 'utf8'), before);
}
async function soak({ page, fixtures }) {
  const seconds = Math.min(300, Math.max(15, Number(process.env.E2E_SOAK_SECONDS || 60)));
  const started = Date.now(), requests = [], samples = [];
  const created = [];
  for (let i = 0; i < 4; i++) created.push(await createRun(page, 'success'));
  const routes = [['/models', '模型配置'], ['/cases', '案例集管理'], ['/repositories', '仓库管理'],
    ['/jobs', '执行记录'], ['/', '单模型测评'], ['/comparisons', '结果对比'], ['/experiments', '多模型实验']];
  await page.goto('/models');
  await page.getByText('服务已连接', { exact: true }).waitFor();
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('HeapProfiler.collectGarbage');
  const baselineHeap = await cdp.send('Runtime.getHeapUsage');
  let iteration = 0;
  while (Date.now() - started < seconds * 1000) {
    const before = Date.now();
    const snapshot = await state(page);
    requests.push(Date.now() - before);
    assert.ok(snapshot.jobs.filter((j) => ['running', 'cancelling'].includes(j.status)).length <= 1);
    const [url, label] = routes[(iteration * 13 + 7) % routes.length];
    await page.getByRole('menuitem', { name: new RegExp(label) }).click();
    await page.waitForURL((location) => location.pathname === url);
    if (iteration > 0 && iteration % 25 === 0) await page.reload();
    await page.getByText('服务已连接', { exact: true }).waitFor();
    samples.push(await page.evaluate(() => ({ domNodes: document.querySelectorAll('*').length,
      heap: performance.memory?.usedJSHeapSize ?? null })));
    iteration++;
    await delay(150);
  }
  await waitState(page, (s) => created.every((c) => job(s, c.job_id)?.status === 'succeeded'), '压力运行任务全部结束');
  const after = await state(page);
  for (const entry of created) assert.equal(after.runs.find((r) => r.key === entry.key).completed, 2);
  await page.getByRole('menuitem', { name: /模型配置/ }).click();
  await page.waitForURL('**/models');
  const beforeGC = await cdp.send('Runtime.getHeapUsage');
  await cdp.send('HeapProfiler.collectGarbage');
  const retainedHeap = await cdp.send('Runtime.getHeapUsage');
  const domAfterGC = await cdp.send('Memory.getDOMCounters');
  await cdp.detach();
  await writeFile(join(fixtures, '../../soak.json'), JSON.stringify({ seconds, iteration, requests, samples,
    baselineHeap, beforeGC, retainedHeap, domAfterGC }, null, 2));
}
export const exploration = [
  ...regression.slice(0, 4).map(([name, fn]) => ['setup-' + name, fn]),
  ...journeys,
  ...continuity,
  ['refresh-and-import-another-resource', importHistory],
  ['close-reopen-during-model-save', staleSave], ['old-connectivity-error-new-form', staleTestError], ['edit-fields-during-connectivity-test', editedDuringTest],
  ['model-delete-503-feedback', deleteFailure], ['model-delete-html-feedback', (ctx) => deleteFailure(ctx, true)],
  ['corrupt-upload-local-storage', staleStorage], ['expired-upload-session', (ctx) => staleStorage(ctx, true)],
  ['lost-import-completion-response', lostCompletion], ['lost-chunk-response-resume', lostChunk],
  ['rapid-double-save', duplicateSave], ['html-503-polling-recovery', jsonRecovery],
  ['malformed-state-response-recovery', malformedState], ['multi-tab-model-edit', multiTab], ['abrupt-server-exit-recovery', abruptRestart], ['bounded-queue-navigation-soak', soak],
  ['skill-upload-response-loss-recovery', skillUploadRecovery],
  ...(process.env.E2E_DSH === '1' ? [['dsh-fifty-cancel-restart-zero-call-recovery', dshRecovery]] : []),
  ...(process.env.E2E_SKILL_COS === '1' ? [['skill-cos-persistence', skillPersistence]] : []),
];
