import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { audit, failureCode, npmInvocation, packagesForLinux, query, registryUrl } from './registry-audit.mjs';

const entry = { version: '1.2.3', integrity: 'sha512-fixture' };
const item = { name: '@fixture/available', ...entry };
const execute = promisify(execFile);

test('Windows calls the npm JavaScript entry with Node instead of invoking a command shim', () => {
  const node = 'C:\\Program Files\\nodejs\\node.exe';
  const cli = 'C:\\Program Files\\nodejs\\node_modules\\npm\\bin\\npm-cli.js';
  const args = ['view', '@fixture/pkg@1.2.3', '--registry=https://example.invalid/path&literal/'];
  assert.deepEqual(npmInvocation(args, { npm_execpath: cli }, 'win32', file => file === cli, node),
    { command: node, args: [cli, ...args] });
  assert.deepEqual(npmInvocation(args, {}, 'win32', file => file === cli, node),
    { command: node, args: [cli, ...args] });
});

test('Windows finds npm through case-insensitive PATH and AppData, preserving spaces as literal arguments', () => {
  const node = 'C:\\standalone\\node.exe';
  const cli = 'D:\\Tools & Kits\\nodejs\\node_modules\\npm\\bin\\npm-cli.js';
  assert.deepEqual(npmInvocation(['view'], { Path: 'C:\\other;"D:\\Tools & Kits\\nodejs"' },
    'win32', file => file === cli, node), { command: node, args: [cli, 'view'] });
  const roaming = 'C:\\Users\\test\\AppData\\Roaming\\npm\\node_modules\\npm\\bin\\npm-cli.js';
  assert.deepEqual(npmInvocation([], { APPDATA: 'C:\\Users\\test\\AppData\\Roaming' },
    'win32', file => file === roaming, node), { command: node, args: [roaming] });
});

test('POSIX keeps its npm executable fallback and missing Windows npm fails explicitly', () => {
  assert.deepEqual(npmInvocation(['view'], {}, 'linux'), { command: 'npm', args: ['view'] });
  assert.throws(() => npmInvocation([], {}, 'win32', () => false), { code: 'NPM_NOT_FOUND' });
});

test('selects exact Linux runtime versions, deduplicates and does not treat optional as unnecessary', () => {
  const packages = packagesForLinux({ lockfileVersion: 3, packages: {
    '': {}, 'node_modules/one': entry, 'node_modules/two/node_modules/one': entry,
    'node_modules/native': { ...entry, optional: true, os: ['linux'], cpu: ['x64'], libc: ['glibc'] },
    'node_modules/win': { ...entry, optional: true, os: ['win32'] },
    'node_modules/not-linux': { ...entry, os: ['!linux'] },
    'node_modules/development': { ...entry, dev: true },
    'node_modules/link': { link: true },
  } });
  assert.deepEqual(packages.map(p => p.name), ['native', 'one']);
});

test('rejects malformed lock entries and conflicting hashes without changing versions', () => {
  assert.throws(() => packagesForLinux({ lockfileVersion: 2 }), /lockfile/);
  for (const change of [{ version: '*' }, { integrity: undefined }]) {
    assert.throws(() => packagesForLinux({ lockfileVersion: 3,
      packages: { 'node_modules/pkg': { ...entry, ...change } } }), /Invalid/);
  }
  assert.throws(() => packagesForLinux({ lockfileVersion: 3, packages: {
    'node_modules/pkg': entry, 'node_modules/other/node_modules/pkg': { ...entry, integrity: 'different' },
  } }), /Conflicting/);
});

test('does not put embedded registry credentials into report URLs', () => {
  for (const value of ['https://user:secret@example.invalid/', 'https://example.invalid/?token=secret', 'file:///tmp'])
    assert.throws(() => registryUrl(value));
  assert.equal(registryUrl('https://example.invalid/npm/'), 'https://example.invalid/npm/');
});

test('keeps missing and permission errors distinct and never returns raw npm credentials', () => {
  assert.equal(failureCode({ stdout: '{"error":{"code":"E404","summary":"secret"}}' }), 'E404');
  assert.equal(failureCode({ stderr: 'npm error code E403 token=secret' }), 'E403');
  assert.equal(failureCode({ killed: true }), 'TIMEOUT');
});

