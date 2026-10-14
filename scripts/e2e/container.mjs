import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { createServer } from 'node:net';
import { randomUUID } from 'node:crypto';
import { appendFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { setInterval, clearInterval } from 'node:timers';
const execute = promisify(execFile);
const limit = 2147483648;
const metricScript = `import json, os
from pathlib import Path
p=Path('/sys/fs/cgroup')
def number(name):
 try: return int((p/name).read_text().strip())
 except (OSError, ValueError): return None
def fields(name):
 try: return {k:int(v) for k,v in (line.split() for line in (p/name).read_text().splitlines())}
 except (OSError, ValueError): return {}
s=os.statvfs(os.environ['E2E_DATA'])
print(json.dumps(dict(memoryCurrent=number('memory.current'), memoryMax=number('memory.max'),
 memoryPeak=number('memory.peak'), swapMax=number('memory.swap.max'), swapCurrent=number('memory.swap.current'), events=fields('memory.events'), stat=fields('memory.stat'),
 pids=number('pids.current'), diskAvailable=s.f_bavail*s.f_frsize)))`;
export class ContainerHarness {
  constructor(root, output) {
    this.root = root; this.output = output;
    this.context = process.env.E2E_DOCKER_CONTEXT || (process.platform === 'darwin' ? 'colima-ccrab' : 'default');
    this.image = process.env.E2E_DOCKER_IMAGE || 'bench-e2e:py311';
    this.name = 'bench-e2e-' + randomUUID().slice(0, 12);
    this.inspectHistory = []; this.samples = []; this.restartCount = 0;
  }
  async docker(...args) {
    const result = await execute('docker', ['--context', this.context, ...args], { timeout: 30000, maxBuffer: 4 * 1024 * 1024 });
    return result.stdout;
  }
  async prepare(data) {
    if (process.platform === 'win32') throw new Error('容器验收请在 WSL/Linux 或 macOS 运行，需使用 Linux 绝对路径挂载隔离数据。');
    this.data = data;
    await this.docker('image', 'inspect', this.image);
    this.port = await freePort();
    await this.docker('network', 'create', this.name);
    this.networkCreated = true;
    await this.docker('create', '--name', this.name, '--user', `${process.getuid?.() ?? 0}:${process.getgid?.() ?? 0}`,
      '--memory', '2g', '--memory-swap', '2g',
      '--cpus', '2', '--pids-limit', '256', '--network', this.name,
      '-p', `127.0.0.1:${this.port}:${this.port}`,
      '--mount', `type=bind,src=${this.root},dst=${this.root},readonly`,
      '--mount', `type=bind,src=${this.output},dst=${this.output}`,
      '-w', this.root, '-e', 'E2E_SIMULATED_LCR=1', '-e', `E2E_DATA=${data}`,
      '-e', `E2E_LARGE_REPOSITORY_MIB=${process.env.E2E_REAL_SUITE ? '0' : process.env.E2E_LARGE_REPOSITORY_MIB || '128'}`,
      '-e', `E2E_VERIFY_WORKSPACE=${process.env.E2E_REAL_SUITE ? '1' : '0'}`,
      '-e', `E2E_STAGE_TIMEOUT_SECONDS=${process.env.E2E_REAL_SUITE ? '900' : '120'}`,
      this.image, 'python', join(this.root, 'scripts/e2e/container_entry.py'), '--data', data,
      '--host', '0.0.0.0', '--port', String(this.port));
    this.created = true;
    const info = await this.inspect('created');
    if (info.HostConfig.Memory !== limit || info.HostConfig.MemorySwap !== limit)
      throw new Error('Docker 未应用 2 GiB 内存与禁用额外 swap 的限制');
    this.timer = setInterval(() => void this.sample(), 1000);
  }
  launch() {
    return spawn('docker', ['--context', this.context, 'start', '-a', this.name], { cwd: this.root });
  }
  async checkWorktreeRecovery() {
    await this.docker('exec', this.name, 'python', join(this.root, 'scripts/e2e/check_interrupted_workspaces.py'),
      '--output', join(this.output, 'cleanup-probe.json'));
  }
  async sample() {
    if (this.sampling) return;
    this.sampling = true;
    try {
      const value = JSON.parse(await this.docker('exec', this.name, 'python', '-c', metricScript));
      value.timestamp = new Date().toISOString();
      value.scenario = this.scenario || 'startup';
      value.restartCount = this.restartCount;
      this.samples.push(value);
      await appendFile(join(this.output, 'container-resources.jsonl'), JSON.stringify(value) + '\n');
    } catch (error) {
      this.lastSampleError = error.message;
    } finally { this.sampling = false; }
  }
  async inspect(phase) {
    const [value] = JSON.parse(await this.docker('inspect', this.name));
    this.inspectHistory.push({ phase, state: value.State, memory: value.HostConfig.Memory,
      memorySwap: value.HostConfig.MemorySwap, image: value.Image });
    return value;
  }
  async killForRestart() {
    await this.sample();
    await this.docker('kill', '--signal', 'KILL', this.name);
    await this.inspect('intentional-kill-' + (++this.restartCount));
  }
  async close() {
    clearInterval(this.timer);
    try {
      if (this.created) {
        await this.sample();
        await this.inspect('before-cleanup');
        await this.docker('stop', '--time', '15', this.name);
        await this.inspect('after-cleanup-stop');
        await this.docker('rm', this.name);
      }
    } finally {
      if (this.networkCreated) await this.docker('network', 'rm', this.name);
      await this.saveReport();
    }
    if (!this.samples.length) throw new Error('未采集到容器 cgroup 数据');
    if (this.inspectHistory.some((v) => v.state.OOMKilled) || this.samples.some((v) => v.events.oom_kill > 0))
      throw new Error('验收容器发生 OOM，详见 container-resources.jsonl 和 container-report.json');
    if (this.samples.some((v) => v.swapMax !== 0)) throw new Error('cgroup 未禁用额外 swap');
    if (this.samples.some((v) => v.memoryMax !== limit)) throw new Error('运行中的 cgroup 内存限制与目标不符');
  }
  async saveReport() {
    const numeric = this.samples.filter((v) => Number.isFinite(v.memoryCurrent));
    const report = { context: this.context, image: this.image, container: this.name,
      inspectHistory: this.inspectHistory, samples: this.samples.length, restartCount: this.restartCount,
      phases: summarize(this.samples),
      maxObservedMemory: Math.max(0, ...numeric.map((v) => v.memoryCurrent)),
      maxKernelPeak: Math.max(0, ...numeric.map((v) => v.memoryPeak || 0)),
      lastSampleError: this.lastSampleError ?? null,
      boundary: 'Linux arm64, Python 3.11, simulated LCR and judge, local storage; no real model or COS calls' };
    await writeFile(join(this.output, 'container-report.json'), JSON.stringify(report, null, 2));
  }
}
async function freePort() {
  const server = createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return port;
}

export async function reachable(url) {
  const { get } = await import('node:http');
  return new Promise((resolve) => {
    const request = get(url + '/api/session', (reply) => { reply.resume(); resolve(reply.statusCode === 200); });
    request.setTimeout(1500, () => request.destroy());
    request.on('error', () => resolve(false));
  });
}

function summarize(samples) {
  const phases = {};
  for (const sample of samples) {
    const row = phases[sample.scenario] ?? { samples: 0, maxMemory: 0, maxAnon: 0, maxFile: 0, maxPressure: 0, oomKills: 0 };
    row.samples++;
    row.maxMemory = Math.max(row.maxMemory, sample.memoryCurrent || 0);
    row.maxAnon = Math.max(row.maxAnon, sample.stat.anon || 0);
    row.maxFile = Math.max(row.maxFile, sample.stat.file || 0);
    row.maxPressure = Math.max(row.maxPressure, sample.events.max || 0);
    row.oomKills = Math.max(row.oomKills, sample.events.oom_kill || 0);
    row.lastMemory = sample.memoryCurrent;
    phases[sample.scenario] = row;
  }
  return phases;
}
