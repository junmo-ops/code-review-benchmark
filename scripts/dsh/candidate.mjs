import { readFileSync } from 'node:fs';
import { dirname } from 'node:path';

export const name = 'benchmark-candidate-skill';
export const inject = ['skills', 'tools'];

/** Member config list in frozen order. Member-list overlay configs always
 * pass ``{ members: [...] }``; a bare record dict (legacy callers) is treated
 * as the single member it always was. */
function membersOf(config) {
  if (Array.isArray(config?.members)) return config.members;
  return config && typeof config === 'object' ? [config] : [];
}

export function apply(ctx, config) {
  const members = membersOf(config);
  for (const member of members) {
    const path = (member.root ?? '/skill') + '/' + member.main;
    ctx.skills.register({ name: member.name ?? 'candidate-skill',
      description: member.description, source: 'runtime', path,
      resourceBase: { kind: 'directory', path: dirname(path) },
      content: readFileSync(path, 'utf8') });
  }
  // Keep the established single-member shape ({version}) and extend it with
  // the member list only for combinations, without breaking existing consumers.
  ctx.provide('benchmarkSkill', members.length === 1
    ? { version: members[0].id }
    : { versions: members.map((member) => member.id),
        members: members.map((member) => ({ name: member.name, version: member.id })) });
}
