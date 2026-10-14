import { spawnSync } from 'node:child_process';
import { copyFile, mkdir } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const folder = join(root, 'output/container-image-context');
await mkdir(folder, { recursive: true });
await Promise.all([
  copyFile(join(root, 'scripts/e2e/Dockerfile'), join(folder, 'Dockerfile')),
  copyFile(join(root, 'backend/requirements.txt'), join(folder, 'requirements.txt')),
]);
const context = process.env.E2E_DOCKER_CONTEXT || (process.platform === 'darwin' ? 'colima-ccrab' : 'default');
const image = process.env.E2E_DOCKER_IMAGE || 'bench-e2e:py311';
const mirror = process.env.E2E_PIP_INDEX_URL ? ['--build-arg', 'PIP_INDEX_URL=' + process.env.E2E_PIP_INDEX_URL] : [];
const result = spawnSync('docker', ['--context', context, 'build', ...mirror, '-t', image, folder], { cwd: root, stdio: 'inherit' });
if (result.error) throw result.error;
if (result.status !== 0) process.exitCode = result.status || 1;
