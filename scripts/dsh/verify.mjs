import { spawnSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validateDependencies } from '../check-dependencies.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
validateDependencies(JSON.parse(readFileSync(join(root, 'dependencies/dsh/package.json'), 'utf8')),
  JSON.parse(readFileSync(join(root, 'dependencies/dsh/package-lock.json'), 'utf8')));
const output = join(root, 'output/playwright', `dsh-contract-${new Date().toISOString().replaceAll(/[:.]/g, '-')}`);
mkdirSync(output, { recursive: true });
const started = Date.now();
const result = spawnSync(join(root, '.venv/bin/python'), ['-m', 'unittest', '-v',
  'test_dsh_baseline', 'test_dsh_linux', 'test_dsh_failures', 'test_dsh_process', 'test_skill_review',
  'test_dsh_pod', 'test_dsh_archive'], {
  cwd: root, stdio: 'inherit', timeout: 900000,
  env: { PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'C.UTF-8',
    PYTHONUTF8: '1', PYTHONUNBUFFERED: '1',
    PYTHONPATH: [join(root, 'backend/src'), join(root, 'backend/tests'), root].join(':'),
    BENCH_CASE_STORAGE_MODE: 'local', BENCH_RUN_STORAGE_MODE: 'local', BENCH_MODEL_STORAGE_MODE: 'local',
    DSH_EVIDENCE_ROOT: join(output, 'evidence') },
});
const code = result.status ?? 1;
writeFileSync(join(output, 'results.json'), JSON.stringify({ status: code ? 'failed' : 'passed',
  exitCode: code, error: result.error?.message, seconds: (Date.now() - started) / 1000,
  node: process.version, platform: process.platform, arch: process.arch,
  boundary: 'Real pinned Linux DSH and real Git; loopback-only deterministic model; no real model or COS.' }, null, 2));
console.log(`DSH 契约证据：${output}`);
process.exitCode = code;
