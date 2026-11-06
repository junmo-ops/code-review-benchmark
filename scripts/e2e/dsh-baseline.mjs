import assert from 'node:assert/strict';
import { join, resolve } from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';
import { api, select, upload, waitState, job, state } from './helpers.mjs';

export async function dshBaseline({ page, fixtures }) {
  await finishPreviousBatch(page, '/repositories');
  await upload(page, '/repositories', join(fixtures, 'repository.zip'), '导入仓库');
  await waitState(page, (s) => s.case_assets?.repositories?.some((r) => r.slug === 'e2e__demo'), 'DSH 仓库导入');
  await finishPreviousBatch(page, '/cases');
  await upload(page, '/cases', join(fixtures, 'dsh-case.zip'), '确认导入');
  await waitState(page, (s) => s.catalog.some((c) => c.datasets.some((d) => d.name === 'DSH Linux 单例')), 'DSH 单例导入');
  const evidence = [];
  for (const scoring of ['案例集原始评分', 'Martian 语义评分']) {
    await page.goto('/');
    await page.getByRole('button', { name: /新建测评/ }).first().click();
    await select(page, '案例集', 'AACR');
    await select(page, '案例范围', 'DSH Linux 单例');
    await select(page, '被测模型', 'DSH 本地review');
    await select(page, '裁判模型', 'DSH 本地judge');
    await select(page, '被测工具', 'DSH + Skill');
    await select(page, 'Skill 模式', '不加载 Skill（基线）');
    await select(page, '评分方法', scoring);
    const response = page.waitForResponse((reply) => reply.url().endsWith('/api/jobs') && reply.request().method() === 'POST');
    await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
    const reply = await response;
    const created = await reply.json();
    assert.equal(reply.status(), 202, JSON.stringify(created));
    let snapshot = await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', 'DSH 保存配置', 120);
    assert.equal(snapshot.runs.find((r) => r.key === created.key).completed, 0);
    const root = resolve(fixtures, '..', 'runs', created.key);
    const cfg = JSON.parse(await readFile(join(root, 'run-config.json'), 'utf8'));
    assert.equal(cfg.skill_mode, 'baseline');
    assert.equal(cfg.judge_model, 'dsh-judge');
    assert.equal(cfg.timeout_minutes, 10);
    const mode = process.env.E2E_DSH_RUNTIME_MODE ?? 'sandbox';
    assert.equal(cfg.dsh_policy.runtime_mode, mode);
    assert.equal(cfg.dsh_policy.os_isolation, mode === 'sandbox');
    await page.reload();
    const launched = await api(page, '/api/jobs', { action: 'workflow', key: created.key,
      profile: snapshot.runs.find((r) => r.key === created.key).profile });
    snapshot = await waitState(page, (s) => ['succeeded', 'failed'].includes(job(s, launched.job_id)?.status), 'DSH 真实检视与本地裁判', 120);
    assert.equal(job(snapshot, launched.job_id).status, 'succeeded', JSON.stringify(job(snapshot, launched.job_id)));
    const run = snapshot.runs.find((r) => r.key === created.key);
    assert.equal(run.completed, 1);
    assert.equal(run.scored_cases, 1);
    const answer = JSON.parse(await readFile(join(root, 'results/demo-ok.json'), 'utf8'));
    assert.equal(answer.skill.mode, 'baseline');
    assert.equal(answer.dsh.runtime_mode, mode);
    assert.equal(answer.review.summary.total_tokens, 220);
    if (cfg.evaluator.id === 'original') {
      const audit = JSON.parse(await readFile(join(root, 'judge-audit.json'), 'utf8'));
      assert.equal(audit.eval_rounds, 3);
      assert.equal(audit.requests.length, 3);
      assert.deepEqual(audit.transport, { request_timeout_seconds: 180, retry_delays_seconds: [10, 30] });
    } else {
      assert.equal(cfg.evaluator.parameters.rounds, 1);
      assert.equal(cfg.evaluator.parameters.timeout_seconds, 180);
      assert.equal(cfg.evaluator.parameters.max_retries, 0);
    }
    const report = await api(page, `/api/artifact?key=${created.key}&path=report.zh-CN.md`, undefined, 'GET');
    assert.match(report.text, /无待测 Skill 基线/);
    assert.match(report.text, /0\.2\.1-rc\.2/);
    if (mode === 'pod') assert.match(report.text, /无操作系统级沙盒/);
    await page.goto(`/?run=${created.key}&tab=report`);
    await page.getByRole('button', { name: '下载完整报告', exact: true }).waitFor();
    const downloaded = page.waitForEvent('download');
    await page.getByRole('button', { name: '下载完整报告', exact: true }).click();
    const download = await downloaded;
    await download.saveAs(resolve(fixtures, `../../dsh-${cfg.evaluator.id}.md`));
    evidence.push({ key: created.key, scoring, cfg, answer });
  }
  const snapshot = await state(page);
  assert.deepEqual(evidence[0].answer.review.comments, evidence[1].answer.review.comments);
  assert.notEqual(evidence[0].answer.session_id, evidence[1].answer.session_id);
  assert.equal(snapshot.runs.filter((r) => r.reviewer === 'dsh_skill').length, 2);
  await writeFile(resolve(fixtures, '../../dsh-baseline.json'), JSON.stringify(evidence, null, 2));
  await page.screenshot({ path: resolve(fixtures, '../../dsh-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(fixtures, '../../dsh-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
}

async function finishPreviousBatch(page, path) {
  await page.goto(path);
  await page.locator('input[type=file]').waitFor({ state: 'attached' });
  const finish = page.getByRole('button', { name: '结束本批次', exact: true });
  if (await finish.isVisible()) {
    await finish.click();
    await finish.waitFor({ state: 'hidden' });
  }
}
