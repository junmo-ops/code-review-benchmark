import { realpathSync } from 'node:fs';
import { dirname, isAbsolute, resolve, sep } from 'node:path';

export const name = 'benchmark-tool-scope';
export const inject = ['tools'];

/** Physical path of `path`, or of its nearest existing ancestor when the
 * target itself does not exist yet (a fresh write target). */
function realOf(path) {
  try {
    return realpathSync.native(path);
  } catch {
    try {
      return realpathSync.native(dirname(path));
    } catch {
      return null;
    }
  }
}

function insideCase(roots, path) {
  return roots.some((root) => path === root || path.startsWith(root.endsWith(sep) ? root : root + sep));
}

/** Resolved target is under one of the roots; native traversal is preserved. */
function containedTarget(roots, target) {
  if (typeof target !== 'string') return false;
  const absolute = isAbsolute(target) ? target : roots[0] + sep + target;
  const physical = realOf(absolute);
  if (physical === null) return false;
  return insideCase(roots, resolve(absolute)) && insideCase(roots, physical);
}

// Legacy policy passed a flat paths dict; current policy passes
// { paths, commands, writes } so skill mode can opt into the write surface and
// command execution that its documented workflow requires. Baseline mode keeps
// static read-only inspection.
export function apply(ctx, options = {}) {
  const config = options.paths ? options : { paths: options, commands: false, writes: false };
  const paths = config.paths;
  const commands = Boolean(config.commands);
  const writes = Boolean(config.writes);
  const tools = new Set(['read', 'glob', 'grep']);
  if (writes) {
    tools.add('write');
    tools.add('edit');
  }
  if (commands) tools.add('bash');
  ctx.tools.guard((execution) => {
    const args = execution.arguments;
    if (!args || typeof args !== 'object') return 'Error: invalid inspection arguments';
    if (execution.name === 'skill') return args.name === 'candidate-skill' ? undefined : 'Error: unknown candidate Skill';
    if (!tools.has(execution.name))
      return 'Error: BENCH_DSH_SCOPE_DENIED: inspection tool is disabled';
    if (execution.name === 'bash') {
      // A shell command string cannot be statically scoped to a path; POD mode
      // only has software guards by design. Writes remain path-scoped, and in
      // sandbox mode bash-sandbox confines the command at the OS layer.
      return undefined;
    }
    const target = execution.name === 'read' || execution.name === 'write' || execution.name === 'edit'
      ? args.file_path : (args.path ?? paths.repo);
    // Inspecting tools (read/glob/grep) may look into the case snapshot, the
    // task input and the bound Skill's resource directory; the write surface
    // stays inside the case snapshot only: task input and the bound Skill are
    // never writable through the software guard.
    const roots = (execution.name === 'write' || execution.name === 'edit')
      ? [paths.repo] : [paths.repo, paths.input, paths.skill];
    return containedTarget(roots, target)
      ? undefined
      : 'Error: BENCH_DSH_SCOPE_DENIED: inspection access outside the case and task input is denied';
  });
  ctx.provide('benchmarkScope', { policy: commands || writes ? 'case-and-task-only' : 'case-and-task-input-only' });
}
