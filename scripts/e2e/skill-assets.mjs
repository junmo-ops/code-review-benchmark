import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';
import { api, upload } from './helpers.mjs';

export async function skillAssets({ page, fixtures }) {
  await upload(page, '/skills', join(fixtures, 'skill.zip'), '导入 Skill');
  await page.getByText(/所选文件导入完成/).waitFor({ timeout: 15000 });
  const first = (await api(page, '/api/skills', undefined, 'GET')).versions[0];
  await page.getByRole('row').filter({ hasText: first.id.slice(0, 12) }).getByRole('button', { name: '版本详情' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByText('references/rules.txt', { exact: true }).waitFor();
  await dialog.getByText('scripts/never-run.sh', { exact: true }).waitFor();
  const [download] = await Promise.all([
    page.waitForEvent('download'), dialog.getByRole('button', { name: '下载原包', exact: true }).click(),
  ]);
  const original = await readFile(await download.path());
  assert.equal(createHash('sha256').update(original).digest('hex'), first.archive_sha256);
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await upload(page, '/skills', join(fixtures, 'skill-changed.zip'), '导入 Skill');
  await page.getByText(/所选文件导入完成/).waitFor({ timeout: 15000 });
  assert.equal((await api(page, '/api/skills', undefined, 'GET')).versions.length, 2);
  await upload(page, '/skills', join(fixtures, 'skill.zip'), '导入 Skill');
  await page.getByText(/所选文件导入完成/).waitFor({ timeout: 15000 });
  assert.equal((await api(page, '/api/skills', undefined, 'GET')).versions.length, 2);
  await upload(page, '/skills', join(fixtures, 'skill-unsafe.zip'), '导入 Skill');
  await page.getByText(/不安全路径/).first().waitFor({ timeout: 15000 });
  assert.equal((await api(page, '/api/skills', undefined, 'GET')).versions.length, 2);
  await page.getByRole('button', { name: '结束本批次', exact: true }).click();
  await page.reload();
  await page.getByRole('row').filter({ hasText: first.id.slice(0, 12) }).waitFor();
  await page.screenshot({ path: join(fixtures, '../../skill-desktop.png'), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.querySelector('.ant-layout-sider')?.getBoundingClientRect().width <= 1);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), '移动页面不应产生横向溢出');
  await page.screenshot({ path: join(fixtures, '../../skill-mobile.png'), fullPage: true, animations: 'disabled' });
  await page.getByRole('row').filter({ hasText: first.id.slice(0, 12) }).getByRole('button', { name: '版本详情' }).click();
  await page.getByRole('dialog').getByText('references/rules.txt', { exact: true }).waitFor();
  await page.getByRole('dialog').screenshot({ path: join(fixtures, '../../skill-mobile-detail.png'), animations: 'disabled' });
  await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 1000 });
}

export async function skillUploadRecovery({ page, fixtures }) {
  const evidence = [];
  for (const phase of ['chunks', 'complete']) {
    await page.goto('/skills');
    await page.evaluate(() => {
      localStorage.removeItem('bench.upload.session.v1.skill');
      localStorage.removeItem('bench.upload.batch.v1.skill');
    });
    const pattern = phase === 'chunks' ? '**/api/uploads/*/chunks/*' : '**/api/uploads/*/complete';
    let calls = 0;
    const started = Date.now();
    await page.route(pattern, async (route) => {
      calls++;
      if (calls !== 1) return route.continue();
      await route.fetch();
      await route.abort('connectionreset');
    });
    try {
      await upload(page, '/skills', join(fixtures, 'skill.zip'), '导入 Skill');
      await page.getByText(/上传连接中断|Failed to fetch/).first().waitFor();
      await page.reload();
      if (phase === 'chunks') {
        await page.getByText(/发现未完成上传/).waitFor();
        await page.locator('input[type=file]').setInputFiles(join(fixtures, 'skill.zip'));
        await page.getByRole('button', { name: '继续上传并导入', exact: true }).click();
        await page.getByText(/所选文件导入完成/).waitFor({ timeout: 15000 });
      } else {
        await page.getByText(/本批次已导入 1 \/ 1/).waitFor({ timeout: 15000 });
      }
      assert.equal(calls, 1, '已接收分块或导入不得因响应丢失而重放');
      assert.equal((await api(page, '/api/skills', undefined, 'GET')).versions.length, 1);
      evidence.push({ fault: `lost-${phase}-response-after-persist`, milliseconds: Date.now() - started, requests: calls });
    } finally { await page.unroute(pattern); }
  }
  await writeFile(join(fixtures, '../../skill-recovery.json'), JSON.stringify({
    boundary: 'Local browser network fault after real local API persistence; no models, DSH or COS.', evidence,
  }, null, 2));
}
