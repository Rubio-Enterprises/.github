// Image input key: everything that shapes a v2 repository image except the baked source snapshot.
// See docs/reusable-workflows/repository-image.md before changing a rule here.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const KEY_LABEL = 'org.rubio-enterprises.image.input-key';
export const BUILT_LABEL = 'org.rubio-enterprises.image.built-at';
export const KEY_SCHEMA = 'repository-image-input-key/1';
export const MAX_AGE_DAYS = 7;

const assets = path.dirname(fileURLToPath(import.meta.url));
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');

// Tracked paths that tools, dependency installs, setup and the build definition read.
const INPUT_PATTERNS = [
  /^\.?mise(\.[^/]+)?\.toml$/, /^mise\.lock$/, /^\.miserc\.toml$/, /^\.config\//, /^\.?mise\//, /^\.?mise-tasks\//,
  /(^|\/)\.tool-versions$/, /(^|\/)\.(node|python|ruby|go|java|bun|deno|terraform)-version$/, /(^|\/)\.nvmrc$/,
  /(^|\/)rust-toolchain(\.toml)?$/,
  /^\.?lefthook(-local)?\.(ya?ml|json|jsonc|toml)$/, /^\.lefthook\//,
  /(^|\/)(package\.json|package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|\.yarnrc(\.yml)?|pnpm-lock\.yaml|pnpm-workspace\.yaml|bun\.lockb?|bunfig\.toml|\.npmrc|\.pnpmfile\.[cm]?js)$/,
  /(^|\/)\.yarn\//, /(^|\/)patches\//, /\.(patch|diff)$/,
  /(^|\/)(pyproject\.toml|uv\.lock|uv\.toml|poetry\.lock|pdm\.lock|Pipfile|Pipfile\.lock|setup\.py|setup\.cfg)$/,
  /(^|\/)(requirements|constraints)[^/]*\.(txt|in)$/, /(^|\/)requirements\//,
  /(^|\/)(Cargo\.toml|Cargo\.lock)$/, /(^|\/)\.cargo\//,
  /(^|\/)(go\.mod|go\.sum|go\.work|go\.work\.sum)$/,
  /(^|\/)(Gemfile|Gemfile\.lock|gems\.rb|gems\.locked|[^/]+\.gemspec)$/, /(^|\/)\.bundle\//,
  /(^|\/)\.gitattributes$/, /^\.gitmodules$/,
];

const STARTER_SETUP_RUN = ['test -z "$(mise ls --missing)" || mise install', 'mise exec -- lefthook install'];
const STARTER_INSTALL_RUN = `npm install --prefix .standards --no-save --prefer-online --fetch-retries=0 --no-audit --no-fund --ignore-scripts --loglevel=error "--@rubio-enterprises:registry=\${STANDARDS_REGISTRY:-https://npm.pkg.github.com}" '--//npm.pkg.github.com/:_authToken=\${NODE_AUTH_TOKEN}' "@rubio-enterprises/standards@\${STANDARDS_VERSION:-latest}"`;
const MISE_CONFIG = /^(\.?mise(\.local)?\.toml|\.config\/mise\.toml|\.config\/mise\/(config(\.local)?\.toml|conf\.d\/[^/]+\.toml)|\.?mise\/config(\.local)?\.toml)$/;
const FILE_TASK = /^(\.?mise-tasks|\.?mise\/tasks|\.config\/mise\/tasks)\/(setup|standards)/;
const SAFE_DIR = /^(?!\/)(?!.*(^|\/)\.\.(\/|$))[A-Za-z0-9._/-]+$/;
const LIFECYCLE = ['preinstall', 'install', 'postinstall', 'prepare', 'preprepare', 'postprepare', 'prepublish', 'dependencies'];
const DEPENDENCY_FIELDS = ['dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies', 'overrides', 'resolutions'];
// Reviewed dependency install scripts that only touch their own package directory or print a notice.
// A package missing here makes the repository hash its whole tree; add one only after reading its script.
export const ALLOWED_INSTALL_SCRIPTS = new Set([
  '@google/genai', '@parcel/watcher', '@scarf/scarf', '@sentry/cli', '@swc/core', 'better-sqlite3', 'bootstrap-vue',
  'core-js', 'core-js-pure', 'es5-ext', 'esbuild', 'fsevents', 'koffi', 'less', 'msgpackr-extract', 'protobufjs', 're2', 'sharp',
  'tesseract.js', 'unrs-resolver', 'vue-demi',
]);
// Bun runs dependency scripts only for trustedDependencies and this verbatim copy of its default list (bun-v1.4.4).
let bunDefaultTrusted;
const bunTrusted = () => (bunDefaultTrusted ??= new Set(fs.readFileSync(path.join(assets, 'bun-default-trusted.txt'), 'utf8').split('\n').filter(Boolean)));

