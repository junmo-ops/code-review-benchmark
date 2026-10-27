import fs from 'node:fs';
import path from 'node:path';
import { parseArgs } from 'node:util';
import { fileURLToPath } from 'node:url';
import { acquireSuppliers, compile, run } from './native.mjs';
import { digest, files, fingerprint, materialize, verifyPayload, verifySource } from './source.mjs';
import { smoke } from './smoke.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export function checkBuildNode(version) {
  if (!/^22\.(9|22)\./.test(version)) throw new Error('Build host requires Node 22.9.x or 22.22.x; target runtime remains Node 22.22.x');
}

function host() {
  if (process.platform !== 'linux' || process.arch !== 'x64' || !process.report.getReport().header.glibcVersionRuntime)
    throw new Error('Build requires Linux x64/glibc');
  checkBuildNode(process.versions.node);
}

function options() {
  const { values } = parseArgs({ options: { registry: { type: 'string' }, cache: { type: 'string' },
    offline: { type: 'boolean' } } });
  const registry = new URL(values.registry || process.env.NPM_CONFIG_REGISTRY || process.env.npm_config_registry || 'http://central.jaf.cmbchina.cn/artifactory/api/npm/group-npm/');
  if (!['http:', 'https:'].includes(registry.protocol) || registry.username || registry.password || registry.search || registry.hash)
    throw new Error('Registry must not contain credentials, query or fragment');
  return { registry: registry.href, cache: values.cache && path.resolve(values.cache), offline: values.offline };
}

function build() {
  host();
  const settings = options();
  const plan = JSON.parse(fs.readFileSync(path.join(root, 'SOURCE_PLAN.json')));
  verifySource(root, plan);
  const work = fs.mkdtempSync(path.join(root, '.dsh-review-build-'));
  try {
    const stage = path.join(work, 'package');
    fs.mkdirSync(path.join(stage, 'dist/packages'), { recursive: true });
    for (const module of plan.modules)
      fs.cpSync(path.join(root, module.source), path.join(stage, module.payload), { recursive: true });
    const moduleRoot = name => {
      const module = plan.modules.find(item => item.location === 'node_modules/' + name);
      if (!module) throw new Error('Missing required module: ' + name);
      return path.join(stage, module.payload);
    };
    const cache = path.join(work, 'cache');
    if (settings.cache) fs.cpSync(settings.cache, cache, { recursive: true });
    const suppliers = acquireSuppliers(root, plan, path.join(work, 'native-inputs'),
      { ...settings, cache });
    for (const supplier of suppliers)
      fs.cpSync(supplier.folder, moduleRoot(supplier.name), { recursive: true });
    compile(root, plan, moduleRoot);
    const manifest = { schema: 1, kind: 'dsh-review-headless', upstreamVersion: '0.2.1-rc.2',
      internalVersion: JSON.parse(fs.readFileSync(path.join(root, 'package.json'))).version,
      sourceFingerprint: fingerprint(root), sourcePlanSha256: digest(path.join(root, 'SOURCE_PLAN.json')),
      target: plan.target, capabilities: plan.capabilities, modules: plan.modules,
      targetCheckSha256: digest(path.join(root, 'scripts/smoke.mjs')),
      runtimeValidation: { requiredNode: '22.22.x', status: 'required-at-install',
        buildHostCheck: process.versions.node.startsWith('22.22.') ? 'passed' : 'not-run-cross-build' },
      nativeInputs: { offline: settings.offline === true, registry: settings.registry },
      toolchain: { node: process.versions.node, cxx: run(process.env.CXX || 'c++', ['--version'], root).split('\n')[0] },
      files: Object.fromEntries(files(path.join(stage, 'dist/packages'))
        .map(name => ['dist/packages/' + name, digest(path.join(stage, 'dist/packages', name))])) };
    verifyPayload(stage, manifest);
    if (process.versions.node.startsWith('22.22.')) {
      const runtime = path.join(work, 'runtime');
      materialize(stage, manifest, runtime);
      smoke(runtime);
    }
    fs.copyFileSync(path.join(root, 'scripts/smoke.mjs'), path.join(stage, 'dist/target-check.mjs'));
    fs.copyFileSync(path.join(root, 'SOURCE_PLAN.json'), path.join(stage, 'dist/source-plan.json'));
    fs.writeFileSync(path.join(stage, 'dist/runtime-manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
    const destination = path.join(root, 'dist');
    fs.rmSync(destination, { recursive: true, force: true });
    fs.renameSync(path.join(stage, 'dist'), destination);
    console.log('Built DSH runtime: ' + manifest.modules.length + ' locked module locations; target Node 22.22.x check is mandatory during image install');
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { build(); } catch (error) {
    console.error('DSH source build failed: ' + error.message);
    process.exitCode = 1;
  }
}
