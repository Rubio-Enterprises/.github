import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';

const digestPattern = /^sha256:[a-f0-9]{64}$/;
const assets = path.dirname(fileURLToPath(import.meta.url));
const sharedConfigStubLinks = new Map([
  ['tombi.toml', '.standards-config/config/tombi.toml'],
]);
let pendingContext;
const run = (command, args) => execFileSync(command, args, { encoding: 'utf8' }).trim();
const MANIFEST_TYPES = [
  'application/vnd.oci.image.index.v1+json',
  'application/vnd.docker.distribution.manifest.list.v2+json',
  'application/vnd.oci.image.manifest.v1+json',
  'application/vnd.docker.distribution.manifest.v2+json',
].join(',');
const sha256 = bytes => `sha256:${createHash('sha256').update(bytes).digest('hex')}`;

// Anonymous pulls of the public base packages, so prepare needs no Docker daemon.
// Digest-addressed content is verified against its digest, as crane does.
export async function ghcrRepository(name) {
  const repository = name.slice('ghcr.io/'.length);
  const response = await fetch(`https://ghcr.io/token?scope=repository:${repository}:pull&service=ghcr.io`);
  if (!response.ok) throw new Error(`Registry token request failed: ${response.status}`);
  const { token } = await response.json();
  const get = async (kind, reference, accept) => {
    const url = `https://ghcr.io/v2/${repository}/${kind}/${reference}`;
    const result = await fetch(url, { headers: { Authorization: `Bearer ${token}`, ...(accept && { Accept: accept }) } });
    if (!result.ok) throw new Error(`Registry request failed: ${result.status} ${kind}/${reference}`);
    const bytes = Buffer.from(await result.arrayBuffer());
    const digest = sha256(bytes);
    if (reference.startsWith('sha256:') && digest !== reference) throw new Error('Registry content does not match its digest');
    return { digest, json: JSON.parse(bytes.toString('utf8')) };
  };
  return {
    manifest: reference => get('manifests', reference, MANIFEST_TYPES),
    blob: digest => get('blobs', digest),
  };
}

