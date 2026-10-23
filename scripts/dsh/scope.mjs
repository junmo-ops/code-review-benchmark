import { realpathSync } from 'node:fs';
import { isAbsolute, resolve } from 'node:path';

export const name = 'benchmark-tool-scope';
export const inject = ['tools'];

export function apply(ctx, paths = { repo: '/work', input: '/input', skill: '/skill' }) {
  ctx.tools.guard((execution) => {
    const args = execution.arguments;
    if (!args || typeof args !== 'object') return 'Error: invalid inspection arguments';
    if (execution.name === 'skill') return args.name === 'candidate-skill' ? undefined : 'Error: unknown candidate Skill';
    if (!['read', 'glob', 'grep'].includes(execution.name))
      return 'Error: BENCH_DSH_SCOPE_DENIED: inspection tool is disabled';
    const target = execution.name === 'read' ? args.file_path : args.path ?? paths.repo;
    if (typeof target !== 'string') return 'Error: invalid inspection path';
    const roots = execution.name === 'read' ? [paths.repo, paths.input, paths.skill] : [paths.repo, paths.skill];
    const contains = (path) => roots.some((root) => path === root || path.startsWith(root + '/'));
    try {
      // The JS implementation normalizes '..'; native preserves physical traversal.
      const absolute = isAbsolute(target) ? target : paths.repo + '/' + target;
      if (!contains(resolve(absolute)) || !contains(realpathSync.native(absolute)))
        return 'Error: BENCH_DSH_SCOPE_DENIED: inspection access outside the case and task input is denied';
    } catch {
      return 'Error: inspection target is unavailable';
    }
    return undefined;
  });
  ctx.provide('benchmarkScope', { policy: 'case-and-task-input-only' });
}
