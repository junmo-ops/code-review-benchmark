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

/** Roots for the given execution: inspection reaches case+input+every bound
 * Skill content root (+the Skill workspace so artifacts written there stay
 * readable); writes stay in the case snapshot plus the same workspace.
 * ``skillRoots`` carry the overlay member configs (same source that registers
 * the names); the legacy ``paths.skill`` root is only a fallback for bare
 * harnesses that predate the member list. Workspace is optional so legacy
 * policies without one keep the case-only write surface. */
function rootsFor(name, paths, skillRoots) {
  const writes = name === 'write' || name === 'edit';
  const base = writes ? [paths.repo] : [paths.repo, paths.input, ...skillRoots];
  if (!writes && paths.skill) base.push(paths.skill);
  return paths.workspace ? base.concat([paths.workspace]) : base;
}

// Legacy policy passed a flat paths dict; current policy passes
// { paths, commands, writes, members } so skill mode can opt into the write
// surface, command execution and the exact member-name set (frozen order maps
// to candidate-skill-1..N) that its documented workflow requires. Baseline
// mode keeps static read-only inspection.
export function apply(ctx, options = {}) {
  const config = options.paths ? options : { paths: options, commands: false, writes: false };
  const paths = config.paths;
  const commands = Boolean(config.commands);
  const writes = Boolean(config.writes);
  // Same member list the candidate plugin registers: guard the exact names the
  // overlay will load, never a second hard-coded roster.
  const members = Array.isArray(config.members) ? config.members : [];
  const skillNames = new Set(members.map((member) => member.name).filter(Boolean));
  const skillRoots = members.map((member) => member.root).filter(Boolean);
  const tools = new Set(['read', 'glob', 'grep']);
  if (writes) {
    tools.add('write');
    tools.add('edit');
  }
  if (commands) tools.add('bash');
  ctx.tools.guard((execution) => {
    const args = execution.arguments;
    if (!args || typeof args !== 'object') return 'Error: invalid inspection arguments';
    if (execution.name === 'skill') {
      // Bare harnesses that predate the member list keep the single canonical
      // name; production configs always carry the member set.
      return (skillNames.size ? skillNames.has(args.name) : args.name === 'candidate-skill')
        ? undefined : 'Error: unknown candidate Skill';
    }
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
    // task input, every bound Skill's resource directory and the generic Skill
    // workspace (so the model can read back its own artifacts). The write
    // surface stays inside the case snapshot plus that same workspace -- the
    // one writable directory outside the case that any candidate Skill may use
    // for its documented scripts and artifacts (the harness provisions
    // runtime.locations(work)['workspace'] for skill mode). Task input and the
    // bound Skills themselves are never writable through the software guard.
    const roots = rootsFor(execution.name, paths, skillRoots);
    return containedTarget(roots, target)
      ? undefined
      : 'Error: BENCH_DSH_SCOPE_DENIED: inspection access outside the case and task input is denied';
  });
  ctx.provide('benchmarkScope', { policy: commands || writes ? 'case-and-task-only' : 'case-and-task-input-only' });
}
