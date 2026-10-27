import { createRequire } from 'node:module';
import path from 'node:path';
import { parseArgs } from 'node:util';
import { fileURLToPath } from 'node:url';

export async function sourceClosure(runtime, omit) {
  const npmPackage = process.env.npm_execpath
    ? path.resolve(path.dirname(process.env.npm_execpath), '../package.json')
    : path.resolve(path.dirname(process.execPath), '../lib/node_modules/npm/package.json');
  const require = createRequire(npmPackage);
  const Arborist = require('@npmcli/arborist');
  const tree = await new Arborist({ path: runtime }).loadActual();
  const queue = [tree], selected = new Set(), skipped = new Set();
  for (let index = 0; index < queue.length; index++) {
    const node = queue[index];
    if (selected.has(node.location) || skipped.has(node.location)) continue;
    if (node.isLink) throw new Error('Linked runtime package is not a source snapshot');
    if (!node.isRoot && omit.test(node.package.name)) {
      skipped.add(node.location);
      continue;
    }
    selected.add(node.location);
    for (const edge of node.edgesOut.values()) {
      if (edge.to) queue.push(edge.to);
      else if (!edge.optional) throw new Error('Unresolved installed dependency: ' + edge.name);
    }
  }
  return { selected: [...selected].filter(Boolean).sort(), omitted: [...skipped].sort() };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { values } = parseArgs({ options: { runtime: { type: 'string' }, omit: { type: 'string' } } });
  if (!values.runtime || !values.omit) throw new Error('Required: --runtime DIRECTORY --omit PATTERN');
  console.log(JSON.stringify(await sourceClosure(path.resolve(values.runtime), new RegExp(values.omit))));
}
