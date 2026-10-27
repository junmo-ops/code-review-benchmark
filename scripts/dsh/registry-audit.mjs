import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { lstat, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve, win32 } from 'node:path';
import { parseArgs, promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const execute = promisify(execFile);
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const target = { os: 'linux', cpu: 'x64', libc: 'glibc' };

export function npmInvocation(args, env = process.env, platform = process.platform,
  exists = existsSync, node = process.execPath) {
  if (env.npm_execpath?.endsWith('npm-cli.js') && exists(env.npm_execpath))
    return { command: node, args: [env.npm_execpath, ...args] };
  if (platform !== 'win32') return { command: 'npm', args };
  const pathValue = Object.entries(env).find(([name]) => name.toLowerCase() === 'path')?.[1] ?? '';
  const folders = [win32.dirname(node), env.APPDATA && win32.join(env.APPDATA, 'npm'),
    ...pathValue.split(';').filter(Boolean).map(folder => folder.replace(/^"|"$/g, ''))].filter(Boolean);
  const cli = folders.map(folder => win32.join(folder, 'node_modules/npm/bin/npm-cli.js')).find(exists);
  if (!cli) throw Object.assign(new Error('Cannot locate npm-cli.js'), { code: 'NPM_NOT_FOUND' });
  return { command: node, args: [cli, ...args] };
}

function matches(values, actual) {
  return !values || (!values.includes('!' + actual)
    && (values.includes(actual) || values.every(value => value.startsWith('!'))));
}

export function packagesForLinux(lock) {
  if (lock.lockfileVersion !== 3 || !lock.packages) throw new Error('Expected npm lockfile v3');
  const packages = new Map();
  for (const [location, entry] of Object.entries(lock.packages)) {
    if (!location || entry.link || entry.dev) continue;
    if (!Object.entries(target).every(([key, value]) => matches(entry[key], value))) continue;
    const name = location.split('node_modules/').at(-1);
    if (!/^(@[a-zA-Z0-9._-]+\/)?[a-zA-Z0-9._-]+$/.test(name)
      || !/^\d+\.\d+\.\d+([+-][a-zA-Z0-9.+-]+)?$/.test(entry.version)
      || !entry.integrity) throw new Error('Invalid locked registry dependency: ' + location);
    const spec = name + '@' + entry.version;
    const item = { name, version: entry.version, integrity: entry.integrity };
    if (packages.has(spec) && packages.get(spec).integrity !== item.integrity)
      throw new Error('Conflicting locked integrity: ' + spec);
    packages.set(spec, item);
  }
  return [...packages.values()].sort((a, b) => a.name.localeCompare(b.name)
    || a.version.localeCompare(b.version));
}

export function registryUrl(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new Error('Registry must be an HTTP(S) URL without credentials, query or fragment');
  return url.href;
}

export function failureCode(error) {
  try {
    const value = JSON.parse(error.stdout).error?.code;
    if (/^[A-Z][A-Z0-9_]{1,40}$/.test(value)) return value;
  } catch {
    // npm may only emit its structured error code to stderr.
  }
  return String(error.stderr ?? '').match(/\b(E[A-Z0-9_]{2,40})\b/)?.[1]
    ?? (error.killed ? 'TIMEOUT' : 'QUERY_FAILED');
}

export async function query(item, registry, timeout, env = process.env) {
  const args = ['view', item.name + '@' + item.version, 'version', 'dist.integrity', '--json',
    '--registry=' + registry, '--fetch-retries=0', '--fetch-timeout=' + timeout, '--prefer-online'];
  if (item.name.startsWith('@')) args.push('--' + item.name.split('/')[0] + ':registry=' + registry);
  try {
    const invocation = npmInvocation(args, env);
    const result = await execute(invocation.command, invocation.args, { timeout: timeout + 5000, maxBuffer: 1024 * 1024,
      env: { ...env, npm_config_update_notifier: 'false' } });
    const metadata = JSON.parse(result.stdout);
    const matchesLock = metadata.version === item.version && metadata['dist.integrity'] === item.integrity;
    return { status: matchesLock ? 'metadata_matches' : 'metadata_mismatch' };
  } catch (error) {
    const code = failureCode(error);
    const status = { E404: 'missing_or_not_visible', E401: 'access_denied', E403: 'access_denied' }[code]
      ?? 'query_failed';
    return { status, code };
  }
}

export async function audit(packages, registry, { concurrency = 4, timeout = 15000, inspect = query } = {}) {
  const external = packages.filter(item => !item.name.startsWith('@deepseek-ai/'));
  const results = new Array(external.length);
  let cursor = 0;
  await Promise.all(Array.from({ length: Math.min(concurrency, external.length) }, async () => {
    while (cursor < external.length) {
      const index = cursor++;
      const item = external[index];
      results[index] = { ...item, ...await inspect(item, registry, timeout) };
    }
  }));
  const counts = {};
  for (const item of results) counts[item.status] = (counts[item.status] ?? 0) + 1;
  return { target, checkedPackages: external.length,
    deepseekSourceCandidates: packages.filter(item => item.name.startsWith('@deepseek-ai/')).length,
    counts, results };
}

function integer(value, fallback, maximum) {
  const number = value === undefined ? fallback : Number(value);
  if (!Number.isInteger(number) || number < 1 || number > maximum) throw new Error('Invalid numeric option');
  return number;
}

async function requireNewOutput(file) {
  try {
    await lstat(file);
  } catch (error) {
    if (error.code === 'ENOENT') return;
    throw error;
  }
  throw new Error('Output already exists');
}

async function main() {
  const { values } = parseArgs({ options: { registry: { type: 'string' }, output: { type: 'string' },
    lock: { type: 'string' }, concurrency: { type: 'string' }, timeout: { type: 'string' },
    details: { type: 'boolean' } } });
  if (!values.registry || !values.output) throw new Error('Required: --registry URL --output PATH');
  const registry = registryUrl(values.registry);
  const lockPath = resolve(values.lock ?? join(root, 'dependencies/dsh/package-lock.json'));
  const outputPath = resolve(values.output);
  if (outputPath === lockPath) throw new Error('Output must not replace the lockfile');
  await requireNewOutput(outputPath);
  npmInvocation([]);
  const bytes = await readFile(lockPath);
  const report = await audit(packagesForLinux(JSON.parse(bytes)), registry, {
    concurrency: integer(values.concurrency, 4, 8), timeout: integer(values.timeout, 15000, 60000) });
  const record = { ...report, registry, lockSha256: createHash('sha256').update(bytes).digest('hex'),
    boundary: 'Released Linux runtime metadata only. No package installation, archive download, lifecycle scripts, model or COS calls. Upstream source/build-tool dependencies, artifact downloads and Kylin execution remain unverified.' };
  await writeFile(outputPath, JSON.stringify(record, null, 2) + '\n', { flag: 'wx' });
  console.log(JSON.stringify({ output: outputPath, checkedPackages: report.checkedPackages,
    deepseekSourceCandidates: report.deepseekSourceCandidates, counts: report.counts }));
  if (values.details) {
    for (const item of report.results.filter(item => item.status !== 'metadata_matches'))
      console.log(JSON.stringify({ name: item.name, version: item.version, status: item.status, code: item.code }));
  }
  if (report.results.some(item => item.status !== 'metadata_matches')) process.exitCode = 2;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    console.error(error.code === 'NPM_NOT_FOUND'
      ? 'Cannot find npm-cli.js. Check your Node/npm installation with node --version and npm --version.'
      : 'Registry audit failed. Check the options, npm availability, lockfile and output path; existing output is never overwritten. Raw npm diagnostics are not printed because they may contain credentials.');
    process.exitCode = 1;
  });
}
