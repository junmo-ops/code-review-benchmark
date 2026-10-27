import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { TextDecoder } from 'node:util';

export function digest(file) {
  return createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

export function files(folder, prefix = '') {
  return fs.readdirSync(path.join(folder, prefix), { withFileTypes: true }).flatMap(entry => {
    const name = prefix + entry.name;
    if (entry.isDirectory()) return files(folder, name + '/');
    if (!entry.isFile()) throw new Error('Non-regular source entry: ' + name);
    return [name];
  }).sort();
}

export function safePath(root, name) {
  if (name.includes('\\') || name.startsWith('/') || name.split('/').some(part => !part || part === '..' || part === '.'))
    throw new Error('Unsafe manifest path');
  const result = path.resolve(root, name);
  if (!result.startsWith(path.resolve(root) + path.sep)) throw new Error('Path escaped root');
  return result;
}

export function verifySource(root, plan) {
  const actual = files(path.join(root, 'vendor')).map(name => 'vendor/' + name);
  if (JSON.stringify(actual) !== JSON.stringify(Object.keys(plan.files).sort())) throw new Error('Source inventory changed');
  const decoder = new TextDecoder('utf-8', { fatal: true });
  for (const name of actual) {
    const file = safePath(root, name), data = fs.readFileSync(file);
    const magic = data.subarray(0, 4).toString('hex');
    if (/\.(node|dll|exe|so|gz|tgz|zip|7z|rpm|deb|whl|wasm)$/i.test(name) || data.includes(0)
      || ['7f454c46', '504b0304', '0061736d'].includes(magic) || magic.startsWith('1f8b'))
      throw new Error('Non-source artifact in Git input: ' + name);
    decoder.decode(data);
    if (digest(file) !== plan.files[name]) throw new Error('Source digest mismatch: ' + name);
  }
}

export function fingerprint(root) {
  const names = ['package.json', 'SOURCE_PLAN.json', ...files(path.join(root, 'scripts'))
    .filter(name => name.endsWith('.mjs')).map(name => 'scripts/' + name)].sort();
  return createHash('sha256').update(JSON.stringify(names.map(name => [name, digest(path.join(root, name))]))).digest('hex');
}

export function materialize(project, payload, destination) {
  if (fs.existsSync(destination)) throw new Error('Runtime destination already exists');
  for (const module of payload.modules) {
    if (!module.location.startsWith('node_modules/') || !module.payload.startsWith('dist/packages/'))
      throw new Error('Invalid module location');
    safePath(project, module.payload);
    safePath(destination, module.location);
  }
  const installed = {};
  for (const module of payload.modules) {
    const source = safePath(project, module.payload), target = safePath(destination, module.location);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.cpSync(source, target, { recursive: true, errorOnExist: true, force: false });
    installed[module.location] = module.version;
  }
  return installed;
}

export function verifyPayload(root, manifest) {
  const names = files(path.join(root, 'dist/packages')).map(name => 'dist/packages/' + name);
  if (JSON.stringify(names) !== JSON.stringify(Object.keys(manifest.files).sort())) throw new Error('Payload inventory changed');
  for (const name of names) {
    if (digest(safePath(root, name)) !== manifest.files[name]) throw new Error('Payload digest mismatch: ' + name);
  }
}
