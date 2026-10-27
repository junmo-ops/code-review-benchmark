import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { digest, fingerprint, verifyPayload, verifySource } from './source.mjs';

export function validateDist(root) {
  const plan = JSON.parse(fs.readFileSync(path.join(root, 'SOURCE_PLAN.json')));
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'dist/runtime-manifest.json')));
  verifySource(root, plan);
  verifyPayload(root, manifest);
  if (manifest.runtimeValidation?.requiredNode !== '22.22.x'
    || manifest.runtimeValidation?.status !== 'required-at-install'
    || manifest.targetCheckSha256 !== digest(path.join(root, 'scripts/smoke.mjs'))
    || manifest.targetCheckSha256 !== digest(path.join(root, 'dist/target-check.mjs')))
    throw new Error('Missing or changed mandatory target runtime check');
  if (manifest.sourceFingerprint !== fingerprint(root)
    || digest(path.join(root, 'SOURCE_PLAN.json')) !== manifest.sourcePlanSha256
    || digest(path.join(root, 'dist/source-plan.json')) !== manifest.sourcePlanSha256
    || manifest.internalVersion !== JSON.parse(fs.readFileSync(path.join(root, 'package.json'))).version)
    throw new Error('Stale dist output: source or version changed; rebuild before publishing');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  validateDist(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'));
  console.log('DSH publish payload hashes and source fingerprint verified');
}
