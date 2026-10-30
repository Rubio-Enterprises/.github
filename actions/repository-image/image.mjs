import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { isDeepStrictEqual } from 'node:util';

export const CRANE = 'gcr.io/go-containerregistry/crane@sha256:1f968817b95790bed063f71175aa6b8ff879fa17064020415f3e18bb6e6a36e1'; // v0.22.1
const digestPattern = /^sha256:[a-f0-9]{64}$/;
const assets = path.dirname(fileURLToPath(import.meta.url));
let pendingContext;
const run = (command, args) => execFileSync(command, args, { encoding: 'utf8' }).trim();
const crane = (...args) => run('docker', ['run', '--rm', CRANE, ...args]);

export function baseReference(value, kind) {
  const name = `ghcr.io/rubio-enterprises/devcontainers/${kind}`;
  if (value !== `${name}:latest` && !new RegExp(`^${name}@sha256:[a-f0-9]{64}$`).test(value)) {
    throw new Error(`Expected ${kind}:latest or its immutable sha256 reference`);
  }
  return name;
}

export function validatePair(baseManifest, ciManifest, baseConfig, ciConfig, baseDigest, arch) {
  for (const config of [baseConfig, ciConfig]) {
    if (config.os !== 'linux' || config.architecture !== arch) throw new Error('Wrong native image architecture');
  }
  if (ciConfig.config?.Labels?.['org.opencontainers.image.base.digest'] !== baseDigest) {
    throw new Error('base-ci was not built from this resolved base index');
  }
  if (!baseManifest.layers?.length || ciManifest.layers.length <= baseManifest.layers.length ||
      baseManifest.layers.some((layer, i) => layer.digest !== ciManifest.layers[i]?.digest) ||
      JSON.stringify(baseConfig.rootfs.diff_ids) !== JSON.stringify(ciConfig.rootfs.diff_ids.slice(0, baseConfig.rootfs.diff_ids.length))) {
    throw new Error('base-ci does not preserve the base filesystem prefix');
  }
}

export function validateRebase(base, ciBase, dev, ci, devConfig, ciConfig, arch) {
  for (const config of [devConfig, ciConfig]) {
    if (config.os !== 'linux' || config.architecture !== arch) throw new Error('Wrong repository image architecture');
  }
  const prefix = (image, parent) => parent.layers.every((layer, i) => layer.digest === image.layers[i]?.digest);
  if (!prefix(dev, base) || !prefix(ci, ciBase) ||
      !isDeepStrictEqual(dev.layers.slice(base.layers.length), ci.layers.slice(ciBase.layers.length)) ||
      !isDeepStrictEqual(devConfig.config, ciConfig.config) ||
      JSON.stringify(devConfig.rootfs.diff_ids.slice(base.layers.length)) !== JSON.stringify(ciConfig.rootfs.diff_ids.slice(ciBase.layers.length))) {
    throw new Error('Rebase changed repository layers or runtime configuration');
  }
  if (!devConfig.config?.Labels?.['devcontainer.metadata']) throw new Error('Missing Dev Container metadata');
  if (!devConfig.config.Env?.includes('UV_LINK_MODE=copy')) throw new Error('Missing uv copy mode');
}

function output(key, value) {
  if (/[\r\n]/.test(value)) throw new Error('Multiline output rejected');
  fs.appendFileSync(process.env.GITHUB_OUTPUT, `${key}=${value}\n`);
}

function resolve() {
  const refs = {};
  for (const [kind, input] of [['base', process.env.BASE], ['base-ci', process.env.BASE_CI]]) {
    const name = baseReference(input, kind);
    const digest = crane('digest', input);
    if (!digestPattern.test(digest)) throw new Error('Invalid registry index digest');
    refs[kind] = `${name}@${digest}`;
    const index = JSON.parse(crane('manifest', refs[kind]));
    for (const arch of ['amd64', 'arm64']) {
      if (index.manifests?.filter(entry => entry.platform?.os === 'linux' && entry.platform?.architecture === arch).length !== 1) {
        throw new Error('Base index must have exactly one image for each native architecture');
      }
    }
  }
  for (const arch of ['amd64', 'arm64']) {
    const fetch = (command, ref) => JSON.parse(crane(command, '--platform', `linux/${arch}`, ref));
    validatePair(fetch('manifest', refs.base), fetch('manifest', refs['base-ci']),
      fetch('config', refs.base), fetch('config', refs['base-ci']), refs.base.split('@')[1], arch);
  }
  for (const [key, ref] of Object.entries(refs)) output(key, ref);
}

