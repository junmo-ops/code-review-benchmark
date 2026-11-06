/** Do not deliver a manifest with a lockfile from another dependency environment. */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

function validatePackage(name, entry) {
  if (typeof entry.version !== 'string' || !entry.version.trim()) {
    throw new Error(`锁文件缺少依赖版本：${name}。请修复元数据，不能用空占位条目安装。`);
  }
  if (!URL.canParse(entry.resolved)) throw new Error(`锁文件缺少有效下载地址：${name}`);
  const url = new URL(entry.resolved);
  if (url.origin !== 'https://registry.npmjs.org' || url.username || url.password) {
    throw new Error(`锁文件下载地址不能随 registry 切换：${name}。请保存标准 npm 地址，安装时配置镜像。`);
  }
  if (typeof entry.integrity !== 'string' || !entry.integrity.trim()) {
    throw new Error(`锁文件缺少完整性记录：${name}`);
  }
}

export function validateDependencies(manifest, lock) {
  const declared = lock.packages?.[''];
  if (!declared) throw new Error('前端锁文件缺少根依赖记录，请同步 package.json 和锁文件。');
  const mismatches = [];
  for (const section of ['dependencies', 'devDependencies']) {
    const wanted = manifest[section] ?? {};
    const locked = declared[section] ?? {};
    for (const name of new Set([...Object.keys(wanted), ...Object.keys(locked)])) {
      if (wanted[name] !== locked[name]) mismatches.push(name);
    }
  }
  if (mismatches.length) {
    throw new Error(`前端依赖清单与锁文件不一致：${mismatches.join(', ')}。请同步依赖清单与锁文件，不能打包不一致的版本。`);
  }
  for (const [name, entry] of Object.entries(lock.packages)) {
    if (name && !entry.link) validatePackage(name, entry);
  }
}

function check() {
  const folder = new URL('../frontend/', import.meta.url);
  const manifest = JSON.parse(readFileSync(new URL('package.json', folder), 'utf8'));
  const lock = JSON.parse(readFileSync(new URL('package-lock.json', folder), 'utf8'));
  validateDependencies(manifest, lock);
  console.log(`依赖清单与锁文件一致：${fileURLToPath(folder)}`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) check();
