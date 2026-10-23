import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { api, job, select, upload, waitState } from './helpers.mjs';

export async function importResource(page, fixtures, path, file, button) {
  await page.goto(path);
  const finish = page.getByRole('button', { name: '结束本批次', exact: true });
  if (await finish.isVisible()) await finish.click();
  await upload(page, path, join(fixtures, file), button);
  await page.getByText(/所选文件导入完成/).waitFor();
}

export async function form(page, scoring, version) {
  await page.goto('/');
  await page.getByRole('button', { name: /新建测评/ }).first().click();
  await select(page, '案例集', 'AACR');
  await select(page, '案例范围', 'DSH Linux 单例');
  await select(page, '被测模型', 'DSH 本地review');
  await select(page, '裁判模型', 'DSH 本地judge');
  await select(page, '被测工具', 'DSH + Skill');
  await select(page, 'Skill 模式', '加载指定 Skill 版本');
  await select(page, 'Skill 版本', version.id.slice(0, 12));
  await select(page, 'Skill 模式', '不加载 Skill（基线）');
  assert.equal(await page.getByLabel('Skill 版本', { exact: true }).count(), 0);
  await select(page, 'Skill 模式', '加载指定 Skill 版本');
  assert.equal(await page.getByLabel('Skill 版本', { exact: true }).inputValue(), '');
  await select(page, 'Skill 版本', version.id.slice(0, 12));
  await select(page, '被测工具', 'LCR CLI');
  assert.equal(await page.getByLabel('Skill 版本', { exact: true }).count(), 0);
  await select(page, '被测工具', 'DSH + Skill');
  await select(page, 'Skill 模式', '加载指定 Skill 版本');
  assert.equal(await page.getByLabel('Skill 版本', { exact: true }).inputValue(), '');
  await select(page, 'Skill 版本', version.id.slice(0, 12));
  await select(page, '评分方法', scoring);
}

export async function dshSkill({ page, fixtures }) {
  await importResource(page, fixtures, '/repositories', 'repository.zip', '导入仓库');
  await importResource(page, fixtures, '/cases', 'dsh-case.zip', '确认导入');
  await importResource(page, fixtures, '/skills', 'skill.zip', '导入 Skill');
  const version = (await api(page, '/api/skills', undefined, 'GET')).versions.find((v) => v.name === 'browser-review');
  const evidence = [];
  for (const scoring of ['案例集原始评分', 'Martian 语义评分']) {
    await form(page, scoring, version);
    const response = page.waitForResponse((r) => r.url().endsWith('/api/jobs') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
    const reply = await response;
    const created = await reply.json();
    assert.equal(reply.status(), 202, JSON.stringify(created));
    assert.equal(reply.request().postDataJSON().skill_version, version.id);
    let snapshot = await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', 'Skill 保存', 120);
    const root = resolve(fixtures, '..', 'runs', created.key);
    const cfg = JSON.parse(await readFile(join(root, 'run-config.json'), 'utf8'));
    assert.equal(cfg.skill_version, version.id);
    assert.equal(snapshot.runs.find((r) => r.key === created.key).completed, 0);
    await importResource(page, fixtures, '/skills', 'skill-changed.zip', '导入 Skill');
    const launched = await api(page, '/api/jobs', { action: 'workflow', key: created.key,
      profile: snapshot.runs.find((r) => r.key === created.key).profile });
    snapshot = await waitState(page, (s) => ['succeeded', 'failed'].includes(job(s, launched.job_id)?.status), '实际 DSH Skill 检视评分', 150);
    assert.equal(job(snapshot, launched.job_id).status, 'succeeded', JSON.stringify(job(snapshot, launched.job_id)));
    const run = snapshot.runs.find((r) => r.key === created.key);
    assert.equal(run.completed, 1);
    assert.equal(run.scored_cases, 1);
    const answer = JSON.parse(await readFile(join(root, 'results/demo-ok.json'), 'utf8'));
    assert.equal(answer.skill.version, version.id);
    assert.equal(answer.skill.loaded, true);
    assert.ok(answer.skill.evidence.some((entry) => entry.form === 'explicit_injection'));
    const report = await api(page, `/api/artifact?key=${created.key}&path=report.zh-CN.md`, undefined, 'GET');
    assert.ok(report.text.includes(version.id));
    await page.goto(`/?run=${created.key}&tab=report`);
    await page.getByText(version.id, { exact: false }).first().waitFor();
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: '下载完整报告', exact: true }).click();
    await (await download).saveAs(resolve(fixtures, `../../dsh-skill-${cfg.evaluator.id}.md`));
    evidence.push({ cfg, answer, key: created.key });
  }
  assert.deepEqual(evidence[0].answer.review.comments, evidence[1].answer.review.comments);
  await writeFile(resolve(fixtures, '../../dsh-skill.json'), JSON.stringify(evidence, null, 2));
  await page.screenshot({ path: resolve(fixtures, '../../dsh-skill-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(fixtures, '../../dsh-skill-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
}
