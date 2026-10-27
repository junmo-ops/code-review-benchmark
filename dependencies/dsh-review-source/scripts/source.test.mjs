import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { digest, fingerprint, materialize, safePath, verifyPayload, verifySource } from './source.mjs';
import { validateDist } from './check-dist.mjs';
import { checkBuildNode } from './build.mjs';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-source-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'vendor'));
  fs.mkdirSync(path.join(root, 'scripts'));
  fs.writeFileSync(path.join(root, 'scripts/smoke.mjs'), '// target-check fixture\n');
  fs.mkdirSync(path.join(root, 'dist/packages/slot'), { recursive: true });
  fs.writeFileSync(path.join(root, 'vendor/main.js'), 'export const value = 1;\n');
  fs.writeFileSync(path.join(root, 'package.json'), '{"name":"fixture","version":"1.0.0"}');
  const plan = { files: { 'vendor/main.js': digest(path.join(root, 'vendor/main.js')) } };
  fs.writeFileSync(path.join(root, 'SOURCE_PLAN.json'), JSON.stringify(plan));
  fs.copyFileSync(path.join(root, 'SOURCE_PLAN.json'), path.join(root, 'dist/source-plan.json'));
  fs.copyFileSync(path.join(root, 'scripts/smoke.mjs'), path.join(root, 'dist/target-check.mjs'));
  fs.copyFileSync(path.join(root, 'vendor/main.js'), path.join(root, 'dist/packages/slot/main.js'));
  const manifest = { sourceFingerprint: fingerprint(root), sourcePlanSha256: digest(path.join(root, 'SOURCE_PLAN.json')),
    targetCheckSha256: digest(path.join(root, 'scripts/smoke.mjs')),
    runtimeValidation: { requiredNode: '22.22.x', status: 'required-at-install' },
    internalVersion: '1.0.0', modules: [{ location: 'node_modules/fixture', payload: 'dist/packages/slot', version: '1.0.0' }],
    files: { 'dist/packages/slot/main.js': digest(path.join(root, 'dist/packages/slot/main.js')) } };
  fs.writeFileSync(path.join(root, 'dist/runtime-manifest.json'), JSON.stringify(manifest));
  return { root, plan, manifest };
}

test('source inventory rejects corrupt, extra, archived and binary inputs instead of disguising them', t => {
  const { root, plan } = fixture(t);
  verifySource(root, plan);
  fs.appendFileSync(path.join(root, 'vendor/main.js'), 'changed');
  assert.throws(() => verifySource(root, plan), /digest/);
  fs.writeFileSync(path.join(root, 'vendor/main.js'), '\u007fELF');
  plan.files['vendor/main.js'] = digest(path.join(root, 'vendor/main.js'));
  assert.throws(() => verifySource(root, plan), /artifact/);
  fs.writeFileSync(path.join(root, 'vendor/extra.txt'), 'extra');
  assert.throws(() => verifySource(root, plan), /inventory/);
});

test('runtime restoration retains source bytes and rejects existing destinations', t => {
  const { root, manifest } = fixture(t);
  const destination = path.join(root, 'runtime');
  materialize(root, manifest, destination);
  assert.equal(digest(path.join(destination, 'node_modules/fixture/main.js')),
    digest(path.join(root, 'vendor/main.js')));
  assert.throws(() => materialize(root, manifest, destination), /already exists/);
});

test('runtime restoration rejects escaping manifest paths before copying', t => {
  const { root, manifest } = fixture(t);
  const destination = path.join(root, 'runtime');
  manifest.modules[0].location = 'node_modules/../../escape';
  assert.throws(() => materialize(root, manifest, destination), /Unsafe/);
  assert.ok(!fs.existsSync(destination));
  for (const name of ['/tmp/escape', '../escape', 'a/../b', 'a\\b'])
    assert.throws(() => safePath(root, name));
});

test('publish guard checks actual dist hashes and rejects stale source or versions', t => {
  const { root } = fixture(t);
  validateDist(root);
  fs.writeFileSync(path.join(root, 'package.json'), '{"version":"2.0.0"}');
  assert.throws(() => validateDist(root), /Stale/);
});

test('payload inventory rejects changed bytes and unlisted files', t => {
  const { root, manifest } = fixture(t);
  verifyPayload(root, manifest);
  fs.appendFileSync(path.join(root, 'dist/packages/slot/main.js'), 'changed');
  assert.throws(() => verifyPayload(root, manifest), /digest/);
  fs.writeFileSync(path.join(root, 'dist/packages/slot/extra.js'), 'extra');
  assert.throws(() => verifyPayload(root, manifest), /inventory/);
});

test('build compatibility does not lower the target runtime requirement', () => {
  checkBuildNode('22.9.0');
  checkBuildNode('22.22.0');
  for (const version of ['22.8.0', '22.19.0', '20.9.0', '25.8.2'])
    assert.throws(() => checkBuildNode(version), /target runtime remains Node 22.22/);
});

test('publishing rejects a missing or altered mandatory target checker', t => {
  const { root } = fixture(t);
  fs.writeFileSync(path.join(root, 'dist/target-check.mjs'), '// bypass\n');
  assert.throws(() => validateDist(root), /mandatory target runtime/);
});