export function trackedFiles(git, rev) {
  return git(['ls-tree', '-r', '-z', '--full-tree', rev]).toString('utf8').split('\0').filter(Boolean).map(record => {
    const match = /^(\d{6}) (\w+) ([0-9a-f]{40,64})\t([\s\S]+)$/.exec(record);
    if (!match) throw new Error('Unreadable Git tree entry');
    return { mode: match[1], type: match[2], oid: match[3], path: match[4] };
  });
}

export function blobReader(git) {
  const cache = new Map();
  return entries => {
    const missing = [...new Set(entries.map(entry => entry.oid))].filter(oid => !cache.has(oid));
    if (missing.length) {
      const output = git(['cat-file', '--batch'], `${missing.join('\n')}\n`);
      let offset = 0;
      for (const oid of missing) {
        const end = output.indexOf(10, offset);
        const [found, type, size] = output.subarray(offset, end).toString('utf8').split(' ');
        if (found !== oid || type !== 'blob') throw new Error('Unreadable Git blob');
        cache.set(oid, output.subarray(end + 1, end + 1 + Number(size)));
        offset = end + 2 + Number(size);
      }
    }
    return entries.map(entry => cache.get(entry.oid));
  };
}

const tomlCache = new Map();
export function parseToml(texts) {
  const pending = texts.filter(text => !tomlCache.has(text));
  if (pending.length) {
    const parsed = JSON.parse(execFileSync('python3', ['-c', `
import json, sys, tomllib
out = []
for text in json.load(sys.stdin):
    try: out.append(tomllib.loads(text))
    except Exception: out.append(None)
print(json.dumps(out, default=str))
`], { input: JSON.stringify(pending), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }));
    pending.forEach((text, i) => tomlCache.set(text, parsed[i]));
  }
  return texts.map(text => tomlCache.get(text));
}

const json = bytes => { try { return JSON.parse(bytes.toString('utf8')); } catch { return undefined; } };
const object = value => value && typeof value === 'object' && !Array.isArray(value);

// release-please moves only these fields on a release commit; they do not change what setup installs.
export function normalizeManifest(name, bytes) {
  const data = json(bytes);
  if (!object(data)) return undefined;
  if (name === 'package.json') delete data.version;
  else if (name === 'package-lock.json' || name === 'npm-shrinkwrap.json') {
    delete data.version;
    if (object(data.packages?.[''])) delete data.packages[''].version;
  } else return undefined;
  return `normalized:${sha256(JSON.stringify(data))}`;
}

function parseBunLock(bytes) {
  try { return JSON.parse(bytes.toString('utf8').replace(/,(\s*[}\]])/g, '$1')); } catch { return undefined; }
}

