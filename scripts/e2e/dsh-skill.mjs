import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { api, job, select, upload, waitState } from './helpers.mjs';

// T04 新契约：创建对话框用一个「Skill 选择」多选覆盖三态 —— 0 个成员（基线）/ 1 个
// 成员（单 Skill）/ ≥2 个成员（Skill 组合）。该 Form.Item 只有 label 没有 name，
// antd 不会给控件绑 id，getByLabel 定位不到，一律按「label 文本所在 .ant-form-item
// 内的 .ant-select」作用域操作（antd v5.27 的 DOM 结构，浏览器端实跑时复核）。
const SKILL_FIELD_LABEL = 'Skill 选择';
const BASELINE_OPTION = '不加载 Skill（基线）';

// —— 多选控件定位与交互 -------------------------------------------------------

export function skillField(page) {
  // label 带 required 星号（antd 把 * 渲染进 label 文本），用 .ant-form-item-label 的
  // hasText 子串匹配做作用域，避免 getByText exact 命中不到带前缀的 label。
  return page.locator('.ant-form-item')
    .filter({ has: page.locator('.ant-form-item-label').filter({ hasText: SKILL_FIELD_LABEL }) })
    .locator('.ant-select');
}

export async function chooseSkill(page, text) {
  const field = skillField(page);
  const open = await field.evaluate((element) => element.classList.contains('ant-select-open'));
  if (open) await field.locator('.ant-select-selector').click(); // 多选下拉不会自动收起
  await field.locator('.ant-select-selector').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content')
    .filter({ hasText: text }).first().click();
}

export async function selectedSkillLabels(page) {
  return (await skillField(page).locator('.ant-select-selection-item-content').allTextContents())
    .map((text) => text.trim()).filter(Boolean);
}

export async function memberCount(page) {
  return (await selectedSkillLabels(page)).filter((label) => label !== BASELINE_OPTION).length;
}

function memberIds(selection) {
  const values = Array.isArray(selection) ? selection : [selection];
  return values.map((entry) => (typeof entry === 'string' ? entry : entry.id));
}

// —— 组合摘要（对齐 backend selection.identity_digest / combination_digest）-----

function asciiString(text) {
  let out = '"';
  for (const char of text) {
    const code = char.codePointAt(0);
    if (code >= 0x80) {
      if (code > 0xFFFF) {
        const high = 0xD800 + ((code - 0x10000) >> 10);
        const low = 0xDC00 + ((code - 0x10000) & 0x3FF);
        out += `\\u${high.toString(16).padStart(4, '0')}\\u${low.toString(16).padStart(4, '0')}`;
      } else {
        out += `\\u${code.toString(16).padStart(4, '0')}`;
      }
    } else if (char === '"' || char === '\\') {
      out += `\\${char}`;
    } else if (code < 0x20) {
      out += ({ '\b': '\\b', '\t': '\\t', '\n': '\\n', '\f': '\\f', '\r': '\\r' })[char]
        ?? `\\u${code.toString(16).padStart(4, '0')}`;
    } else {
      out += char;
    }
  }
  return out + '"';
}

