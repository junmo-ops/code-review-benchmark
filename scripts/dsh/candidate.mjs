import { readFileSync } from 'node:fs';
import { dirname } from 'node:path';

export const name = 'benchmark-candidate-skill';
export const inject = ['skills', 'tools'];

export function apply(ctx, record) {
  const path = (record.root ?? '/skill') + '/' + record.main;
  ctx.skills.register({ name: 'candidate-skill', description: record.description,
    source: 'runtime', path, resourceBase: { kind: 'directory', path: dirname(path) },
    content: readFileSync(path, 'utf8') });
  ctx.provide('benchmarkSkill', { version: record.id });
}
