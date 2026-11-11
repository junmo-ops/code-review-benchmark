// Software-guard behavior for the benchmark tool scope: writes stay inside the
// case snapshot plus the generic Skill workspace; inspection may also reach the
// task input and the bound Skill resources. Critical for stats because a fresh
// write target has no physical ancestor inside the roots, and because the Skill
// workspace must be writable without the harness knowing the Skill's own workDir.
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { apply } from './scope.mjs';

function capture(paths, options) {
  const guards = [];
  apply({ tools: { guard: (fn) => guards.push(fn) }, provide: () => {} }, { paths, ...options });
  assert.equal(guards.length, 1, 'exactly one tool guard is installed');
  return (name, arguments_) => guards[0]({ name, arguments: arguments_ });
}

test('skill mode writes admit the case snapshot and the Skill workspace only', () => {
  const root = mkdtempSync(join(tmpdir(), 'dsh-scope-'));
  try {
    const repo = join(root, 'repo');
    const workspace = join(root, 'workspace');
    const input = join(root, 'input');
    const skill = join(root, 'skill');
    const outside = join(root, 'outside');
    for (const dir of [repo, workspace, input, skill, outside]) mkdirSync(dir);
    writeFileSync(join(repo, 'sample.py'), 'x');
    writeFileSync(join(workspace, 'issues.json'), '{}');
    const path = { repo, input, skill, workspace };
    const guard = capture(path, { commands: true, writes: true });
    assert.equal(guard('write', { file_path: join(repo, 'notes.json') }), undefined);
    assert.equal(guard('write', { file_path: join(workspace, 'issues.json') }), undefined);
    assert.equal(guard('write', { file_path: join(repo, 'sample.py') }), undefined);
    assert.match(guard('write', { file_path: join(outside, 'secret.txt') }), /BENCH_DSH_SCOPE_DENIED/);
    // Task input and the bound Skill resources are never writable.
    assert.match(guard('write', { file_path: join(path.input, 'REVIEW_DIFF.patch') }), /BENCH_DSH_SCOPE_DENIED/);
    assert.match(guard('write', { file_path: join(path.skill, 'SKILL.md') }), /BENCH_DSH_SCOPE_DENIED/);
    // read/glob/grep inspect the case, input, Skill and the workspace; the
    // workspace is read-back-able so artifacts written by scripts stay visible.
    assert.equal(guard('read', { file_path: join(workspace, 'issues.json') }), undefined);
    assert.equal(guard('read', { file_path: join(repo, 'sample.py') }), undefined);
    assert.equal(guard('read', { file_path: join(path.input, 'REVIEW_DIFF.patch') }), undefined);
    assert.match(guard('read', { file_path: join(outside, 'secret.txt') }), /BENCH_DSH_SCOPE_DENIED/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('legacy paths without a workspace keep the case-only write surface', () => {
  const root = mkdtempSync(join(tmpdir(), 'dsh-scope-'));
  try {
    const repo = join(root, 'repo');
    mkdirSync(repo);
    writeFileSync(join(repo, 'sample.py'), 'x');
    const path = { repo, input: join(root, 'input'), skill: join(root, 'skill') };
    const guard = capture(path, { commands: false, writes: false });
    assert.equal(guard('read', { file_path: join(repo, 'sample.py') }), undefined);
    // Baseline has no write surface at all: the tool is disabled by the guard.
    assert.match(guard('write', { file_path: join(repo, 'new.json') }), /BENCH_DSH_SCOPE_DENIED/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('skill tool guard admits exactly the registered member names', () => {
  const root = mkdtempSync(join(tmpdir(), 'dsh-scope-'));
  try {
    const repo = join(root, 'repo');
    const input = join(root, 'input');
    mkdirSync(repo);
    mkdirSync(input);
    const members = [
      { name: 'candidate-skill-1', root: join(root, 'skills', 'a'.repeat(64), 'content') },
      { name: 'candidate-skill-2', root: join(root, 'skills', 'b'.repeat(64), 'content') },
    ];
    const guard = capture({ repo, input, skill: join(root, 'skill'), workspace: join(root, 'workspace') },
      { commands: true, writes: true, members });
    assert.equal(guard('skill', { name: 'candidate-skill-1' }), undefined);
    assert.equal(guard('skill', { name: 'candidate-skill-2' }), undefined);
    assert.match(guard('skill', { name: 'candidate-skill-3' }), /unknown candidate Skill/);
    // The pre-combination single name is no longer loadable in a member batch.
    assert.match(guard('skill', { name: 'candidate-skill' }), /unknown candidate Skill/);
    // The legacy fallback (no member list) keeps the single canonical name.
    const legacy = capture({ repo, input, skill: join(root, 'skill'), workspace: join(root, 'workspace') },
      { commands: true, writes: true });
    assert.equal(legacy('skill', { name: 'candidate-skill' }), undefined);
    assert.match(legacy('skill', { name: 'candidate-skill-1' }), /unknown candidate Skill/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('member content roots stay readable and never writable', () => {
  const root = mkdtempSync(join(tmpdir(), 'dsh-scope-'));
  try {
    const repo = join(root, 'repo');
    const input = join(root, 'input');
    const skillsA = join(root, 'skills', 'a'.repeat(64), 'content');
    const skillsB = join(root, 'skills', 'b'.repeat(64), 'content');
    const workspace = join(root, 'workspace');
    for (const dir of [repo, input, skillsA, skillsB, workspace]) mkdirSync(dir, { recursive: true });
    writeFileSync(join(skillsA, 'SKILL.md'), 'first');
    writeFileSync(join(skillsB, 'SKILL.md'), 'second');
    const members = [
      { name: 'candidate-skill-1', root: skillsA },
      { name: 'candidate-skill-2', root: skillsB },
    ];
    const guard = capture({ repo, input, skill: join(root, 'skill'), workspace },
      { commands: true, writes: true, members });
    assert.equal(guard('read', { file_path: join(skillsA, 'SKILL.md') }), undefined);
    assert.equal(guard('read', { file_path: join(skillsB, 'SKILL.md') }), undefined);
    assert.match(guard('write', { file_path: join(skillsA, 'new.txt') }), /BENCH_DSH_SCOPE_DENIED/);
    assert.match(guard('write', { file_path: join(skillsB, 'new.txt') }), /BENCH_DSH_SCOPE_DENIED/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('relative targets resolve from the case root and stay contained', () => {
  const root = mkdtempSync(join(tmpdir(), 'dsh-scope-'));
  try {
    const repo = join(root, 'repo');
    mkdirSync(repo);
    const path = { repo, input: join(root, 'input'), skill: join(root, 'skill') };
    const guard = capture(path, { commands: false, writes: false });
    assert.equal(guard('read', { file_path: 'sample.py' }), undefined);
    assert.match(guard('read', { file_path: '../policy.json' }), /BENCH_DSH_SCOPE_DENIED/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
