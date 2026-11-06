import assert from 'node:assert/strict';
import test from 'node:test';
import { validateDependencies } from './check-dependencies.mjs';

const manifest = { dependencies: { fixture: '1.0.0' }, devDependencies: {} };

function lockfile() {
  return { packages: {
    '': { ...manifest },
    'node_modules/fixture': {
      version: '1.0.0',
      resolved: 'https://registry.npmjs.org/fixture/-/fixture-1.0.0.tgz',
      integrity: 'sha512-fixture',
    },
  } };
}

test('accepts a portable locked dependency', () => {
  assert.doesNotThrow(() => validateDependencies(manifest, lockfile()));
});

test('rejects a mismatched root manifest', () => {
  const lock = lockfile();
  lock.packages[''].dependencies = { fixture: '2.0.0' };
  assert.throws(() => validateDependencies(manifest, lock), /fixture/);
});

test('rejects optional placeholders without a version before npm ci', () => {
  const lock = lockfile();
  lock.packages['node_modules/fixture/node_modules/native'] = { dev: true, optional: true };
  assert.throws(() => validateDependencies(manifest, lock), /native/);
});

test('rejects empty versions and missing integrity records', () => {
  for (const change of [{ version: '' }, { integrity: undefined }]) {
    const lock = lockfile();
    Object.assign(lock.packages['node_modules/fixture'], change);
    assert.throws(() => validateDependencies(manifest, lock), /fixture/);
  }
});

test('rejects custom registry URLs that would bypass registry switching', () => {
  const lock = lockfile();
  lock.packages['node_modules/fixture'].resolved = 'https://private.invalid/npm/fixture.tgz';
  assert.throws(() => validateDependencies(manifest, lock), /fixture/);
});

test('allows links without registry metadata', () => {
  const lock = lockfile();
  lock.packages['node_modules/local'] = { link: true, resolved: '../local' };
  assert.doesNotThrow(() => validateDependencies(manifest, lock));
});
