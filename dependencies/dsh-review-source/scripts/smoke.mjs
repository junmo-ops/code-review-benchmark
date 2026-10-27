import path from 'node:path';
import fs from 'node:fs';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

function run(args, cwd, env) {
  const result = spawnSync(process.execPath, args, { cwd, env, encoding: 'utf8', timeout: 30000,
    maxBuffer: 4 * 1024 * 1024 });
  if (result.status !== 0) throw new Error('Target runtime check failed: ' + (result.stderr || result.error?.message || 'nonzero exit').slice(-4000));
  return result.stdout.trim();
}

export function smoke(runtime) {
  if (!process.versions.node.startsWith('22.22.')) throw new Error('Target DSH runtime requires Node 22.22.x; build-host compatibility is not runtime compatibility');
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-target-check-'));
  try { return check(runtime, home); }
  finally { fs.rmSync(home, { recursive: true, force: true }); }
}

function check(runtime, home) {
  const env = { PATH: process.env.PATH, HOME: home, DSH_HOME: home, DSH_TELEMETRY_DISABLED: '1', LANG: 'C.UTF-8' };
  const entry = path.join(runtime, 'node_modules/@deepseek-ai/dsh/lib/bin.js');
  if (run([entry, '--version'], runtime, env) !== '0.2.1-rc.2') throw new Error('Wrong DSH version');
  const script = `
const {createRequire} = await import('node:module');
const require = createRequire(process.cwd() + '/package.json');
const loader = require('node-addon-require-builtin');
loader.requireBuiltin('internal/modules/esm/loader');
loader.requireBuiltin('internal/modules/cjs/loader');
if (loader.getBindingInfo().product !== 'require-builtin') throw new Error('Wrong native product');
const {tryLock} = require('@deepseek-ai/node-addon-system-linux-x64/bin/glibc/system.node');
if (typeof tryLock !== 'function') throw new Error('No native flock');
const fs = await import('node:fs');
const fd = fs.openSync(process.env.HOME + '/flock-probe', 'w');
try { await new Promise((resolve, reject) => tryLock(fd, errno => errno ? reject(new Error('flock failed: ' + errno)) : resolve())); }
finally { fs.closeSync(fd); }
const {rgPath} = await import('@vscode/ripgrep');
const {execFileSync} = await import('node:child_process');
execFileSync(rgPath, ['--version']);
const koffi = require('koffi');
if (typeof koffi.load !== 'function') throw new Error('No native FFI');
const sharp = require('sharp');
await sharp({create:{width:1,height:1,channels:3,background:'#ffffff'}}).png().toBuffer();
`;
  run(['--input-type=module', '-e', script], runtime, env);
  return { status: 'passed', node: process.versions.node, version: '0.2.1-rc.2',
    components: ['private-module-loader', 'flock', 'ripgrep', 'koffi', 'sharp'] };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (!process.argv[2]) throw new Error('Runtime directory is required');
    console.log(JSON.stringify(smoke(path.resolve(process.argv[2]))));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