export function baseReference(value, kind) {
  const name = `ghcr.io/rubio-enterprises/devcontainers/${kind}`;
  if (value !== `${name}:latest` && !(value.startsWith(`${name}@`) && digestPattern.test(value.slice(name.length + 1)))) {
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

export async function resolve(env = process.env) {
  const refs = {};
  const images = {};
  for (const [kind, input] of [['base', env.BASE], ['base-ci', env.BASE_CI]]) {
    const name = baseReference(input, kind);
    const registry = await ghcrRepository(name);
    const index = await registry.manifest(input.slice(name.length + 1));
    if (!digestPattern.test(index.digest)) throw new Error('Invalid registry index digest');
    refs[kind] = `${name}@${index.digest}`;
    images[kind] = {};
    for (const arch of ['amd64', 'arm64']) {
      const entries = index.json.manifests?.filter(entry => entry.platform?.os === 'linux' && entry.platform?.architecture === arch);
      if (entries?.length !== 1) {
        throw new Error('Base index must have exactly one image for each native architecture');
      }
      const manifest = (await registry.manifest(entries[0].digest)).json;
      if (!digestPattern.test(manifest.config?.digest ?? '')) throw new Error('Invalid image config digest');
      images[kind][arch] = { manifest, config: (await registry.blob(manifest.config.digest)).json };
    }
  }
  for (const arch of ['amd64', 'arm64']) {
    const base = images.base[arch];
    const ci = images['base-ci'][arch];
    validatePair(base.manifest, ci.manifest, base.config, ci.config, refs.base.split('@')[1], arch);
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
      const link = path.join(root, name);
      const destination = fs.readlinkSync(link);
      if (sharedConfigStubLinks.get(name) === destination) {
        fs.unlinkSync(link);
        continue;
      }
      const target = path.resolve(root, relative, destination);
      if (!target.startsWith(`${root}${path.sep}`) || target.split(path.sep).includes('.standards-config')) {
        throw new Error('Source symlink escapes the clean context');
      }
    } else if (entry.isDirectory()) walk(root, visit, name);
    else if (entry.isFile()) visit(name);
  }
}

export function qualifySubmodules(workspace, src) {
  const git = args => execFileSync('git', ['-C', workspace, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  const gitlinks = git(['ls-tree', '-rz', 'HEAD']).split('\0').filter(record => record.startsWith('160000 ')).map(record => {
    const match = /^160000 commit ([a-f0-9]{40})\t(.+)$/.exec(record);
    if (!match || /[\x00-\x1f\x7f]/.test(match[2])) throw new Error('Unsafe submodule path');
    return { sha: match[1], name: match[2] };
  });
  if (!gitlinks.length) return;
  const modulesFile = path.join(src, '.gitmodules');
  if (!fs.existsSync(modulesFile) || !fs.lstatSync(modulesFile).isFile()) {
    throw new Error('Submodules require a regular committed .gitmodules file');
  }
  let entries;
  try {
    entries = git(['config', '--no-includes', '--null', '--blob', 'HEAD:.gitmodules', '--list']);
  } catch {
    throw new Error('Cannot parse committed submodule qualification');
  }
  const modules = new Map();
  for (const entry of entries.split('\0').filter(Boolean)) {
    const separator = entry.indexOf('\n');
    const field = /^submodule\.(.+)\.(path|url)$/.exec(separator < 0 ? entry : entry.slice(0, separator));
    if (!field) continue;
    const module = modules.get(field[1]) || {};
    if (Object.hasOwn(module, field[2])) throw new Error('Duplicate submodule qualification');
    module[field[2]] = separator < 0 ? '' : entry.slice(separator + 1);
    modules.set(field[1], module);
  }
  const paths = new Map();
  for (const module of modules.values()) {
    const url = /^https:\/\/github\.com\/Rubio-Enterprises\/([A-Za-z0-9_.-]+?)(?:\.git)?$/.exec(module.url || '');
    if (!module.path || !url || ['.', '..'].includes(url[1]) || paths.has(module.path)) {
      throw new Error('Submodules require an exact credential-free organization HTTPS URL and unique path');
    }
    paths.set(module.path, module.url);
  }
  if (paths.size !== gitlinks.length) throw new Error('Submodule declarations do not match committed gitlinks');
  for (const { name, sha } of gitlinks) {
    if (!paths.has(name)) throw new Error('Submodule declarations do not match committed gitlinks');
    const directory = path.join(src, name);
    if (!fs.existsSync(directory) || !fs.lstatSync(directory).isDirectory() || fs.readdirSync(directory).length) {
      throw new Error('Qualified submodule must remain an empty non-symlink directory');
    }
    console.log(`::notice::Omitted organization submodule ${JSON.stringify(name).replaceAll('%', '%25')} at ${sha}`);
  }
}

function prepare() {
  const workspace = process.env.GITHUB_WORKSPACE;
  if (!/^[a-f0-9]{40}$/.test(process.env.STANDARDS_PIN || '')) throw new Error('Invalid STANDARDS_PIN');
  const config = process.env.CONFIG;
  if (!config || path.isAbsolute(config) || config.split('/').includes('..')) throw new Error('Unsafe config path');
  if (run('git', ['-C', workspace, 'rev-parse', 'HEAD']) !== process.env.GITHUB_SHA) throw new Error('Checkout SHA mismatch');
  const context = fs.mkdtempSync(path.join(process.env.RUNNER_TEMP, 'repository-image-'));
  pendingContext = context;
  const src = path.join(context, 'src');
  fs.mkdirSync(src);
  execFileSync('git', ['-C', workspace, 'archive', '--format=tar', '-o', path.join(context, 'source.tar'), 'HEAD']);
  run('tar', ['-xf', path.join(context, 'source.tar'), '-C', src]);
  fs.rmSync(path.join(context, 'source.tar'));
  qualifySubmodules(workspace, src);
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
    await operation();
  } catch (error) {
    if (pendingContext) fs.rmSync(pendingContext, { recursive: true, force: true });
    console.error(`::error::${error.message}`);
    process.exitCode = 1;
  }
}
