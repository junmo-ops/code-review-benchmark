import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { digest, files } from './source.mjs';

export function run(command, args, cwd, env = process.env, diagnostics = true) {
  const result = spawnSync(command, args, { cwd, env, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
  if (result.status !== 0) {
    const detail = diagnostics ? (result.stderr ?? '').slice(-6000)
      : String(result.stderr ?? '').match(/\bE[A-Z0-9_]+\b/)?.[0] ?? 'native input acquisition failed';
    throw new Error('Command failed: ' + command + '\n' + detail);
  }
  return result.stdout.trim();
}

export function compile(root, plan, moduleRoot) {
  const headers = path.join(root, 'vendor/node-headers');
  const source = path.join(root, 'vendor/native-loader');
  const output = path.join(moduleRoot('node-addon-require-builtin-linux-x64-gnu'), 'prebuilt/linux-x64-gnu-napi-v9.node');
  fs.mkdirSync(path.dirname(output), { recursive: true });
  run(process.env.CXX || 'c++', ['-shared', '-fPIC', '-pthread', '-std=c++17', '-fno-exceptions',
    '-fvisibility=hidden', '-DNAPI_VERSION=9', '-DNARB_BACKEND=1', '-DNARB_PRODUCT=1',
    '-DNODE_ADDON_API_DISABLE_CPP_EXCEPTIONS', '-DNODE_GYP_MODULE_NAME=require_builtin',
    '-I', headers, '-I', path.join(root, 'vendor/node-addon-api'),
    ...plan.nativeSources.map(name => path.join(source, name)), '-o', output], root);
  const system = moduleRoot('@deepseek-ai/node-addon-system-linux-x64');
  fs.mkdirSync(path.join(system, 'bin/glibc'), { recursive: true });
  const sources = moduleRoot('@deepseek-ai/node-addon-system');
  run(process.env.CC || 'cc', ['-shared', '-fPIC', '-DNAPI_VERSION=8', '-I', headers,
    path.join(sources, 'src/flock.c'), '-o', path.join(system, 'bin/glibc/system.node')], root);
  run(process.env.CC || 'cc', ['-O2', path.join(sources, 'src/main.c'),
    '-o', path.join(system, 'bin/landlock-run')], root);
  fs.chmodSync(path.join(system, 'bin/landlock-run'), 0o755);
  const systemMetadata = JSON.parse(fs.readFileSync(path.join(system, 'prebuilds.json')));
  systemMetadata.binaries = systemMetadata.binaries.filter(item => item.libc !== 'musl');
  systemMetadata.binaries.find(item => item.tool === 'landlock-run').kind = 'dynamic-glibc-source';
  fs.writeFileSync(path.join(system, 'prebuilds.json'), JSON.stringify(systemMetadata, null, 2) + '\n');
}

export function acquireSuppliers(root, plan, directory, options) {
  if (!process.env.npm_execpath) throw new Error('Run this build through npm run build');
  fs.mkdirSync(directory, { recursive: true });
  for (const name of ['package.json', 'package-lock.json'])
    fs.copyFileSync(path.join(root, 'vendor/native-inputs', name), path.join(directory, name));
  const args = [process.env.npm_execpath, 'ci', '--prefix', directory, '--ignore-scripts', '--engine-strict',
    '--no-audit', '--no-fund', '--fetch-retries=0', '--fetch-timeout=15000', '--registry=' + options.registry,
    '--@koromix:registry=' + options.registry, '--@vscode:registry=' + options.registry,
    '--@img:registry=' + options.registry, '--cache=' + options.cache];
  if (options.offline) args.push('--offline');
  run(process.execPath, args, directory, process.env, false);
  const actual = {};
  for (const name of plan.suppliers) {
    const folder = path.join(directory, 'node_modules', name);
    for (const file of files(folder)) actual['node_modules/' + name + '/' + file] = digest(path.join(folder, file));
  }
  const entries = value => JSON.stringify(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)));
  if (entries(actual) !== entries(plan.supplierFiles)) throw new Error('Native supplier contents differ from approved inputs');
  return plan.suppliers.map(name => ({ name, folder: path.join(directory, 'node_modules', name) }));
}
