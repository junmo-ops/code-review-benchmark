/** Do not deliver a manifest with a lockfile from another dependency environment. */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const folder = new URL('../frontend/', import.meta.url);
const manifest = JSON.parse(readFileSync(new URL('package.json', folder), 'utf8'));
const lock = JSON.parse(readFileSync(new URL('package-lock.json', folder), 'utf8'));
const declared = lock.packages?.[''];
if (!declared) throw new Error('前端锁文件缺少根依赖记录，请同步行内生成的 package-lock.json。');
const mismatches = [];
for (const section of ['dependencies', 'devDependencies']) {
  const wanted = manifest[section] ?? {};
  const locked = declared[section] ?? {};
  for (const name of new Set([...Object.keys(wanted), ...Object.keys(locked)])) {
    if (wanted[name] !== locked[name]) mismatches.push(name);
  }
}
if (mismatches.length) {
  throw new Error(`前端依赖清单与锁文件不一致：${mismatches.join(', ')}。请同步行内验证过的 package-lock.json，不能打包旧锁文件。`);
}
console.log(`依赖清单与锁文件一致：${fileURLToPath(folder)}`);
