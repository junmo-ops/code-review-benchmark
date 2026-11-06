import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { companyLock } from './company-npm-lock.mjs';

const location = 'node_modules/@fixture/artifact';
const registry = 'http://company.example/npm/';
const sri = value => 'sha512-' + createHash('sha512').update(value).digest('base64');

function fixture() {
  const entry = { version: '1.0.0', resolved: 'https://registry.npmjs.org/@fixture/artifact/-/artifact-1.0.0.tgz',
    integrity: sri('base') };
  return { lock: { lockfileVersion: 3, packages: { [location]: entry } },
    policy: { schema: 1, registry, artifacts: [{ location, version: '1.0.0',
      baseIntegrity: entry.integrity, integrity: sri('company') }] } };
}

test('company lock changes only approved integrity, leaves the base intact and is idempotent', () => {
  const { lock, policy } = fixture();
  const before = globalThis.structuredClone(lock);
  const prepared = companyLock(lock, policy, registry);
  assert.deepEqual(lock, before);
  assert.deepEqual(prepared.packages[location], { ...lock.packages[location], integrity: sri('company') });
  assert.deepEqual(companyLock(prepared, policy, registry), prepared);
  assert.deepEqual(companyLock(lock, policy, 'http://COMPANY.example:80/npm'), prepared);
});

test('company approval refuses stale versions, changed digests, missing entries and duplicate approvals', () => {
  for (const change of [value => { value.policy.artifacts[0].version = '2.0.0'; },
    value => { value.lock.packages[location].integrity = sri('unknown'); },
    value => { delete value.lock.packages[location]; },
    value => { value.policy.artifacts.push(value.policy.artifacts[0]); },
    value => { value.policy.artifacts[0].integrity = ''; },
    value => { value.policy.schema = 2; }]) {
    const value = fixture();
    change(value);
    assert.throws(() => companyLock(value.lock, value.policy, registry));
  }
});

test('company approval cannot be reused for a different registry or expose registry credentials', () => {
  const { lock, policy } = fixture();
  for (const url of ['https://registry.npmjs.org/', 'http://company.example/other/',
    'http://name:secret@company.example/npm/', 'file:///tmp/', 'http://company.example/npm/?token=secret']) {
    assert.throws(() => companyLock(lock, policy, url), error => {
      assert.doesNotMatch(error.message, /secret/);
      return true;
    });
  }
});

test('CLI prepares the image lock and refuses invalid approval without modifying the file', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'company-lock-cli-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const { lock, policy } = fixture();
  const filename = path.join(directory, 'package-lock.json');
  const approval = path.join(directory, 'npm-company-artifacts.json');
  fs.writeFileSync(filename, JSON.stringify(lock));
  fs.writeFileSync(approval, JSON.stringify(policy));
  const script = fileURLToPath(new URL('./company-npm-lock.mjs', import.meta.url));
  const args = [script, '--lock', filename, '--policy', approval, '--registry', registry];
  execFileSync(process.execPath, args);
  assert.equal(JSON.parse(fs.readFileSync(filename)).packages[location].integrity, sri('company'));
  const before = fs.readFileSync(filename);
  policy.artifacts[0].version = '2.0.0';
  fs.writeFileSync(approval, JSON.stringify(policy));
  assert.throws(() => execFileSync(process.execPath, args, { stdio: 'pipe' }));
  assert.deepEqual(fs.readFileSync(filename), before);
});

test('the company frontend lock preserves every version and all unrelated lock records', () => {
  const lock = JSON.parse(fs.readFileSync('frontend/package-lock.json'));
  const policy = JSON.parse(fs.readFileSync('frontend/npm-company-artifacts.json'));
  const prepared = companyLock(lock, policy, policy.registry);
  const expected = globalThis.structuredClone(lock);
  expected.packages['node_modules/@eslint/object-schema'].integrity = policy.artifacts[0].integrity;
  assert.deepEqual(prepared, expected);
  assert.equal(policy.artifacts[0].version, '2.1.7');
});
