import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { BUILT_LABEL, KEY_LABEL, MAX_AGE_DAYS, blobReader, inputKey, trackedFiles } from './key.mjs';

const digestPattern = /^sha256:[a-f0-9]{64}$/;
const assets = path.dirname(fileURLToPath(import.meta.url));
const packageLink = '.config/mise/conf.d/standards.toml';
const packageTarget = '../../../.standards/node_modules/@rubio-enterprises/standards/mise/standards.toml';
const selfHostedTarget = '../../../mise/standards.toml';
const packageName = '@rubio-enterprises/standards';
const npmAuth = ['--@rubio-enterprises:registry=https://npm.pkg.github.com', '--//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}'];
const present = file => { try { return fs.lstatSync(file); } catch (error) { if (error.code === 'ENOENT') return; throw error; } };

export function standardsShape(root) {
  const link = present(path.join(root, packageLink));
  if (!link) throw new Error('Repository images require the Standards Package wiring; adopt @rubio-enterprises/standards first');
  const copier = ['.copier-answers.yml', '.config/mise-bootstrap.toml'].filter(name => present(path.join(root, name)));
  const configs = ['mise.toml', '.mise.toml'].filter(name => present(path.join(root, name)));
  const target = link.isSymbolicLink() && fs.readlinkSync(path.join(root, packageLink));
  const selfHosted = target === selfHostedTarget && process.env.GITHUB_REPOSITORY === 'Rubio-Enterprises/standards';
  if (copier.length || configs.length !== 1 || (target !== packageTarget && !selfHosted)) {
    throw new Error('Ambiguous standards shape: v2 requires the package link and no Copier artifacts');
  }
  if (fs.readdirSync(root).some(name => /^\.?mise\.repository-image(?:\.local)?\.toml$/.test(name))) {
    throw new Error('mise.repository-image.toml is reserved for transient image setup');
  }
  const config = path.join(root, configs[0]);
  if (!fs.lstatSync(config).isFile()) throw new Error('Root mise config must be a regular file');
  const data = JSON.parse(run('python3', ['-c', 'import json,sys,tomllib; print(json.dumps(tomllib.load(open(sys.argv[1], "rb"))))', config]));
  if (!data.tasks?.setup || !data.tasks?.['standards:install']) throw new Error('Incomplete v2 setup tasks');
  if (selfHosted) {
    for (const name of ['package.json', 'mise/standards.toml', 'lefthook/standards.yml']) {
      if (!present(path.join(root, name))?.isFile()) throw new Error('Incomplete standards self-hosting source');
    }
    if (JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8')).name !== packageName) {
      throw new Error('Standards self-hosting requires its own package manifest');
    }
  }
  const release = data.env?.STANDARDS_VERSION ?? 'latest';
  if (typeof release !== 'string' || !/^(latest|[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)$/.test(release)) {
    throw new Error('Image builds require latest or an exact Standards Release Pin');
  }
  return { release, selfHosted };
}

function inspect() {
  output('release', standardsShape(process.env.GITHUB_WORKSPACE).release);
}

function npm(args) {
  try {
    return JSON.parse(execFileSync('npm', [...args, ...npmAuth, '--ignore-scripts', '--loglevel=error'], {
      encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
    }));
  }
  catch { throw new Error('Standards Package registry operation failed; verify packages read access'); }
}

function release() {
  const spec = process.env.STANDARDS_RELEASE;
  if (!/^(latest|[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)$/.test(spec || '')) throw new Error('Invalid release selector');
  const result = npm(['view', `${packageName}@${spec}`, 'version', 'dist.integrity', '--json']);
  if (!/^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$/.test(result.version || '') ||
      !/^sha512-[A-Za-z0-9+/]{86}==$/.test(result['dist.integrity'] || '')) throw new Error('Invalid package identity');
  output('version', result.version);
  output('integrity', result['dist.integrity']);
}

function packRelease(directory, expected) {
  const [result] = npm(['pack', `${packageName}@${expected.version}`, '--pack-destination', directory, '--json']);
  if (!result || result.name !== packageName || result.version !== expected.version || result.integrity !== expected.integrity ||
      path.basename(result.filename || '') !== result.filename) throw new Error('Packed package identity mismatch');
  const archive = path.join(directory, result.filename);
  const bytes = fs.readFileSync(archive);
  if (`sha512-${createHash('sha512').update(bytes).digest('base64')}` !== expected.integrity) throw new Error('Packed package integrity mismatch');
  if (process.env.NODE_AUTH_TOKEN && bytes.includes(Buffer.from(process.env.NODE_AUTH_TOKEN))) throw new Error('Credential in package archive');
  return archive;
}

function pack() {
  const context = process.env.CONTEXT;
  const expected = JSON.parse(fs.readFileSync(path.join(context, 'expected/package.json'), 'utf8'));
  const directory = path.join(context, 'package');
  fs.mkdirSync(directory);
  const archive = packRelease(directory, expected);
  run('python3', [path.join(assets, 'recipe.py'), archive, path.join(directory, 'Dockerfile'), expected.version]);
  fs.renameSync(archive, path.join(directory, 'standards.tgz'));
}

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
export async function ghcrRepository(name, credential) {
  const repository = name.slice('ghcr.io/'.length);
  const response = await fetch(`https://ghcr.io/token?scope=repository:${repository}:pull&service=ghcr.io`,
    credential ? { headers: { Authorization: `Basic ${Buffer.from(credential).toString('base64')}` } } : {});
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

export function validateProvenance(dev, ci, baseDigest, ciBaseDigest) {
  for (const [manifest, expected] of [[dev, baseDigest], [ci, ciBaseDigest]]) {
    if (!digestPattern.test(expected) || manifest.annotations?.['org.opencontainers.image.base.digest'] !== expected) {
      throw new Error('Missing or incorrect manifest base digest annotation');
    }
  }
}

export function assertSingleAmd64Index(index) {
  const entries = index?.manifests;
  if (index?.schemaVersion !== 2 || !['application/vnd.oci.image.index.v1+json',
    'application/vnd.docker.distribution.manifest.list.v2+json'].includes(index.mediaType) ||
      entries?.length !== 1 || entries[0].platform?.os !== 'linux' || entries[0].platform?.architecture !== 'amd64' ||
      !digestPattern.test(entries[0].digest ?? '')) {
    throw new Error('Expected a manifest list with exactly one linux/amd64 image');
  }
  return entries[0].digest;
}

// Reads the published :main pair and reports why it cannot stand in for this commit, if it cannot.
export async function publishedPair({ image, ciImage, key, base, baseCi, now = Date.now(), credential }) {
  const pair = {};
  for (const [kind, name, expectedBase] of [['dev', image, base], ['ci', ciImage, baseCi]]) {
    let index, child, manifest, labels;
    try {
      const registry = await ghcrRepository(name, credential);
      index = await registry.manifest('main');
      child = assertSingleAmd64Index(index.json);
      manifest = (await registry.manifest(child)).json;
      if (!digestPattern.test(manifest.config?.digest ?? '')) throw new Error('Invalid published config digest');
      labels = (await registry.blob(manifest.config.digest)).json.config?.Labels ?? {};
    } catch (error) {
      return { reason: `${kind} :main is unreadable (${error.message})` };
    }
    if (labels[KEY_LABEL] !== key) return { reason: `${kind} :main was built from different inputs` };
    if (manifest.annotations?.['org.opencontainers.image.base.digest'] !== expectedBase.split('@')[1]) {
      return { reason: `${kind} :main was built on a different base` };
    }
    const built = Date.parse(labels[BUILT_LABEL] ?? '');
    if (!Number.isFinite(built) || built > now + 300e3 || now - built > MAX_AGE_DAYS * 86400e3) {
      return { reason: `${kind} :main is older than ${MAX_AGE_DAYS} days` };
    }
    pair[kind] = { index: index.digest, child };
  }
  return { pair };
}

async function key() {
  const workspace = process.env.GITHUB_WORKSPACE;
  const sha = process.env.GITHUB_SHA;
  const git = (args, input) => execFileSync('git', ['-C', workspace, ...args], { input, maxBuffer: 1 << 30 });
  if (git(['rev-parse', 'HEAD']).toString().trim() !== sha) throw new Error('Checkout SHA mismatch');
  const { selfHosted } = standardsShape(workspace);
  const config = process.env.CONFIG;
  if (!config || path.isAbsolute(config) || config.split('/').includes('..')) throw new Error('Unsafe config path');
  if (!/^[0-9]+\.[0-9]+\.[0-9]+$/.test(process.env.DEVCONTAINER_CLI_VERSION || '')) throw new Error('Missing Dev Container CLI version');
  const bases = { base: process.env.BASE, baseCi: process.env.BASE_CI };
  for (const [kind, value] of [['base', bases.base], ['base-ci', bases.baseCi]]) {
    baseReference(value ?? '', kind);
    if (!value.includes('@')) throw new Error('Image input keys require resolved base references');
  }
  const expected = { version: process.env.STANDARDS_VERSION, integrity: process.env.STANDARDS_INTEGRITY };
  const directory = fs.mkdtempSync(path.join(process.env.RUNNER_TEMP, 'repository-image-key-'));
  let packageInputs;
  try {
    const archive = packRelease(directory, expected);
    packageInputs = JSON.parse(run('python3', [path.join(assets, 'recipe.py'), 'inputs', archive, expected.version]));
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
  const image = process.env.IMAGE;
  const ciImage = `${image}-ci`;
  const result = inputKey({
    files: trackedFiles(git, sha), read: blobReader(git), packageInputs, config, bases,
    repository: process.env.GITHUB_REPOSITORY, images: { image, ciImage }, cli: process.env.DEVCONTAINER_CLI_VERSION, sha,
    treeReasons: selfHosted ? ['self-hosted standards source'] : [],
  });
  console.log(`Image input key ${result.key} (${result.rule === 'tree' ? 'whole tracked tree' : 'setup inputs'})`);
  for (const reason of result.reasons) console.log(`  whole-tree reason: ${reason}`);
  output('input-key', result.key);
  output('rule', result.rule);
  let decision = { reason: 'manual run rebuilds' };
  if (process.env.FORCE !== 'true') {
    decision = await publishedPair({
      image, ciImage, key: result.key, base: bases.base, baseCi: bases.baseCi,
      credential: `${process.env.GITHUB_ACTOR}:${process.env.REGISTRY_TOKEN}`,
    });
  }
  if (!decision.pair) {
    console.log(`Building: ${decision.reason}`);
    output('skip', 'false');
    return;
  }
  console.log(`::notice::Image inputs unchanged; aliasing ${image}@${decision.pair.dev.index} instead of building`);
  output('skip', 'true');
  for (const kind of ['dev', 'ci']) {
    output(`${kind}-index`, decision.pair[kind].index);
    output(`${kind}-child`, decision.pair[kind].child);
  }
}

const CRANE = {
  version: '0.22.1',
  sha256: '0ab7a1d6932a213aed964ce97666c3077fe691c8606413674a8b3e0b9ec4cda0',
};

async function installCrane(directory) {
  if (process.env.RUNNER_OS !== 'Linux' || process.env.RUNNER_ARCH !== 'X64') throw new Error('Pinned crane requires Linux X64');
  const response = await fetch(`https://github.com/google/go-containerregistry/releases/download/v${CRANE.version}/go-containerregistry_Linux_x86_64.tar.gz`);
  if (!response.ok) throw new Error(`crane download failed: ${response.status}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  if (createHash('sha256').update(bytes).digest('hex') !== CRANE.sha256) throw new Error('crane checksum mismatch');
  fs.writeFileSync(path.join(directory, 'crane.tar.gz'), bytes);
  run('tar', ['-xzf', path.join(directory, 'crane.tar.gz'), '-C', directory, 'crane']);
  return path.join(directory, 'crane');
}

// Build Service client jobs run without a Docker daemon, so they need the Docker CLI, the buildx plugin, crane (staging
// registry and GHCR) and step (client certificate). Each is installed from a checksum-pinned release unless already on PATH,
// which lets the image runner bake them in.
export const CLIENT_TOOLS = [
  {
    name: 'docker', url: 'https://download.docker.com/linux/static/stable/x86_64/docker-29.9.2.tgz',
    sha256: '9ac225999fd5e863f7694c728ef23d480616b1e5639328fa309befd4243d69eb', member: 'docker/docker',
  },
  {
    name: 'docker-buildx', url: 'https://github.com/docker/buildx/releases/download/v0.38.1/buildx-v0.38.1.linux-amd64',
    sha256: 'e25d5cd675092bb5d1d03632fdc46cdf3fede7b514dc5f023a105e00d3ce5ad4', plugin: true,
  },
  {
    name: 'crane', sha256: CRANE.sha256, member: 'crane',
    url: `https://github.com/google/go-containerregistry/releases/download/v${CRANE.version}/go-containerregistry_Linux_x86_64.tar.gz`,
  },
  {
    name: 'step', url: 'https://github.com/smallstep/cli/releases/download/v0.31.0/step_linux_0.31.0_amd64.tar.gz',
    sha256: 'a7e13726efa8782f3d5459174dacf602ab3514b9431f2d42a6a75950b7685e5f', member: 'step_0.31.0/bin/step',
  },
];

const onPath = (name, env) => (env.PATH || '').split(path.delimiter).some(directory => {
  try { fs.accessSync(path.join(directory, name), fs.constants.X_OK); return fs.statSync(path.join(directory, name)).isFile(); }
  catch { return false; }
});

export async function installTools(env = process.env, tools = CLIENT_TOOLS) {
  if (env.RUNNER_OS !== 'Linux' || env.RUNNER_ARCH !== 'X64') throw new Error('Pinned client tools require Linux X64');
  const root = path.join(env.RUNNER_TEMP, 'build-service');
  const bin = path.join(root, 'bin');
  const plugins = path.join(root, 'cli-plugins');
  fs.mkdirSync(bin, { recursive: true, mode: 0o700 });
  fs.mkdirSync(plugins, { recursive: true, mode: 0o700 });
  for (const tool of tools) {
    if (tool.plugin ? onPath('docker', env) && buildxWorks(env) : onPath(tool.name, env)) {
      console.log(`${tool.name}: already available`);
      continue;
    }
    const response = await fetch(tool.url);
    if (!response.ok) throw new Error(`${tool.name} download failed: ${response.status}`);
    const bytes = Buffer.from(await response.arrayBuffer());
    if (createHash('sha256').update(bytes).digest('hex') !== tool.sha256) throw new Error(`${tool.name} checksum mismatch`);
    const destination = path.join(tool.plugin ? plugins : bin, tool.name);
    if (tool.plugin) fs.writeFileSync(destination, bytes, { mode: 0o755 });
    else {
      const archive = path.join(root, `${tool.name}.tgz`);
      fs.writeFileSync(archive, bytes);
      const extracted = path.join(root, `${tool.name}.extract`);
      fs.mkdirSync(extracted);
      run('tar', ['-xzf', archive, '--no-same-owner', '-C', extracted, tool.member]);
      fs.renameSync(path.join(extracted, tool.member), destination);
      fs.chmodSync(destination, 0o755);
      fs.rmSync(extracted, { recursive: true });
      fs.rmSync(archive);
    }
    console.log(`${tool.name}: installed from ${tool.url}`);
  }
  fs.appendFileSync(env.GITHUB_PATH, `${bin}\n`);
}

function buildxWorks(env) {
  try { execFileSync('docker', ['buildx', 'version'], { env, stdio: 'ignore' }); return true; } catch { return false; }
}

// Points this commit's tags at the published pair whose recorded inputs match; never builds or moves :main.
export async function alias(env = process.env, install = installCrane) {
  const sha = env.GITHUB_SHA;
  if (!/^[a-f0-9]{40}$/.test(sha || '')) throw new Error('Invalid commit SHA');
  const directory = fs.mkdtempSync(path.join(env.RUNNER_TEMP, 'repository-image-alias-'));
  try {
    const crane = await install(directory);
    const craneEnv = { ...process.env, DOCKER_CONFIG: path.join(directory, 'auth') };
    const call = (args, input) => execFileSync(crane, args, { env: craneEnv, input, encoding: 'utf8', stdio: ['pipe', 'pipe', 'inherit'] }).trim();
    call(['auth', 'login', 'ghcr.io', '--username', env.GITHUB_ACTOR, '--password-stdin'], env.REGISTRY_TOKEN);
    for (const [name, prefix] of [[env.IMAGE, 'DEV'], [`${env.IMAGE}-ci`, 'CI']]) {
      const index = env[`${prefix}_INDEX`];
      const child = env[`${prefix}_CHILD`];
      if (!digestPattern.test(index || '') || !digestPattern.test(child || '')) throw new Error('Invalid published digests');
      if (assertSingleAmd64Index(JSON.parse(call(['manifest', `${name}@${index}`]))) !== child) throw new Error('Published index child changed');
      call(['tag', `${name}@${index}`, `sha-${sha}`]);
      call(['tag', `${name}@${child}`, `sha-${sha}-amd64`]);
      if (call(['digest', `${name}:sha-${sha}`]) !== index || call(['digest', `${name}:sha-${sha}-amd64`]) !== child) {
        throw new Error('Alias tags do not resolve to the published pair');
      }
      assertSingleAmd64Index(JSON.parse(call(['manifest', `${name}:sha-${sha}`])));
      console.log(`Aliased ${name}:sha-${sha} -> ${index}`);
    }
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

function output(key, value) {
  if (/[\r\n]/.test(value)) throw new Error('Multiline output rejected');
  fs.appendFileSync(process.env.GITHUB_OUTPUT, `${key}=${value}\n`);
}

export async function resolve(env = process.env) {
  const refs = {};
  const images = {};
  const arch = 'amd64';
  for (const [kind, input] of [['base', env.BASE], ['base-ci', env.BASE_CI]]) {
    const name = baseReference(input, kind);
    const registry = await ghcrRepository(name);
    const index = await registry.manifest(input.slice(name.length + 1));
    if (!digestPattern.test(index.digest)) throw new Error('Invalid registry index digest');
    refs[kind] = `${name}@${index.digest}`;
    const entries = index.json.manifests?.filter(entry => entry.platform?.os === 'linux' && entry.platform?.architecture === arch);
    if (entries?.length !== 1) {
      throw new Error('Base index must have exactly one linux/amd64 image');
    }
    const manifest = (await registry.manifest(entries[0].digest)).json;
    if (!digestPattern.test(manifest.config?.digest ?? '')) throw new Error('Invalid image config digest');
    images[kind] = { manifest, config: (await registry.blob(manifest.config.digest)).json };
  }
  const base = images.base;
  const ci = images['base-ci'];
  validatePair(base.manifest, ci.manifest, base.config, ci.config, refs.base.split('@')[1], arch);
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
    if (['.standards-config', '.standards', '.git'].includes(entry.name)) throw new Error('Tracked Git or standards checkout rejected');
    if (entry.isSymbolicLink()) {
      const link = path.join(root, name);
      const destination = fs.readlinkSync(link);
      if (name === packageLink && [packageTarget, selfHostedTarget].includes(destination)) {
        visit(name, true);
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
  const { selfHosted } = standardsShape(src);
  const pins = path.join(context, 'pins');
  fs.mkdirSync(pins);
  walk(src, (name, symlink) => {
    if (selfHosted || /^(?:\.mise(?:\..+)?\.toml|mise(?:\..+)?\.toml|mise\.lock|package\.json)$/.test(name) || name.startsWith(`.config${path.sep}`)) {
      fs.mkdirSync(path.dirname(path.join(pins, name)), { recursive: true });
      if (symlink) fs.symlinkSync(fs.readlinkSync(path.join(src, name)), path.join(pins, name));
      else fs.copyFileSync(path.join(src, name), path.join(pins, name));
    }
  });
  // `true` stands in for the docker binary: the CLI probes the daemon even to read a file, and Build Service clients have none.
  const definition = JSON.parse(run(process.env.DEVCONTAINER, ['read-configuration', '--workspace-folder', src, '--config', path.join(src, config), '--docker-path', 'true']));
  fs.mkdirSync(path.join(pins, '.standards'));
  const { configFilePath, ...configuration } = definition.configuration;
  validateDefinition(configuration);
  fs.mkdirSync(path.join(context, 'features'));
  fs.writeFileSync(path.join(context, 'features/devcontainer.json'), JSON.stringify({
    image: process.env.BASE, features: definition.configuration.features || {}, remoteUser: 'vscode',
  }));
  const lock = path.join(src, path.dirname(config), 'devcontainer-lock.json');
  if (fs.existsSync(lock)) fs.copyFileSync(lock, path.join(context, 'features/devcontainer-lock.json'));
  fs.mkdirSync(path.join(context, 'expected'));
  const version = process.env.STANDARDS_VERSION;
  const integrity = process.env.STANDARDS_INTEGRITY;
  if (!/^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$/.test(version || '') ||
      !/^sha512-[A-Za-z0-9+/]{86}==$/.test(integrity || '')) throw new Error('Missing resolved Standards Package identity');
  fs.writeFileSync(path.join(context, 'expected/package.json'), JSON.stringify({ version, integrity }));
  fs.writeFileSync(path.join(context, 'expected/profile.toml'), '[env]\nSTANDARDS_VERSION = "file:/run/standards.tgz"\nNODE_AUTH_TOKEN = ""\nnpm_config_cache = "/run/npm-cache"\n');
  fs.copyFileSync(path.join(context, 'expected/profile.toml'), path.join(pins, 'mise.repository-image.toml'));
  fs.mkdirSync(path.join(context, 'build'));
  execFileSync('git', ['-C', workspace, 'archive', '--format=tar', '-o', path.join(context, 'build/source.tar'), process.env.GITHUB_SHA]);
  output('assets', assets);
  output('context', context);
  pendingContext = undefined;
}

function verify() {
  const directory = process.argv[3];
  const read = name => JSON.parse(fs.readFileSync(path.join(directory, `${name}.json`), 'utf8'));
  validateRebase(read('base'), read('base-ci'), read('dev'), read('ci'), read('dev-config'), read('ci-config'), process.env.ARCH);
  validateProvenance(read('dev'), read('ci'), process.env.BASE?.split('@')[1], process.env.BASE_CI?.split('@')[1]);
  validateInputKey(read('dev-config'), process.env.INPUT_KEY);
}

// Layers of the dev image above the public base, in order. verify has already proven the base layers byte-identical to the
// published base, so the audit reads only these.
function aboveBase() {
  const directory = process.argv[3];
  const read = name => JSON.parse(fs.readFileSync(path.join(directory, `${name}.json`), 'utf8'));
  const layers = read('dev').layers.slice(read('base').layers.length);
  if (!layers.length) throw new Error('The dev image has no layers above the base');
  for (const { digest } of layers) {
    if (!digestPattern.test(digest ?? '')) throw new Error('Invalid layer digest');
    console.log(digest);
  }
}

export function validateInputKey(config, key) {
  if (!key) return;
  const labels = config.config?.Labels ?? {};
  if (labels[KEY_LABEL] !== key || !Number.isFinite(Date.parse(labels[BUILT_LABEL] ?? ''))) {
    throw new Error('Built image does not record its image input key');
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const operation = { resolve, prepare, verify, inspect, release, pack, key, alias: () => alias(),
      'above-base': aboveBase, tools: () => installTools(),
    }[process.argv[2]];
    if (!operation) throw new Error('Unknown image operation');
    await operation();
  } catch (error) {
    if (pendingContext) fs.rmSync(pendingContext, { recursive: true, force: true });
    console.error(`::error::${error.message}`);
    process.exitCode = 1;
  }
}
