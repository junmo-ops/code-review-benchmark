import fs from 'node:fs';
import path from 'node:path';
import { parseArgs } from 'node:util';
import { materialize, verifyPayload } from './source.mjs';

const { values } = parseArgs({ options: { package: { type: 'string' }, destination: { type: 'string' } } });
if (!values.package || !values.destination) throw new Error('Required: --package ROOT --destination NEW_DIRECTORY');
const root = path.resolve(values.package);
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'dist/runtime-manifest.json')));
verifyPayload(root, manifest);
materialize(root, manifest, path.resolve(values.destination));
fs.writeFileSync(path.resolve(values.destination, 'headless-distribution.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log('Headless module layout restored without npm dependency resolution');