test('reports upstream source candidates separately, preserving exact missing external versions', async () => {
  const requests = [];
  const result = await audit([item, { ...item, name: '@deepseek-ai/dsh' }], 'http://fixture.invalid/', {
    inspect: async candidate => { requests.push(candidate); return { status: 'missing_or_not_visible', code: 'E404' }; },
  });
  assert.deepEqual(requests, [item]);
  assert.equal(result.deepseekSourceCandidates, 1);
  assert.deepEqual(result.counts, { missing_or_not_visible: 1 });
  assert.equal(result.results[0].version, '1.2.3');
});

test('real npm metadata queries override a conflicting scoped registry, do not fetch archives and classify failures', {
  timeout: 30000,
}, async t => {
  const directory = await mkdtemp(join(tmpdir(), 'dsh-registry-audit-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const requests = [];
  const server = createServer((request, response) => {
    const name = decodeURIComponent(request.url).split('/').at(-1);
    requests.push(request.url);
    if (name === 'missing') return response.writeHead(404).end('{"error":"not_found"}');
    if (name === 'denied') return response.writeHead(403).end('{"error":"forbidden"}');
    const version = { name: '@fixture/' + name, version: '1.2.3',
      dist: { integrity: name === 'mismatch' ? 'sha512-wrong' : 'sha512-fixture',
        tarball: 'http://127.0.0.1:9/never-download.tgz' } };
    response.setHeader('content-type', 'application/json');
    response.end(JSON.stringify({ name: version.name, 'dist-tags': { latest: '1.2.3' }, versions: { '1.2.3': version } }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => {
    server.closeAllConnections();
    return new Promise(resolve => server.close(resolve));
  });
  await writeFile(join(directory, 'user.npmrc'), '@fixture:registry=http://127.0.0.1:9/forbidden/\n');
  await writeFile(join(directory, 'global.npmrc'), '');
  const env = { ...process.env, npm_config_userconfig: join(directory, 'user.npmrc'),
    npm_config_globalconfig: join(directory, 'global.npmrc'), npm_config_cache: join(directory, 'cache') };
  const registry = `http://127.0.0.1:${server.address().port}/npm/`;
  const results = [];
  for (const name of ['available', 'missing', 'denied', 'mismatch'])
    results.push(await query({ ...item, name: '@fixture/' + name }, registry, 2000, env));
  assert.deepEqual(results, [{ status: 'metadata_matches' },
    { status: 'missing_or_not_visible', code: 'E404' }, { status: 'access_denied', code: 'E403' },
    { status: 'metadata_mismatch' }]);
  assert.equal(requests.length, 4);
  assert.ok(requests.every(url => url.startsWith('/npm/') && !url.includes('.tgz')));
  const lock = { lockfileVersion: 3, packages: { '': {}, 'node_modules/@deepseek-ai/dsh': entry,
    ...Object.fromEntries(['available', 'missing', 'denied'].map(name => ['node_modules/@fixture/' + name, entry])) } };
  const lockPath = join(directory, 'package-lock.json'), output = join(directory, 'report.json');
  const before = JSON.stringify(lock);
  await writeFile(lockPath, before);
  const script = fileURLToPath(new URL('./registry-audit.mjs', import.meta.url));
  await assert.rejects(execute(process.execPath, [script, '--registry', registry, '--lock', lockPath,
    '--output', output, '--timeout', '2000', '--details'], { env, timeout: 15000 }), error => {
    assert.equal(error.code, 2);
    assert.doesNotMatch(error.stdout + error.stderr, /authToken|password|secret/);
    assert.match(error.stdout, /@fixture\/missing/);
    assert.match(error.stdout, /@fixture\/denied/);
    return true;
  });
  const saved = await readFile(output, 'utf8');
  const report = JSON.parse(saved);
  assert.equal(report.deepseekSourceCandidates, 1);
  assert.deepEqual(report.counts, { metadata_matches: 1, access_denied: 1, missing_or_not_visible: 1 });
  assert.equal(await readFile(lockPath, 'utf8'), before);
  assert.equal(requests.length, 7);
  await assert.rejects(execute(process.execPath, [script, '--registry', 'http://127.0.0.1:9/',
    '--lock', lockPath, '--output', output], { env, timeout: 2000 }), error => error.code === 1);
  assert.equal(await readFile(output, 'utf8'), saved);
});