// Top-level YAML keys read by pnpm 10+ to admit dependency builds; anything else is treated as unknown.
export function pnpmBuildPolicy(text) {
  const allowed = new Set();
  let block;
  for (const raw of text.split('\n')) {
    const line = raw.replace(/\s+#.*$/, '').replace(/\s+$/, '');
    if (!line.trim() || line.trim().startsWith('#')) continue;
    if (!/^\s/.test(line)) {
      const key = /^([A-Za-z]+):(.*)$/.exec(line);
      if (!key) return undefined;
      block = undefined;
      if (['dangerouslyAllowAllBuilds', 'neverBuiltDependencies', 'onlyBuiltDependenciesFile', 'ignoredBuiltDependencies'].includes(key[1])) {
        if (key[1] === 'ignoredBuiltDependencies') { block = 'ignored'; continue; }
        return undefined;
      }
      if (['allowBuilds', 'onlyBuiltDependencies'].includes(key[1])) {
        if (key[2].trim()) return undefined;
        block = key[1];
      }
      continue;
    }
    if (block === 'allowBuilds') {
      const entry = /^\s+(?:"([^"]+)"|'([^']+)'|([^\s:'"]+)):\s*(true|false)$/.exec(line);
      if (!entry) return undefined;
      if (entry[4] === 'true') allowed.add(entry[1] ?? entry[2] ?? entry[3]);
    } else if (block === 'onlyBuiltDependencies') {
      const entry = /^\s+-\s+(?:"([^"]+)"|'([^']+)'|(\S+))$/.exec(line);
      if (!entry) return undefined;
      allowed.add(entry[1] ?? entry[2] ?? entry[3]);
    } else if (block === 'ignored' && !/^\s+-\s+\S/.test(line)) return undefined;
  }
  return allowed;
}

function lockedName(key) {
  const index = key.lastIndexOf('node_modules/');
  return index < 0 ? key : key.slice(index + 'node_modules/'.length);
}

const COMMANDS = [
  [/^bun install --frozen-lockfile( --ignore-scripts)?$/, m => ({ kind: 'js', pm: 'bun', ignore: !!m[1] })],
  [/^npm(?: --prefix (\S+))? ci(?: --prefix (\S+))?((?: --(?:ignore-scripts|no-audit|no-fund))*)$/,
    m => ({ kind: 'js', pm: 'npm', dir: m[1] ?? m[2], ignore: (m[3] || '').includes('--ignore-scripts'), invalid: m[1] && m[2] })],
  [/^pnpm(?: --dir (\S+))? install --frozen-lockfile( --ignore-scripts)?$/, m => ({ kind: 'js', pm: 'pnpm', dir: m[1], ignore: !!m[2] })],
  [/^uv sync --(?:frozen|locked)(?: --python "\$\(mise which python\)")?$/, () => ({ kind: 'uv-sync' })],
  [/^uv venv (?:--clear|--allow-existing)(?: --quiet)? \.venv$/, () => ({ kind: 'none' })],
  [/^uv pip install(?: --quiet)? --python \.venv -r (\S+)$/, m => ({ kind: 'requirements', file: m[1] })],
  [/^cargo fetch --locked$/, () => ({ kind: 'none' })],
];

export function classifyCommand(command) {
  for (const [pattern, describe] of COMMANDS) {
    const match = pattern.exec(command);
    if (match) {
      const result = describe(match);
      if (result.invalid || (result.dir !== undefined && !SAFE_DIR.test(result.dir))) return undefined;
      return result;
    }
  }
}

const join = (...parts) => path.posix.normalize(path.posix.join(...parts.filter(Boolean))).replace(/^\.\/?$/, '').replace(/\/$/, '');

