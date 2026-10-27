import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { sourceClosure } from './source-closure.mjs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fingerprint } from '../../dependencies/dsh-review-source/scripts/source.mjs';

function write(folder, value) {
  fs.mkdirSync(folder, { recursive: true });
  fs.writeFileSync(path.join(folder, 'package.json'), JSON.stringify(value));
}

test('installed dependency closure retains lazy dependencies, excluding only declared unsupported subtrees', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-closure-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  write(root, { name: 'fixture', version: '1.0.0', dependencies: { engine: '1.0.0', voice: '1.0.0' } });
  write(path.join(root, 'node_modules/engine'), { name: 'engine', version: '1.0.0', dependencies: { lazy: '1.0.0' } });
  write(path.join(root, 'node_modules/lazy'), { name: 'lazy', version: '1.0.0' });
  write(path.join(root, 'node_modules/voice'), { name: 'voice', version: '1.0.0', dependencies: { audio: '1.0.0' } });
  write(path.join(root, 'node_modules/audio'), { name: 'audio', version: '1.0.0' });
  const result = await sourceClosure(root, /^voice$/);
  assert.deepEqual(result.selected, ['node_modules/engine', 'node_modules/lazy']);
  assert.deepEqual(result.omitted, ['node_modules/voice']);
  fs.rmSync(path.join(root, 'node_modules/lazy'), { recursive: true });
  await assert.rejects(sourceClosure(root, /^voice$/), /Unresolved installed dependency: lazy/);
});

test('company audit observation is pinned to the actual lock, not an assumed latest package set', () => {
  const observation = JSON.parse(fs.readFileSync('docs/dsh-company-registry-observation.json'));
  const lock = fs.readFileSync('dependencies/dsh/package-lock.json');
  assert.equal(createHash('sha256').update(lock).digest('hex'), observation.lockSha256);
  assert.equal(new Set(observation.missingOrNotVisible).size, observation.counts.missing_or_not_visible);
  assert.equal(observation.checkedPackages, observation.counts.metadata_matches + observation.counts.missing_or_not_visible);
});

test('consumer approval matches the renamed producer and rejects an independently changed package name', t => {
  const producer = 'dependencies/dsh-review-source';
  const approved = JSON.parse(fs.readFileSync('dependencies/dsh/headless-release.json'));
  const pkg = JSON.parse(fs.readFileSync(path.join(producer, 'package.json')));
  assert.equal(pkg.name, approved.name);
  assert.equal(pkg.version, approved.version);
  assert.equal(fingerprint(producer), approved.sourceFingerprint);
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-renamed-source-'));
  t.after(() => fs.rmSync(temporary, { recursive: true, force: true }));
  for (const name of ['package.json', 'SOURCE_PLAN.json', 'scripts'])
    fs.cpSync(path.join(producer, name), path.join(temporary, name), { recursive: true });
  write(temporary, { ...pkg, name: '@unapproved/renamed-runtime' });
  assert.notEqual(fingerprint(temporary), approved.sourceFingerprint);
});

test('standalone Git source keeps nested dist/bin/runtime files and vendor CRLF bytes with autocrlf enabled', t => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-source-git-'));
  t.after(() => fs.rmSync(temporary, { recursive: true, force: true }));
  const root = path.join(temporary, 'source'), output = path.join(temporary, 'checkout');
  fs.mkdirSync(root);
  for (const name of ['.gitattributes', '.gitignore'])
    fs.copyFileSync(path.join('dependencies/dsh-review-source', name), path.join(root, name));
  const inputs = { 'package.json': '{"name":"fixture"}\n',
    'vendor/npm/a/package.json': '{"name":"vendor-fixture"}\r\n',
    'vendor/npm/a/dist/index.js': 'export const value = 1;\r\n',
    'vendor/npm/a/bin/main.js': 'console.log(1);\n', 'vendor/npm/a/runtime/loader.js': 'export {};\n' };
  for (const [name, data] of Object.entries(inputs)) {
    fs.mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
    fs.writeFileSync(path.join(root, name), data);
  }
  const git = args => execFileSync('git', ['-C', root, '-c', 'core.autocrlf=true', ...args]);
  git(['init', '--quiet']);
  git(['add', '--all']);
  git(['checkout-index', '--all', '--prefix=' + output + path.sep]);
  for (const [name, data] of Object.entries(inputs)) assert.equal(fs.readFileSync(path.join(output, name), 'utf8'), data);
});