export function validateDefinition(config) {
  if (!config || Array.isArray(config) || typeof config !== 'object' ||
      Object.keys(config).some(key => !['name', 'features'].includes(key))) {
    throw new Error('Build definition permits only name and extra features; migrate legacy Dockerfiles first');
  }
  if (config.features !== undefined && (!config.features || Array.isArray(config.features) || typeof config.features !== 'object')) {
    throw new Error('features must be an object');
  }
  for (const feature of Object.keys(config.features || {})) {
    if (!/^ghcr\.io\/[a-z0-9._/-]+:[0-9][a-zA-Z0-9._-]*(?:@sha256:[a-f0-9]{64})?$/.test(feature) ||
        feature.startsWith('ghcr.io/rubio-enterprises/devcontainer-features/baseline:')) {
      throw new Error('Use versioned OCI extra Features, not baseline or local Features');
    }
  }
}

function walk(root, visit, relative = '') {
  for (const entry of fs.readdirSync(path.join(root, relative), { withFileTypes: true })) {
    const name = path.join(relative, entry.name);
    if (['.standards-config', '.git'].includes(entry.name)) throw new Error('Tracked Git or standards checkout rejected');
    if (entry.isSymbolicLink()) {
      const target = path.resolve(root, relative, fs.readlinkSync(path.join(root, name)));
      if (!target.startsWith(`${root}${path.sep}`) || target.split(path.sep).includes('.standards-config')) {
        throw new Error('Source symlink escapes the clean context');
      }
    } else if (entry.isDirectory()) walk(root, visit, name);
    else if (entry.isFile()) visit(name);
  }
}

function prepare() {
  const workspace = process.env.GITHUB_WORKSPACE;
  if (!/^[a-f0-9]{40}$/.test(process.env.STANDARDS_PIN || '')) throw new Error('Invalid STANDARDS_PIN');
  const config = process.env.CONFIG;
  if (!config || path.isAbsolute(config) || config.split('/').includes('..')) throw new Error('Unsafe config path');
  if (run('git', ['-C', workspace, 'rev-parse', 'HEAD']) !== process.env.GITHUB_SHA) throw new Error('Checkout SHA mismatch');
  if (run('git', ['-C', workspace, 'ls-tree', '-r', 'HEAD']).split('\n').some(line => line.startsWith('160000 '))) {
    throw new Error('Submodules need an explicit image-source qualification');
  }
  const context = fs.mkdtempSync(path.join(process.env.RUNNER_TEMP, 'repository-image-'));
  pendingContext = context;
  const src = path.join(context, 'src');
  fs.mkdirSync(src);
  execFileSync('git', ['-C', workspace, 'archive', '--format=tar', '-o', path.join(context, 'source.tar'), 'HEAD']);
  run('tar', ['-xf', path.join(context, 'source.tar'), '-C', src]);
  fs.rmSync(path.join(context, 'source.tar'));
  const pins = path.join(context, 'pins');
  fs.mkdirSync(pins);
  walk(src, name => {
    if (/^(?:\.mise(?:\..+)?\.toml|mise(?:\..+)?\.toml|mise\.lock|package\.json)$/.test(name) || name.startsWith(`.config${path.sep}`)) {
      fs.mkdirSync(path.dirname(path.join(pins, name)), { recursive: true });
      fs.copyFileSync(path.join(src, name), path.join(pins, name));
    }
  });
  const definition = JSON.parse(run(process.env.DEVCONTAINER, ['read-configuration', '--workspace-folder', src, '--config', path.join(src, config)]));
  fs.mkdirSync(path.join(pins, '.standards-config'));
  const { configFilePath, ...configuration } = definition.configuration;
  validateDefinition(configuration);
  fs.mkdirSync(path.join(context, 'features'));
  fs.writeFileSync(path.join(context, 'features/devcontainer.json'), JSON.stringify({
    image: process.env.BASE, features: definition.configuration.features || {}, remoteUser: 'vscode',
  }));
  const lock = path.join(src, path.dirname(config), 'devcontainer-lock.json');
  if (fs.existsSync(lock)) fs.copyFileSync(lock, path.join(context, 'features/devcontainer-lock.json'));
  fs.mkdirSync(path.join(context, 'expected'));
  fs.writeFileSync(path.join(context, 'expected/pin'), process.env.STANDARDS_PIN);
  fs.mkdirSync(path.join(context, 'empty'));
  output('assets', assets);
  output('context', context);
  pendingContext = undefined;
}

function verify() {
  const directory = process.argv[3];
  const read = name => JSON.parse(fs.readFileSync(path.join(directory, `${name}.json`), 'utf8'));
  validateRebase(read('base'), read('base-ci'), read('dev'), read('ci'), read('dev-config'), read('ci-config'), process.env.ARCH);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const operation = { resolve, prepare, verify }[process.argv[2]];
    if (!operation) throw new Error('Unknown image operation');
    operation();
  } catch (error) {
    if (pendingContext) fs.rmSync(pendingContext, { recursive: true, force: true });
    console.error(`::error::${error.message}`);
    process.exitCode = 1;
  }
}
