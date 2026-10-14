import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { select, waitState, job, closeDialog } from './helpers.mjs';

async function runDraft(page, name) {
  await page.getByRole('button', { name: /新建测评/ }).first().click();
  await select(page, '案例集', 'AACR');
  await select(page, '案例范围', '浏览器回归案例');
  await select(page, '被测模型', '回归-success');
  await select(page, '裁判模型', '回归-success');
  await page.getByLabel('测评名称（选填）', { exact: true }).fill(name);
}
function gate() {
  let release;
  const promise = new Promise((resolve) => { release = resolve; });
  return { promise, release };
}
async function reopenCreate({ page }) {
  await page.goto('/');
  const received = gate(), held = gate();
  await page.route('**/api/jobs', async (route) => {
    const reply = await route.fetch();
    received.release(); await held.promise;
    await route.fulfill({ response: reply });
  });
  try {
    await runDraft(page, '已提交但响应缓慢');
    await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
    await received.promise;
    await closeDialog(page);
    await runDraft(page, '正在填写的下一轮');
    const response = page.waitForResponse((r) => r.url().endsWith('/api/jobs'));
    held.release(); await response; await delay(500);
    assert.equal(await page.getByLabel('测评名称（选填）', { exact: true }).count(), 1, '旧创建响应关闭了新建测评表单');
    assert.equal(await page.getByLabel('测评名称（选填）', { exact: true }).inputValue(), '正在填写的下一轮');
    assert.equal(new URL(page.url()).searchParams.has('run'), false, '旧请求抢走了当前操作焦点');
  } finally { held.release(); await page.unroute('**/api/jobs'); }
}
async function experimentReopen({ page }) {
  await page.goto('/models');
  await page.getByRole('menuitem', { name: /多模型实验/ }).click();
  await page.waitForURL('**/experiments');
  const received = gate(), held = gate();
  await page.route('**/api/comparisons', async (route) => {
    received.release(); await held.promise;
    await route.fulfill({ status: 202, json: { key: 'compare-e2e-delayed', job_id: 'e2e-delayed' } }).catch(() => {});
  });
  try {
    await page.getByRole('button', { name: '创建实验', exact: true }).click();
    await select(page, '案例集', 'AACR');
    await select(page, '案例范围', '浏览器回归案例');
    await page.getByRole('checkbox', { name: /回归-success/ }).check();
    await page.getByRole('checkbox', { name: /回归-partial/ }).check();
    await page.getByRole('button', { name: '创建并运行', exact: true }).click();
    await received.promise;
    await page.goBack();
    await page.waitForURL('**/models');
    const response = page.waitForResponse((r) => r.url().endsWith('/api/comparisons'));
    held.release(); await response; await delay(500);
    assert.equal(new URL(page.url()).pathname, '/models', '旧实验响应将用户拉回已离开的页面');
    assert.equal(await page.getByRole('dialog').count(), 0);
  } finally { held.release(); await page.unroute('**/api/comparisons'); }
}
async function savedRun(page, name) {
  await page.goto('/');
  await runDraft(page, name);
  const reply = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
  const created = await (await reply).json();
  await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', '仅保存配置');
  return created;
}
async function closeDuringCheck({ page }) {
  await savedRun(page, '检查准备时关闭详情');
  await page.getByRole('tab', { name: '概览与案例', exact: true }).click();
  await page.getByText('分步执行、恢复与导出', { exact: true }).click();
  const received = gate(), held = gate();
  await page.route('**/api/jobs', async (route) => {
    const reply = await route.fetch();
    received.release(); await held.promise;
    await route.fulfill({ response: reply }).catch(() => {});
  });
  try {
    await page.getByRole('button', { name: '检查准备', exact: true }).click();
    await received.promise;
    await closeDialog(page);
    const response = page.waitForResponse((r) => r.url().endsWith('/api/jobs'));
    held.release(); await response; await delay(500);
    assert.equal(new URL(page.url()).searchParams.has('run'), false, '关闭详情后旧操作响应又打开详情');
    assert.equal(await page.getByRole('dialog').count(), 0);
  } finally { held.release(); await page.unroute('**/api/jobs'); }
}
async function saveThenSingleCase({ page, fixtures }) {
  await page.goto('/');
  await runDraft(page, '先保存后单例试跑');
  const reply = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
  const created = await (await reply).json();
  await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', '仅保存配置');
  await page.reload();
  await page.getByRole('tab', { name: '概览与案例', exact: true }).click();
  await page.getByText('分步执行、恢复与导出', { exact: true }).click();
  await select(page, '执行范围', 'demo-ok');
  await page.getByRole('button', { name: /检\s*视/ }).click();
  await page.getByRole('dialog').last().getByText(/执行范围：demo-ok/).waitFor();
  const reviewed = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '开始执行', exact: true }).click();
  const review = await (await reviewed).json();
  const snapshot = await waitState(page, (s) => job(s, review.job_id)?.status === 'succeeded', '只检视所选案例');
  assert.equal(snapshot.runs.find((r) => r.key === created.key).completed, 1);
  assert.equal(snapshot.runs.find((r) => r.key === created.key).metrics?.f1, undefined);
  await page.reload();
  await page.getByRole('tab', { name: '概览与案例', exact: true }).click();
  await page.getByRole('button', { name: '评分已有答卷', exact: true }).click();
  await page.getByRole('dialog').last().getByText(/所有完整答卷/).waitFor();
  const scoring = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '开始执行', exact: true }).click();
  const scored = await (await scoring).json();
  await waitState(page, (s) => job(s, scored.job_id)?.status === 'succeeded', '单例答卷评分');
  const scope = JSON.parse(await readFile(join(fixtures, '../runs', created.key, 'evaluation-scope.json'), 'utf8'));
  assert.deepEqual(scope.included, ['demo-ok']);
  await page.getByRole('tab', { name: '执行日志', exact: true }).click();
  const saved = page.waitForEvent('download');
  await page.getByRole('button', { name: '保存日志', exact: true }).click();
  const download = await saved;
  assert.match(await readFile(await download.path(), 'utf8'), /MOCK judge/);
  assert.equal(download.suggestedFilename(), 'execution.log');
  await page.getByRole('tab', { name: '中文报告', exact: true }).click();
  await page.getByText(/模拟裁判结果/).waitFor();
  const report = page.waitForEvent('download');
  await page.getByRole('button', { name: '保存文件', exact: true }).click();
  assert.match(await readFile(await (await report).path(), 'utf8'), /模拟裁判结果/);
}
async function rejectedCreateRetry({ page }) {
  await page.goto('/');
  let first = true;
  await page.route('**/api/jobs', (route) => {
    if (!first) return route.continue();
    first = false;
    return route.fulfill({ status: 503, json: { error: '暂时无法创建，请重试' } });
  });
  try {
    await runDraft(page, '失败后保留配置');
    await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
    await page.getByText('暂时无法创建，请重试', { exact: true }).waitFor();
    assert.equal(await page.getByLabel('测评名称（选填）', { exact: true }).inputValue(), '失败后保留配置');
    const reply = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
    const created = await (await reply).json();
    await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', '创建失败后直接重试');
  } finally { await page.unroute('**/api/jobs'); }
}
async function lostCreateResponse({ page }) {
  await page.goto('/');
  let once = true;
  let accepted;
  await page.route('**/api/jobs', async (route) => {
    if (!once) return route.continue();
    once = false;
    const reply = await route.fetch();
    accepted = await reply.json();
    await route.abort('connectionreset');
  });
  try {
    await runDraft(page, '提交成功但响应丢失');
    await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
    await waitState(page, (s) => s.jobs.some((j) => j.id === accepted?.job_id), '服务端已接受创建');
    await page.waitForURL((url) => url.searchParams.get('run') === accepted.key, { timeout: 5000 });
    await page.getByLabel('测评名称（选填）', { exact: true }).waitFor({ state: 'hidden' });
    await closeDialog(page);
  } finally { await page.unroute('**/api/jobs'); }
}
async function unknownCreation({ page }) {
  await page.goto('/');
  let attempts = 0;
  await page.route('**/api/jobs', (route) => ++attempts === 1 ? route.abort('connectionreset') : route.continue());
  try {
    await runDraft(page, '连接断开后先确认记录');
    await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
    await page.getByText(/未能确认创建结果.*执行记录.*避免重复创建/).waitFor();
    assert.equal(attempts, 1, '结果不明时不能自动重发创建请求');
    assert.equal(await page.getByLabel('测评名称（选填）', { exact: true }).inputValue(), '连接断开后先确认记录');
    const reply = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
    const created = await (await reply).json();
    await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', '确认未创建后由用户重试');
    assert.equal(attempts, 2);
  } finally { await page.unroute('**/api/jobs'); }
}
export const journeys = [
  ['create-close-reopen-slow-response', reopenCreate],
  ['run-check-close-slow-response', closeDuringCheck],
  ['experiment-submit-browser-back', experimentReopen],
  ['save-config-single-case-score-download', saveThenSingleCase],
  ['create-failure-keeps-draft-retry', rejectedCreateRetry],
  ['create-accepted-response-lost', lostCreateResponse],
  ['create-unknown-response-check-first', unknownCreation],
];
