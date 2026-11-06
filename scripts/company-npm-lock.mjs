import fs from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

function registryKey(value) {
  let url;
  try { url = new URL(value); } catch { throw new Error('Invalid company registry URL'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new Error('Company registry must be HTTP(S) without credentials, query or fragment');
  if (!url.pathname.endsWith('/')) url.pathname += '/';
  return url.href;
}

function approvedEntry(packages, artifact, seen) {
  if (!artifact || typeof artifact.location !== 'string' || !artifact.location.startsWith('node_modules/'))
    throw new Error('Invalid company artifact location');
  const digests = [artifact.baseIntegrity, artifact.integrity];
  if (!digests.every(value => typeof value === 'string' && /^sha512-[A-Za-z0-9+/]{86}==$/.test(value)))
    throw new Error('Company artifacts require exact SHA-512 integrity records');
  const entry = packages[artifact.location];
  if (!entry || entry.version !== artifact.version || seen.has(artifact.location) || !digests.includes(entry.integrity))
    throw new Error('Company artifact approval does not match the locked version or integrity');
  seen.add(artifact.location);
  return entry;
}

export function companyLock(lock, policy, registry) {
  if (policy?.schema !== 1 || !Array.isArray(policy.artifacts))
    throw new Error('Invalid company artifact approval');
  if (registryKey(registry) !== registryKey(policy.registry))
    throw new Error('Selected registry does not match the company artifact approval');
  const prepared = globalThis.structuredClone(lock);
  const seen = new Set();
  for (const artifact of policy.artifacts)
    approvedEntry(prepared.packages || {}, artifact, seen).integrity = artifact.integrity;
  return prepared;
}

function prepare() {
  const { values } = parseArgs({ options: {
    lock: { type: 'string', default: 'package-lock.json' },
    policy: { type: 'string', default: 'npm-company-artifacts.json' },
    registry: { type: 'string' },
  } });
  if (!values.registry) throw new Error('--registry is required');
  const filename = resolve(values.lock);
  const lock = JSON.parse(fs.readFileSync(filename, 'utf8'));
  const policy = JSON.parse(fs.readFileSync(resolve(values.policy), 'utf8'));
  const prepared = companyLock(lock, policy, values.registry);
  fs.writeFileSync(filename, JSON.stringify(prepared, null, 2) + '\n');
  console.log('Company npm artifact integrity pinned; dependency versions unchanged');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { prepare(); } catch (error) {
    console.error('Company npm lock preparation failed: ' + error.message);
    process.exitCode = 1;
  }
}