// Decide whether setup reads only manifest-style inputs. Every unknown is a reason to hash the whole tree.
export function setupReasons({ files, read, packageTasks = [] }) {
  const reasons = [];
  const byPath = new Map(files.map(entry => [entry.path, entry]));
  const text = name => { const entry = byPath.get(name); return entry && entry.mode !== '120000' && entry.type === 'blob' ? read([entry])[0].toString('utf8') : undefined; };
  for (const name of packageTasks) {
    if (name === 'setup' || name.startsWith('setup:') || name === 'standards:install') reasons.push(`package defines task ${name}`);
  }
  for (const entry of files) if (FILE_TASK.test(entry.path)) reasons.push(`file task ${entry.path}`);
  const configs = files.filter(entry => MISE_CONFIG.test(entry.path) && entry.mode !== '120000');
  const parsed = parseToml(configs.map(entry => read([entry])[0].toString('utf8')));
  const tasks = new Map();
  configs.forEach((entry, i) => {
    const data = parsed[i];
    if (!object(data)) { reasons.push(`unparseable ${entry.path}`); return; }
    const { tasks: _tasks, ...settings } = data;
    const serialized = JSON.stringify(settings);
    if (/\b(exec|read_file)\(/.test(serialized)) reasons.push(`template command in ${entry.path}`);
    if (data.task_config) reasons.push(`task includes in ${entry.path}`);
    if ([data.env ?? []].flat().some(env => Object.keys(env?._ ?? {}).some(key => key !== 'path'))) reasons.push(`env directive in ${entry.path}`);
    if (Object.keys(data.hooks ?? {}).some(key => !['enter', 'leave', 'cd'].includes(key))) reasons.push(`install hook in ${entry.path}`);
    for (const [tool, value] of Object.entries(data.tools ?? {})) {
      const values = Array.isArray(value) ? value : [value];
      if (values.some(item => (object(item) && (item.postinstall || /^(path|ref):/.test(item.version ?? ''))) || /^(path|ref):/.test(typeof item === 'string' ? item : ''))) {
        reasons.push(`local or postinstall tool ${tool}`);
      }
    }
    for (const [name, task] of Object.entries(data.tasks ?? {})) {
      if (tasks.has(name) && JSON.stringify(tasks.get(name)) !== JSON.stringify(task)) reasons.push(`task ${name} defined twice`);
      tasks.set(name, task);
    }
  });
  const setup = tasks.get('setup');
  if (!object(setup) || JSON.stringify(setup.run) !== JSON.stringify(STARTER_SETUP_RUN) ||
      JSON.stringify(setup.depends) !== '["standards:install"]' ||
      JSON.stringify(setup.depends_post) !== '[{"task":"setup:*","optional":true}]' ||
      Object.keys(setup).some(key => !['description', 'run', 'depends', 'depends_post'].includes(key))) reasons.push('setup task differs from the starter');
  const install = tasks.get('standards:install');
  if (!object(install) || install.run !== STARTER_INSTALL_RUN || Object.keys(install).some(key => !['description', 'run'].includes(key))) {
    reasons.push('standards:install differs from the starter');
  }
  const pending = [...tasks.keys()].filter(name => name.startsWith('setup:'));
  const seen = new Set(['setup', 'standards:install']);
  const commands = [];
  while (pending.length) {
    const name = pending.pop();
    if (seen.has(name)) continue;
    seen.add(name);
    const task = tasks.get(name);
    if (!object(task)) { reasons.push(`unknown task ${name}`); continue; }
    if (Object.keys(task).some(key => !['description', 'run', 'dir', 'depends', 'depends_post', 'wait_for'].includes(key))) {
      reasons.push(`task ${name} uses unsupported keys`);
      continue;
    }
    if (task.dir !== undefined && (typeof task.dir !== 'string' || !SAFE_DIR.test(task.dir))) reasons.push(`task ${name} dir`);
    for (const key of ['depends', 'depends_post', 'wait_for']) {
      for (const dependency of [task[key] ?? []].flat()) {
        if (typeof dependency !== 'string' || !tasks.has(dependency)) reasons.push(`task ${name} dependency`);
        else pending.push(dependency);
      }
    }
    const runs = [task.run ?? []].flat();
    for (const run of runs) {
      if (typeof run !== 'string') { reasons.push(`task ${name} run entry`); continue; }
      for (const part of run.trim().split(/\s*&&\s*/)) {
        const command = classifyCommand(part);
        if (!command) reasons.push(`task ${name} runs ${JSON.stringify(part.slice(0, 80))}`);
        else commands.push({ ...command, task: name, dir: join(typeof task.dir === 'string' ? task.dir : '', command.dir ?? '') });
      }
    }
  }
  const manifests = files.filter(entry => /(^|\/)package\.json$/.test(entry.path) && entry.type === 'blob' && entry.mode !== '120000');
  const packages = new Map(manifests.map(entry => [entry.path, json(read([entry])[0])]));
  const js = commands.filter(command => command.kind === 'js');
  if (js.length) {
    for (const [name, data] of packages) {
      if (!object(data)) { reasons.push(`unparseable ${name}`); continue; }
      for (const field of DEPENDENCY_FIELDS) {
        const specs = JSON.stringify(data[field] ?? {});
        if (/"(file|link|portal|exec):|"\.{0,2}\/|"~\//.test(specs)) reasons.push(`local dependency in ${name}`);
      }
      if (js.some(command => !command.ignore) && LIFECYCLE.some(script => data.scripts?.[script] !== undefined)) {
        reasons.push(`lifecycle script in ${name}`);
      }
    }
    if (files.some(entry => /(^|\/)\.pnpmfile\.[cm]?js$/.test(entry.path))) reasons.push('pnpmfile');
  }
  for (const command of js.filter(item => !item.ignore)) {
    const local = name => join(command.dir, name);
    const scripted = [];
    if (command.pm === 'npm') {
      const lockName = [local('npm-shrinkwrap.json'), local('package-lock.json')].find(name => byPath.has(name));
      const lock = lockName && json(Buffer.from(text(lockName) ?? ''));
      if (!object(lock?.packages)) { reasons.push(`no readable npm lockfile for ${command.task}`); continue; }
      for (const [key, value] of Object.entries(lock.packages)) if (key && value?.hasInstallScript) scripted.push(lockedName(key));
    } else if (command.pm === 'bun') {
      const lock = byPath.has(local('bun.lock')) && parseBunLock(Buffer.from(text(local('bun.lock')) ?? ''));
      if (!object(lock?.packages)) { reasons.push(`no readable bun.lock for ${command.task}`); continue; }
      const trusted = packages.get(local('package.json'))?.trustedDependencies ?? [];
      if (!Array.isArray(trusted)) { reasons.push('trustedDependencies'); continue; }
      scripted.push(...trusted);
      for (const value of Object.values(lock.packages)) {
        const name = Array.isArray(value) && typeof value[0] === 'string' && value[0].indexOf('@', 1) > 0
          ? value[0].slice(0, value[0].indexOf('@', 1)) : undefined;
        if (!name) reasons.push('unreadable bun.lock package');
        else if (bunTrusted().has(name)) scripted.push(name);
      }
    } else {
      const manifest = packages.get(local('package.json'));
      const manager = /^pnpm@(\d+)\./.exec(manifest?.packageManager ?? '');
      if (!manager || Number(manager[1]) < 10) { reasons.push(`pnpm version for ${command.task}`); continue; }
      if (object(manifest.pnpm) && Object.keys(manifest.pnpm).some(key => /built|builds/i.test(key))) reasons.push('pnpm build policy in package.json');
      const workspace = text(local('pnpm-workspace.yaml'));
      const allowed = workspace === undefined ? new Set() : pnpmBuildPolicy(workspace);
      if (!allowed) { reasons.push(`unreadable pnpm build policy for ${command.task}`); continue; }
      scripted.push(...allowed);
    }
    for (const name of new Set(scripted)) if (!ALLOWED_INSTALL_SCRIPTS.has(name)) reasons.push(`install script of ${name}`);
  }
  if (commands.some(command => command.kind === 'uv-sync')) {
    const projects = files.filter(entry => /(^|\/)pyproject\.toml$/.test(entry.path) && entry.mode !== '120000');
    parseToml(projects.map(entry => read([entry])[0].toString('utf8'))).forEach((data, i) => {
      const name = projects[i].path;
      if (!object(data)) reasons.push(`unparseable ${name}`);
      else if (data['build-system'] || data.tool?.uv?.package === true) reasons.push(`installable project ${name}`);
      else if (Object.values(data.tool?.uv?.sources ?? {}).flat().some(source => object(source) && (source.path || source.workspace || source.editable))) {
        reasons.push(`local source in ${name}`);
      }
    });
  }
  for (const command of commands.filter(item => item.kind === 'requirements')) {
    const queue = [join(command.dir, command.file)];
    const visited = new Set();
    while (queue.length) {
      const name = queue.pop();
      if (visited.has(name)) continue;
      visited.add(name);
      const content = INPUT_PATTERNS.some(pattern => pattern.test(name)) ? text(name) : undefined;
      if (content === undefined) { reasons.push(`requirements file ${name}`); continue; }
      for (const raw of content.replace(/\\\n/g, ' ').split('\n')) {
        const line = raw.replace(/(^|\s)#.*$/, '').trim();
        if (!line) continue;
        const nested = /^(?:-r|-c|--requirement|--constraint)[ =](\S+)$/.exec(line);
        if (nested) queue.push(join(path.posix.dirname(name), nested[1]));
        else if (/^(-e|--editable|\.|\/|~|file:)/.test(line) || /@\s*(file:|\.)/.test(line)) reasons.push(`local requirement in ${name}`);
        else if (line.startsWith('-') && !/^--(index-url|extra-index-url|hash)[ =]/.test(line)) reasons.push(`requirement option in ${name}`);
      }
    }
  }
  return [...new Set(reasons)];
}

// The key's file component: input files (or the whole tree), with release-version normalization.
export function fileComponent(files, read, rule) {
  const chosen = files.filter(entry => rule === 'tree' || entry.type === 'commit' || INPUT_PATTERNS.some(pattern => pattern.test(entry.path)));
  return chosen.map(entry => {
    const base = path.posix.basename(entry.path);
    const normalized = entry.path === base && entry.type === 'blob' && entry.mode !== '120000' ? normalizeManifest(base, read([entry])[0]) : undefined;
    return [entry.mode, entry.path, normalized ?? entry.oid];
  });
}

export function assetsDigest(directory = assets) {
  const names = fs.readdirSync(directory, { withFileTypes: true }).filter(entry => entry.isFile()).map(entry => entry.name).sort();
  return sha256(names.map(name => `${name}\0${sha256(fs.readFileSync(path.join(directory, name)))}\n`).join(''));
}

export function inputKey({ files, read, packageInputs, config, bases, repository, images, cli, sha, treeReasons = [] }) {
  const reasons = [...treeReasons, ...setupReasons({ files, read, packageTasks: packageInputs.tasks })];
  if (packageInputs.fallback) reasons.push('package configuration reads more than its pins');
  const subst = files.filter(entry => /(^|\/)\.gitattributes$/.test(entry.path) && /export-subst/.test(read([entry])[0].toString('utf8')));
  const rule = reasons.length ? 'tree' : 'inputs';
  const definition = [config, path.posix.join(path.posix.dirname(config), 'devcontainer-lock.json')];
  const document = {
    schema: KEY_SCHEMA,
    builder: { assets: assetsDigest(), cli },
    repository, images, bases,
    definition: files.filter(entry => definition.includes(entry.path)).map(entry => [entry.mode, entry.path, entry.oid]),
    package: rule === 'tree' ? packageInputs.all : packageInputs.relevant,
    rule,
    files: fileComponent(files, read, rule),
    commit: subst.length ? sha : undefined,
  };
  return { key: `sha256:${sha256(JSON.stringify(document))}`, rule, reasons, subst: subst.length > 0 };
}