// 与 json.dumps(sort_keys=True, ensure_ascii=True, separators=(',', ':')) 逐字节一致。
function canonicalJson(value) {
  if (value === null) return 'null';
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (typeof value === 'object') {
    return `{${Object.keys(value).sort()
      .map((key) => `${canonicalJson(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  if (typeof value === 'string') return asciiString(value);
  return String(value);
}

const sha256 = (text) => createHash('sha256').update(text).digest('hex');

function identityDigest(record) {
  return sha256(canonicalJson(record));
}

function combinationDigest(records) {
  if (records.length === 1) return identityDigest(records[0]); // N=1 摘要 = 成员 identity_digest
  return sha256(canonicalJson(records.map(identityDigest).sort()));
}

// —— 提交与断言辅助 ------------------------------------------------------------

async function saveCreation(page) {
  const response = page.waitForResponse(
    (reply) => reply.url().endsWith('/api/jobs') && reply.request().method() === 'POST');
  await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
  const reply = await response;
  const created = await reply.json();
  assert.equal(reply.status(), 202, JSON.stringify(created));
  return { reply, created };
}

export function assertPayload(reply, mode, ids) {
  // 新契约：payload 统一发 skill_mode + skill_versions 成员列表；旧单值 skill_version
  // 不再发送；基线（0 个成员）wire 上省略数组（后端对空数组 fail-closed）。
  const payload = reply.request().postDataJSON();
  assert.equal(payload.skill_mode, mode, JSON.stringify(payload));
  assert.equal(payload.skill_version, undefined, JSON.stringify(payload));
  if (ids.length) assert.deepEqual(payload.skill_versions, ids, JSON.stringify(payload));
  else assert.equal(payload.skill_versions, undefined, JSON.stringify(payload));
}

async function configOf(root) {
  return JSON.parse(await readFile(join(root, 'run-config.json'), 'utf8'));
}

function assertSingleConfig(cfg, id, outputLimit) {
  // N=1：冻结配置写成员列表 + 组合摘要，同时保留旧单值投影（skill_version / skill_identity）。
  assert.deepEqual(cfg.skill_versions, [id]);
  assert.equal(cfg.skill_version, id);
  assert.deepEqual(cfg.skill_identities, [cfg.skill_identity]);
  assert.match(cfg.skill_combination_digest, /^[0-9a-f]{64}$/);
  assert.equal(cfg.skill_combination_digest, identityDigest(cfg.skill_identities[0]));
  assert.equal(cfg.dsh_max_output_tokens, outputLimit);
  assert.equal(cfg.dsh_policy.max_output_tokens, outputLimit);
}

function assertSingleAnswer(answer, id) {
  // answer['skill'] 判别规则：先看 mode，再看有无 members 列表；N=1 保持旧单对象形态。
  assert.equal(answer.skill.mode, 'skill');
  assert.equal(answer.skill.version, id);
  assert.ok(!('members' in answer.skill));
  assert.equal(answer.skill.loaded, true);
  assert.ok(answer.skill.evidence.some((entry) => entry.form === 'explicit_injection'));
}

function assertCombinedConfig(cfg, ids) {
  // N≥2：组合结构（成员列表 + 组合摘要，无序集合，顺序不参与身份）；不写旧单值投影。
  assert.deepEqual(cfg.skill_versions, ids);
  assert.equal(cfg.skill_identities.length, 2);
  assert.deepEqual(cfg.skill_identities.map((record) => record.id), ids);
  assert.equal(cfg.skill_version, undefined);
  assert.equal(cfg.skill_identity, undefined);
  assert.match(cfg.skill_combination_digest, /^[0-9a-f]{64}$/);
  assert.equal(cfg.skill_combination_digest, combinationDigest(cfg.skill_identities));
  assert.equal(cfg.skill_combination_digest, combinationDigest([...cfg.skill_identities].reverse()));
}

function assertCombinedAnswer(answer, ids) {
  // answer['skill'] 组合形态：{mode:'skill', members:[{version,name,loaded,evidence}]}。
  assert.equal(answer.skill.mode, 'skill');
  assert.equal(answer.skill.members.length, 2);
  assert.deepEqual(answer.skill.members.map((member) => member.version), ids);
  assert.deepEqual(answer.skill.members.map((member) => member.name),
    ['candidate-skill-1', 'candidate-skill-2']);
  for (const member of answer.skill.members) {
    assert.equal(member.loaded, true);
    assert.ok(member.evidence.some((entry) => entry.form === 'explicit_injection'));
  }
}

export async function importResource(page, fixtures, path, file, button) {
  await page.goto(path);
  const finish = page.getByRole('button', { name: '结束本批次', exact: true });
  if (await finish.isVisible()) await finish.click();
  await upload(page, path, join(fixtures, file), button);
  await page.getByText(/所选文件导入完成/).waitFor();
}

export async function form(page, scoring, version, outputLimit) {
  // version：单个版本对象 / 版本 id / id 数组（覆盖三态：0 个 = 基线、1 个 = 单 Skill、≥2 = 组合）。
  const selected = memberIds(version);
  await page.goto('/');
  await page.getByRole('button', { name: /新建测评/ }).first().click();
  await select(page, '案例集', 'AACR');
  await select(page, '案例范围', 'DSH Linux 单例');
  await select(page, '被测模型', 'DSH 本地review');
  await select(page, '裁判模型', 'DSH 本地judge');
  await select(page, '被测工具', 'DSH + Skill');
  // 三态-0：显式基线 = 只选基线一项，无成员。
  await chooseSkill(page, BASELINE_OPTION);
  assert.deepEqual(await selectedSkillLabels(page), [BASELINE_OPTION]);
  // 三态-1 / 三态-N：逐点成员后断言成员数与提示文案（1 = 单 Skill；≥2 = Skill 组合）。
  for (const id of selected) await chooseSkill(page, id.slice(0, 12));
  assert.equal(await memberCount(page), selected.length);
  if (selected.length === 1) {
    await page.getByText(/^单 Skill：/).waitFor();
    assert.equal(await page.getByText(/^Skill 组合（\d+ 个成员/).count(), 0);
  } else {
    await page.getByText(new RegExp(`^Skill 组合（${selected.length} 个成员`)).waitFor();
  }
  // 互斥：点基线清空成员集合；随后重新选回（组合态同样成立）。
  await chooseSkill(page, BASELINE_OPTION);
  assert.equal(await memberCount(page), 0);
  for (const id of selected) await chooseSkill(page, id.slice(0, 12));
  assert.equal(await memberCount(page), selected.length);
  // 被测工具切换：离开 DSH + Skill 时控件卸载（成员与模式一并清空），切回后回到基线态。
  await select(page, '被测工具', 'LCR CLI');
  assert.equal(await skillField(page).count(), 0);
  await select(page, '被测工具', 'DSH + Skill');
  assert.deepEqual(await selectedSkillLabels(page), [BASELINE_OPTION]);
  for (const id of selected) await chooseSkill(page, id.slice(0, 12));
  assert.equal(await memberCount(page), selected.length);
  await select(page, '评分方法', scoring);
  if (outputLimit !== undefined) {
    await page.getByText('高级参数', { exact: true }).click();
    await page.getByLabel('DSH 单次输出上限（tokens）', { exact: true }).fill(String(outputLimit));
  }
}

async function singleRun(page, fixtures, scoring, version, evidence) {
  await form(page, scoring, version.id, 32768);
  const { reply, created } = await saveCreation(page);
  assertPayload(reply, 'skill', [version.id]);
  let snapshot = await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', 'Skill 保存', 120);
  const root = resolve(fixtures, '..', 'runs', created.key);
  const cfg = await configOf(root);
  assertSingleConfig(cfg, version.id, 32768);
  assert.equal(snapshot.runs.find((r) => r.key === created.key).completed, 0);
  // 保存后导入「同一 Skill 的变化版本」，已保存批次的冻结内容不受影响。
  await importResource(page, fixtures, '/skills', 'skill-changed.zip', '导入 Skill');
  const launched = await api(page, '/api/jobs', { action: 'workflow', key: created.key,
    profile: snapshot.runs.find((r) => r.key === created.key).profile });
  snapshot = await waitState(page, (s) => ['succeeded', 'failed'].includes(job(s, launched.job_id)?.status), '实际 DSH Skill 检视评分', 150);
  assert.equal(job(snapshot, launched.job_id).status, 'succeeded', JSON.stringify(job(snapshot, launched.job_id)));
  const run = snapshot.runs.find((r) => r.key === created.key);
  assert.equal(run.completed, 1);
  assert.equal(run.scored_cases, 1);
  const answer = JSON.parse(await readFile(join(root, 'results/demo-ok.json'), 'utf8'));
  assertSingleAnswer(answer, version.id);
  // .skill-load.json：N=1 为单个对象形态（顶层 dict，非组合列表）。
  const load = JSON.parse(await readFile(join(root, 'raw/demo-ok.skill-load.json'), 'utf8'));
  assert.equal(load.mode, 'skill');
  assert.equal(load.version, version.id);
  assert.equal(load.loaded, true);
  const inputs = JSON.parse(await readFile(join(root, 'raw/demo-ok.model-inputs.json'), 'utf8'));
  assert.ok(inputs.length > 0);
  assert.ok(inputs.every((request) => request.max_completion_tokens === 32768));
  const report = await api(page, `/api/artifact?key=${created.key}&path=report.zh-CN.md`, undefined, 'GET');
  assert.ok(report.text.includes(version.id));
  assert.ok(report.text.includes('单次模型输出上限 32768 tokens'));
  await page.goto(`/?run=${created.key}&tab=report`);
  await page.getByText(version.id, { exact: false }).first().waitFor();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载完整报告', exact: true }).click();
  await (await download).saveAs(resolve(fixtures, `../../dsh-skill-${cfg.evaluator.id}.md`));
  evidence.push({ cfg, answer, key: created.key });
}

async function comboPositive(page, fixtures, members, evidence) {
  const ids = members.map((member) => member.id);
  await form(page, '案例集原始评分', ids, 32768);
  const { reply, created } = await saveCreation(page);
  assertPayload(reply, 'skill', ids);
  let snapshot = await waitState(page, (s) => job(s, created.job_id)?.status === 'succeeded', 'Skill 组合保存', 120);
  const root = resolve(fixtures, '..', 'runs', created.key);
  const cfg = await configOf(root);
  assertCombinedConfig(cfg, ids);
  const launched = await api(page, '/api/jobs', { action: 'workflow', key: created.key,
    profile: snapshot.runs.find((r) => r.key === created.key).profile });
  snapshot = await waitState(page, (s) => ['succeeded', 'failed'].includes(job(s, launched.job_id)?.status), '实际 DSH 组合检视评分', 150);
  assert.equal(job(snapshot, launched.job_id).status, 'succeeded', JSON.stringify(job(snapshot, launched.job_id)));
  const run = snapshot.runs.find((r) => r.key === created.key);
  assert.equal(run.completed, 1);
  assert.equal(run.scored_cases, 1);
  const answer = JSON.parse(await readFile(join(root, 'results/demo-ok.json'), 'utf8'));
  assertCombinedAnswer(answer, ids);
  // .skill-load.json：组合 = 顶层成员列表（每项 mode/version/name/loaded/evidence）。
  const load = JSON.parse(await readFile(join(root, 'raw/demo-ok.skill-load.json'), 'utf8'));
  assert.ok(Array.isArray(load));
  assert.deepEqual(load.map((entry) => entry.mode), ['skill', 'skill']);
  assert.deepEqual(load.map((entry) => entry.name), ['candidate-skill-1', 'candidate-skill-2']);
  assert.ok(load.every((entry) => entry.loaded));
  // 报告：subject 列出成员名 + 组合摘要，加载列按成员逐一列出证据。
  const report = await api(page, `/api/artifact?key=${created.key}&path=report.zh-CN.md`, undefined, 'GET');
  assert.match(report.text, /Skill 组合（2 个成员：[^）]+）；组合摘要 [0-9a-f]{64}/);
  assert.match(report.text, /完整加载已核验（2\/2）：candidate-skill-1［explicit_injection］/);
  assert.ok(report.text.includes(cfg.skill_combination_digest));
  await page.goto(`/?run=${created.key}&tab=report`);
  await page.getByText('Skill 组合（2 个成员', { exact: false }).first().waitFor();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载完整报告', exact: true }).click();
  await (await download).saveAs(resolve(fixtures, '../../dsh-skill-combination.md'));
  evidence.push({ cfg, answer, key: created.key, combination: true });
}

async function comboRejected(page, members) {
  const ids = members.map((member) => member.id);
  await form(page, '案例集原始评分', ids, 32768);
  assert.equal(await memberCount(page), 2);
  await page.getByText(/^Skill 组合（2 个成员/).waitFor();
  const response = page.waitForResponse(
    (reply) => reply.url().endsWith('/api/jobs') && reply.request().method() === 'POST');
  await page.getByRole('button', { name: '仅保存配置', exact: true }).click();
  const reply = await response;
  const body = await reply.json();
  // 组合级校验：同名成员被后端拒绝（create 400，不排队）。
  assert.equal(reply.status(), 400, JSON.stringify(body));
  assertPayload(reply, 'skill', ids);
  assert.match(body.error, /组合内不允许同名成员/);
  await page.getByText(/组合内不允许同名成员/).first().waitFor();
  await page.getByRole('dialog').last().getByRole('button', { name: '取消', exact: true }).click();
}

export async function dshSkill({ page, fixtures }) {
  await importResource(page, fixtures, '/repositories', 'repository.zip', '导入仓库');
  await importResource(page, fixtures, '/cases', 'dsh-case.zip', '确认导入');
  await importResource(page, fixtures, '/skills', 'skill.zip', '导入 Skill');
  await importResource(page, fixtures, '/skills', 'skill-other.zip', '导入 Skill');
  const versions = (await api(page, '/api/skills', undefined, 'GET')).versions;
  const version = versions.find((entry) => entry.name === 'browser-review');
  const other = versions.find((entry) => entry.name !== 'browser-review');
  assert.ok(version && other, '缺少组合 e2e 所需 Skill 版本（browser-review / browser-other）');
  const evidence = [];
  for (const scoring of ['案例集原始评分', 'Martian 语义评分']) {
    await singleRun(page, fixtures, scoring, version, evidence);
  }
  assert.deepEqual(evidence[0].answer.review.comments, evidence[1].answer.review.comments);
  // 组合正路径：N=2 两个不同名成员（每案例会话逐一加载并留证）。
  await comboPositive(page, fixtures, [version, other], evidence);
  // 组合负路径：两个同名成员（browser-review 与 skill-changed）被组合级校验拒绝。
  // skill-changed 由 singleRun 中途导入，这里在循环后重新取版本确保 --only 单场景也成立。
  const latest = (await api(page, '/api/skills', undefined, 'GET')).versions;
  const changed = latest.find((entry) => entry.name === 'browser-review' && entry.id !== version.id);
  assert.ok(changed, '缺少同名成员副本（skill-changed.zip）');
  await comboRejected(page, [version, changed]);
  await writeFile(resolve(fixtures, '../../dsh-skill.json'), JSON.stringify(evidence, null, 2));
  await page.screenshot({ path: resolve(fixtures, '../../dsh-skill-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(fixtures, '../../dsh-skill-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
}
