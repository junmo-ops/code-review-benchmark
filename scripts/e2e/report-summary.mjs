import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { state } from './helpers.mjs';

const report = `# 代码检视测评报告

评分状态：完成

## 评分结果

| 口径 | 精确率 | 召回率 | F1 |
|---|---:|---:|---:|
| 语义匹配 | 50.0% | 75.0% | 60.0% |

## 耗时与用量

| 阶段 | 耗时（秒） | 输入 Token | 输出 Token | 总 Token |
|---|---:|---:|---:|---:|
| 检视：demo-ok | 10 | 1,000 | 200 | 1,200 |
| 检视：demo-next | 未提供 | 未提供 | 未提供 | 未提供 |
| 裁判评分 | 30 | 80 | 20 | 100 |

## 检视工具发现的问题

案例：\`demo-ok\`；模型：\`mock\`。

### 意见 1

ONLY_DETAIL_FINDING

案例：\`demo-next\`；模型：\`mock\`。

### 意见 1

SECOND_CASE_FINDING

## 参考答案与匹配情况

### 案例 \`demo-ok\` · 第 1 轮

ROUND_ONE_REFERENCE

### 案例 \`demo-ok\` · 第 2 轮

ROUND_TWO_REFERENCE
`;

export async function reportSummaryScenario({ page }) {
  const snapshot = await state(page);
  const run = snapshot.runs.find((item) => item.metrics?.f1 === 1);
  assert.ok(run);
  const handler = async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('path') === 'report.zh-CN.md')
      return route.fulfill({ json: { text: report, truncated: false } });
    return route.continue();
  };
  await page.route('**/api/artifact?*', handler);
  try {
    await page.goto(`/?run=${run.key}&tab=report`);
    const article = page.locator('.markdown');
    await article.getByText('耗时与 Token', { exact: true }).waitFor();
    assert.match(await article.innerText(), /1,200（1\/2 例）/);
    assert.doesNotMatch(await article.innerText(), /ONLY_DETAIL_FINDING|SECOND_CASE_FINDING|ROUND_ONE_REFERENCE/);
    const summary = page.waitForEvent('download');
    await page.getByRole('button', { name: '下载报告摘要', exact: true }).click();
    const downloaded = await summary;
    const text = await readFile(await downloaded.path(), 'utf8');
    assert.match(downloaded.suggestedFilename(), /summary\.md$/);
    assert.match(text, /1,200（1\/2 例）/);
    assert.doesNotMatch(text, /ONLY_DETAIL_FINDING/);
    const full = page.waitForEvent('download');
    await page.getByRole('button', { name: '下载完整报告', exact: true }).click();
    assert.equal(await readFile(await (await full).path(), 'utf8'), report);
    await page.getByText('案例明细', { exact: true }).click();
    await page.locator('.ant-select').filter({ has: page.getByRole('combobox', { name: '案例明细章节' }) }).click();
    await page.getByText('检视工具发现的问题 · demo-ok', { exact: true }).click();
    await article.getByText('ONLY_DETAIL_FINDING', { exact: true }).waitFor();
    assert.doesNotMatch(await article.innerText(), /SECOND_CASE_FINDING/);
    await page.reload();
    await article.getByText('耗时与 Token', { exact: true }).waitFor();
  } finally {
    await page.unroute('**/api/artifact?*', handler);
  }
}
