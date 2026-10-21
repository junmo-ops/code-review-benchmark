import assert from 'node:assert/strict';
import { Buffer } from 'node:buffer';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { promisify } from 'node:util';

const execute = promisify(execFile);
const name = '@registry-switch/fixture';
const tarballPath = `${name}/-/fixture-1.0.0.tgz`;

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'bench-registry-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = join(root, 'source'), app = join(root, 'app');
  await mkdir(source);
  await mkdir(app);
  await writeFile(join(source, 'package.json'), JSON.stringify({ name, version: '1.0.0' }));
  await writeFile(join(source, 'index.js'), 'module.exports = 42;\n');
  await writeFile(join(root, 'user.npmrc'), '');
  await writeFile(join(root, 'global.npmrc'), '');
  const env = { ...process.env, npm_config_update_notifier: 'false',
    npm_config_userconfig: join(root, 'user.npmrc'),
    npm_config_globalconfig: join(root, 'global.npmrc') };
  const result = await execute('npm', ['pack', '--json', '--ignore-scripts'], {
    cwd: source, env, timeout: 15000,
  });
  const archive = await readFile(join(source, JSON.parse(result.stdout)[0].filename));
  const manifest = { name: 'registry-test-app', version: '1.0.0', dependencies: { [name]: '1.0.0' } };
  const lock = { name: manifest.name, version: manifest.version, lockfileVersion: 3, requires: true,
    packages: { '': manifest, [`node_modules/${name}`]: { version: '1.0.0',
      resolved: `https://registry.npmjs.org/${tarballPath}`,
      integrity: 'sha512-' + createHash('sha512').update(archive).digest('base64') } } };
  await writeFile(join(app, 'package.json'), JSON.stringify(manifest));
  return { root, app, env, archive, lock };
}

async function registry(t, prefix, archive) {
  const requests = [];
  const server = createServer((request, response) => {
    requests.push(request.url);
    if (decodeURIComponent(request.url) !== prefix + tarballPath) {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { 'content-type': 'application/octet-stream',
      'content-length': archive.length });
    response.end(archive);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => {
    server.closeAllConnections();
    return new Promise((resolve) => server.close(resolve));
  });
  return { url: `http://127.0.0.1:${server.address().port}${prefix}`, requests };
}

async function install(context, url) {
  await writeFile(join(context.app, 'package-lock.json'), JSON.stringify(context.lock));
  return execute('npm', ['ci', '--registry=' + url, '--cache=' + join(context.root, 'cache'),
    '--ignore-scripts', '--no-audit', '--no-fund', '--fetch-retries=0', '--fetch-timeout=5000'], {
    cwd: context.app, env: context.env, timeout: 15000,
  });
}

for (const [label, prefix] of [['external', '/npm/'], ['corporate', '/artifactory/api/npm/group-npm/']]) {
  test(`npm ci fetches locked scoped packages only from the selected ${label} mirror`, {
    skip: process.platform === 'win32', timeout: 30000,
  }, async (t) => {
    const context = await fixture(t);
    const mirror = await registry(t, prefix, context.archive);
    const before = JSON.stringify(context.lock);
    await install(context, mirror.url);
    assert.equal(await readFile(join(context.app, 'node_modules', name, 'index.js'), 'utf8'),
      'module.exports = 42;\n');
    assert.deepEqual(mirror.requests.map(decodeURIComponent), [prefix + tarballPath]);
    assert.equal(await readFile(join(context.app, 'package-lock.json'), 'utf8'), before);
  });
}

test('a selected mirror cannot bypass the locked integrity', {
  skip: process.platform === 'win32', timeout: 30000,
}, async (t) => {
  const context = await fixture(t);
  const mirror = await registry(t, '/npm/', Buffer.from('incorrect archive'));
  await assert.rejects(install(context, mirror.url), (error) => {
    assert.match(error.stderr, /EINTEGRITY/);
    return true;
  });
  assert.ok(mirror.requests.length > 0);
});
