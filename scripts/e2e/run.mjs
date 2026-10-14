import { spawn, spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { scenarios } from './scenarios.mjs';
import { exploration } from './exploration.mjs';
import { ContainerHarness, reachable } from './container.mjs';
import { containerScenarios } from './container-scenarios.mjs';
import { realScenarios } from './real-repositories.mjs';
const containerMode = process.argv.includes('--container');
const exploring = process.argv.includes('--explore') || containerMode;
const realMode = Boolean(process.env.E2E_REAL_SUITE);
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const windows = process.platform === 'win32';
const python = join(root, '.venv', windows ? 'Scripts/python.exe' : 'bin/python');
const output = join(root, 'output/playwright', new Date().toISOString().replaceAll(/[:.]/g, '-'));
let server;
let containerRuntime;
let browser;
let context;
const results = [];
const errors = [];
let serverError;
let cleaned = false;
let fatalError = null;
async function playwright() {
  const require = createRequire(import.meta.url);
  try {
    return require(process.env.E2E_PLAYWRIGHT_MODULE || 'playwright');
  } catch {
    throw new Error('先运行 npm run e2e:setup 安装独立浏览器回归依赖；无需修改 frontend 依赖。');
  }
}
async function serverUrl(data) {
  const deadline = Date.now() + (containerMode ? 180000 : 30000);
  while (Date.now() < deadline) {
    if (serverError) throw serverError;
    if (server.exitCode !== null) throw new Error(`回归服务器退出：${server.exitCode}，查看 backend.log`);
    try {
      const url = JSON.parse(await readFile(join(data, 'server.json'), 'utf8')).url;
      if (!containerMode || await reachable(url)) return url;
    } catch { /* Metadata may not exist until the isolated server binds. */ }
    await delay(200);
  }
  throw new Error('回归服务器启动超时，查看 backend.log');
}
async function execute(page, fixtures) {
  const only = process.argv.find((argument) => argument.startsWith('--only='))?.slice(7);
  const available = availableScenarios();
  if (only && !available.some(([name]) => name === only)) throw new Error(`未知验收场景：${only}`);
  const selected = available.filter(([name]) => !only || name.startsWith('setup-') || name === only);
  for (const [name, scenario] of selected) {
    const started = Date.now();
    const errorOffset = errors.length;
    console.log(`RUN ${name}`);
    if (containerRuntime) containerRuntime.scenario = name;
    try {
      await scenario({ page, fixtures, restart: () => restartServer(page, fixtures) });
      if (errors.length > errorOffset) throw new Error(errors.slice(errorOffset).join('\n'));
      results.push({ name, status: 'passed', milliseconds: Date.now() - started });
      await page.screenshot({ path: join(output, `${name}.png`), fullPage: true });
      console.log(`PASS ${name}`);
    } catch (error) {
      results.push({ name, status: 'failed', error: error.stack, milliseconds: Date.now() - started });
      await page.screenshot({ path: join(output, `${name}-failed.png`), fullPage: true }).catch(() => {});
      await writeFile(join(output, `${name}-page.txt`), await page.locator('body').innerText().catch(() => 'unavailable'));
      console.error(`FAIL ${name}: ${error.message}`);
      if (!exploring || name.startsWith("setup-")) throw error;
      process.exitCode = 1;
      await page.goto("/models");
    }
  }
}
function availableScenarios() {
  if (realMode) return realScenarios;
  return containerMode ? containerScenarios : exploring ? exploration : scenarios;
}
async function main() {
  if (realMode && !containerMode) throw new Error('真实仓库验收请使用 e2e:container，以限制后端资源并隔离数据。');
  const { chromium } = await playwright();
  await mkdir(output, { recursive: true });
  const build = spawnSync('npm', ['--prefix', 'frontend', 'run', 'build', '--', '--outDir', join(output, 'frontend-dist')], { cwd: root, stdio: 'inherit', shell: windows });
  if (build.status !== 0) throw new Error('前端构建失败，未启动回归');
  const data = join(output, 'runtime');
  if (containerMode) {
    containerRuntime = new ContainerHarness(root, output);
    await containerRuntime.prepare(data);
  }
  launchServer(data);
  const url = await serverUrl(data);
  if (containerRuntime) await containerRuntime.checkWorktreeRecovery();
  browser = await chromium.launch({ headless: !process.argv.includes('--headed'),
    ...(process.env.E2E_BROWSER_CHANNEL ? { channel: process.env.E2E_BROWSER_CHANNEL } : {}) });
  context = await browser.newContext({ baseURL: url, viewport: { width: 1440, height: 1000 } });
  // All browser requests must stay inside this isolated local server.
  await context.route('**/*', (route) => new URL(route.request().url()).origin === url ? route.continue() : route.abort());
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on('pageerror', (error) => errors.push(error.message));
  context.on("page", (tab) => tab.on("pageerror", (error) => errors.push(error.message)));
  await execute(page, join(data, 'fixtures'));
}
function launchServer(data, extra = []) {
  const log = createWriteStream(join(output, 'backend.log'), { flags: 'a' });
  server = containerMode ? containerRuntime.launch() : spawn(python, [join(root, 'scripts/e2e/server.py'), '--data', data, ...extra], { cwd: root, detached: !windows });
  server.once('error', (error) => { serverError = error; });
  server.stdout.pipe(log, { end: false }); server.stderr.pipe(log, { end: false });
  server.once('close', () => log.end());
}
function killTree(pid) {
  if (windows) spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore' });
  else {
    try { process.kill(-pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
  }
}
async function restartServer(page, fixtures) {
  if (containerMode) return restartContainer(fixtures);
  const data = resolve(fixtures, '..');
  const { state } = await import('./helpers.mjs');
  const snapshot = await state(page);
  const children = snapshot.jobs.filter((entry) => ['running', 'cancelling'].includes(entry.status) && entry.pid);
  const { url } = JSON.parse(await readFile(join(data, 'server.json'), 'utf8'));
  const old = server;
  const exited = new Promise((done) => old.once('close', done));
  killTree(old.pid);
  for (const child of children) killTree(child.pid);
  await exited;
  await rm(join(data, 'server.json'));
  serverError = undefined;
  launchServer(data, ['--reuse', '--port', new URL(url).port]);
  return serverUrl(data);
}
async function restartContainer(fixtures) {
  const exited = new Promise((done) => server.once('close', done));
  await containerRuntime.killForRestart();
  await exited;
  const data = resolve(fixtures, '..');
  await rm(join(data, 'server.json'));
  serverError = undefined;
  launchServer(data);
  return serverUrl(data);
}
async function stopBackend() {
  if (containerRuntime) {
    try { await containerRuntime.close(); }
    catch (error) { fatalError ??= error.stack; process.exitCode = 1; }
  }
  if (!containerMode && server?.pid && server.exitCode === null && server.signalCode === null) {
    if (windows) spawnSync('taskkill', ['/PID', String(server.pid), '/T', '/F'], { stdio: 'ignore' });
    else {
      try { process.kill(-server.pid, 'SIGTERM'); } catch (error) { if (error.code !== 'ESRCH') console.error(error.message); }
    }
  }
}
async function cleanup() {
  if (cleaned) return;
  cleaned = true;
  if (context) await context.tracing.stop({ path: join(output, 'trace.zip') }).catch(() => {});
  if (browser) await browser.close().catch((error) => { fatalError = error.stack; process.exitCode = 1; });
  await stopBackend();
  await mkdir(output, { recursive: true });
  if (errors.length && !process.exitCode) process.exitCode = 1;
  await writeFile(join(output, 'results.json'), JSON.stringify({ status: process.exitCode ? "failed" : "passed", mode: containerMode ? "container-2g" : exploring ? "explore" : "regression", results, browserErrors: errors, fatalError }, null, 2));
  console.log(`回归产物：${output}`);
}
for (const signal of ["SIGINT", "SIGTERM"]) process.once(signal, async () => {
  process.exitCode = 130;
  await cleanup();
  process.exit(130);
});
try { await main(); }
catch (error) { fatalError = error.stack; console.error(error.stack); process.exitCode = 1; }
finally { await cleanup(); }
