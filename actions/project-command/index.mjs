import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const imageId = /^sha256:[a-f0-9]{64}$/;
const containerId = /^[a-f0-9]{12,64}$/;
const assets = path.dirname(fileURLToPath(import.meta.url));
const contextNames = [
  'GITHUB_ACTIONS', 'GITHUB_REPOSITORY', 'GITHUB_REPOSITORY_ID', 'GITHUB_REPOSITORY_OWNER',
  'GITHUB_SHA', 'GITHUB_REF', 'GITHUB_REF_NAME', 'GITHUB_REF_TYPE', 'GITHUB_HEAD_REF',
  'GITHUB_BASE_REF', 'GITHUB_EVENT_NAME', 'GITHUB_WORKFLOW', 'GITHUB_RUN_ID',
  'GITHUB_RUN_ATTEMPT', 'GITHUB_RUN_NUMBER', 'BASE_SHA', 'HEAD_SHA',
];
const channels = ['GITHUB_OUTPUT', 'GITHUB_ENV', 'GITHUB_PATH', 'GITHUB_STEP_SUMMARY'];
const protectedNames = new Set([
  ...channels, 'GITHUB_STATE', 'GITHUB_WORKSPACE', 'GITHUB_EVENT_PATH', 'HOME', 'PATH',
  'RUNNER_TEMP', 'GIT_CONFIG_GLOBAL', 'NODE_OPTIONS', 'BASH_ENV', 'ENV', 'LD_PRELOAD',
]);
const input = name => process.env['INPUT_' + name.toUpperCase()] ?? '';
const seconds = start => ((performance.now() - start) / 1000).toFixed(3);
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const write = (file, value) => fs.writeFileSync(file, JSON.stringify(value), { mode: 0o600 });

export function environment(source = process.env) {
  const env = { ...source };
  for (const key of Object.keys(env)) {
    if (key.startsWith('INPUT_') || ['NODE_AUTH_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN', 'MISE_GITHUB_TOKEN', 'NPM_TOKEN'].includes(key)) delete env[key];
  }
  return env;
}

export function invoke(command, args, { capture = false, stdin, env = environment(), allowFailure = false, cwd } = {}) {
  const result = spawnSync(command, args, {
    encoding: 'utf8', stdio: [stdin === undefined ? 'ignore' : 'pipe', capture ? 'pipe' : 'inherit', 'inherit'],
    input: stdin, env, cwd,
  });
  const status = result.status ?? 1;
  if (status && !allowFailure) throw new Error(`${path.basename(command)} failed (exit ${status})`);
  return capture ? { status, stdout: result.stdout ?? '' } : status;
}

export function append(file, name, value) {
  if (!file) throw new Error('Missing Actions file channel');
  const delimiter = randomUUID();
  fs.appendFileSync(file, `${name}<<${delimiter}\n${value}\n${delimiter}\n`);
}

export function stateRoot(root) {
  const parent = fs.realpathSync(process.env.RUNNER_TEMP);
  if (!root || !path.isAbsolute(root) || !path.basename(root).startsWith('project-command-')
      || fs.realpathSync(path.dirname(root)) !== parent || fs.lstatSync(root).isSymbolicLink()) {
    throw new Error('Invalid project environment state location');
  }
  return root;
}

export function loadState(root) {
  stateRoot(root);
  const state = read(path.join(root, 'state.json'));
  if (state.version !== 1 || state.run !== process.env.GITHUB_RUN_ID
      || state.attempt !== process.env.GITHUB_RUN_ATTEMPT || state.job !== process.env.GITHUB_JOB
      || !/^project-command-[a-f0-9-]+$/.test(state.label || '')) {
    throw new Error('Project environment belongs to another job');
  }
  return state;
}

export function runtimeConfiguration(state, root) {
  for (const value of [state.workspace, root, state.remote]) {
    if (/[\r\n,]/.test(value)) throw new Error('Unsupported project bind-mount path');
  }
  return {
    image: state.reference, workspaceFolder: state.remote,
    workspaceMount: `type=bind,source=${state.workspace},target=${state.remote}`,
    mounts: [
      `type=bind,source=${root},target=/run/project-command,readonly`,
      `type=bind,source=${root}/io,target=/run/project-command/io`,
    ],
    containerUser: 'vscode', remoteUser: 'vscode', updateRemoteUserUID: true, userEnvProbe: 'none',
    remoteEnv: {
      CI: 'true', GITHUB_WORKSPACE: state.remote, GITHUB_EVENT_PATH: '/run/project-command/event.json',
      GIT_CONFIG_GLOBAL: '/run/project-command/gitconfig', RUNNER_TEMP: '/run/project-command/io/tmp',
    },
  };
}

export function qualifyMetadata(result, config) {
  const merged = result.mergedConfiguration;
  if (!merged || result.configuration?.image !== config.image
      || merged.remoteUser !== 'vscode' || merged.containerUser !== 'vscode'
      || merged.privileged || merged.capAdd?.length || merged.securityOpt?.length || merged.runArgs?.length
      || merged.initializeCommand || merged.features
      || JSON.stringify(merged.mounts) !== JSON.stringify(config.mounts)
      || merged.workspaceMount !== config.workspaceMount || merged.workspaceFolder !== config.workspaceFolder
      || JSON.stringify(merged.remoteEnv) !== JSON.stringify(config.remoteEnv)
      || merged.onCreateCommands?.some(command => command !== '/usr/local/share/rubio-baseline/claim-mise-shims')
      || ['updateContentCommands', 'postCreateCommands', 'postStartCommands', 'postAttachCommands']
        .some(key => merged[key]?.length)) {
    throw new Error('Unqualified Repository Image metadata, privileges, lifecycle or mounts');
  }
}

function cli(state, root, args, options) {
  const [operation, ...rest] = args;
  return invoke(path.join(root, 'cli/node_modules/.bin/devcontainer'), [
    operation, '--workspace-folder', state.workspace, '--config', path.join(root, 'devcontainer.json'),
    '--id-label', `rubio.project-command=${state.label}`, ...rest,
  ], options);
}

function prepare() {
  if (process.env.RUNNER_ENVIRONMENT !== 'github-hosted' || process.env.RUNNER_OS !== 'Linux'
      || process.env.RUNNER_ARCH !== 'X64') throw new Error('Hosted Repository Images require a GitHub-hosted Linux X64 VM');
  if (!/^Rubio-Enterprises\/[A-Za-z0-9_.-]+$/i.test(process.env.GITHUB_REPOSITORY || '')) throw new Error('Invalid base repository');
  if (!input('token')) throw new Error('Repository Image pull requires the repository GITHUB_TOKEN');
  const prepareStart = performance.now();
  const root = fs.mkdtempSync(path.join(fs.realpathSync(process.env.RUNNER_TEMP), 'project-command-'));
  const state = {
    version: 1, label: 'project-command-' + randomUUID(),
    run: process.env.GITHUB_RUN_ID, attempt: process.env.GITHUB_RUN_ATTEMPT, job: process.env.GITHUB_JOB,
    workspace: fs.realpathSync(process.env.GITHUB_WORKSPACE),
    remote: '/workspaces/' + process.env.GITHUB_REPOSITORY.split('/')[1],
    source: `ghcr.io/${process.env.GITHUB_REPOSITORY.toLowerCase()}/devcontainer:main`,
    prepared: false,
  };
  write(path.join(root, 'state.json'), state);
  append(process.env.GITHUB_STATE, 'project_command_state', root);
  fs.mkdirSync(path.join(root, 'io/tmp'), { recursive: true });
  fs.mkdirSync(path.join(root, 'commands'));
  fs.writeFileSync(path.join(root, 'gitconfig'), '', { mode: 0o600 });
  fs.copyFileSync(process.env.GITHUB_EVENT_PATH, path.join(root, 'event.json'));
  state.previousImages = invoke('docker', ['image', 'ls', '--quiet', '--no-trunc'], { capture: true }).stdout.trim().split(/\s+/).filter(Boolean);
  state.previousVolumes = invoke('docker', ['volume', 'ls', '--quiet'], { capture: true }).stdout.trim().split(/\s+/).filter(Boolean);
  write(path.join(root, 'state.json'), state);
  const npmEnv = {
    ...environment(), npm_config_cache: path.join(root, 'npm-cache'),
    NPM_CONFIG_USERCONFIG: path.join(root, 'npm-userconfig'),
    NPM_CONFIG_GLOBALCONFIG: path.join(root, 'npm-globalconfig'),
  };
  const cliStart = performance.now();
  invoke('npm', ['install', '--prefix', path.join(root, 'cli'), '--ignore-scripts', '--no-audit', '--no-fund', '@devcontainers/cli@0.89.0'], { env: npmEnv });
  state.cliSeconds = seconds(cliStart);
  const auth = path.join(root, 'auth');
  fs.mkdirSync(auth, { mode: 0o700 });
  const pullStart = performance.now();
  try {
    invoke('docker', ['--config', auth, 'login', 'ghcr.io', '--username', process.env.GITHUB_ACTOR, '--password-stdin'], { capture: true, stdin: input('token') });
    invoke('docker', ['--config', auth, 'pull', state.source]);
    const [pulled] = JSON.parse(invoke('docker', ['image', 'inspect', state.source], { capture: true }).stdout);
    state.image = pulled.Id;
    const repository = state.source.slice(0, state.source.lastIndexOf(':'));
    const references = (pulled.RepoDigests ?? []).filter(value => typeof value === 'string'
      && value.startsWith(repository + '@') && imageId.test(value.slice(repository.length + 1)));
    if (!imageId.test(state.image || '') || references.length !== 1) throw new Error('Invalid immutable Repository Image identity');
    state.reference = references[0];
    state.pullSeconds = seconds(pullStart);
  } finally {
    fs.rmSync(auth, { recursive: true, force: true });
  }
  const start = performance.now();
  const config = runtimeConfiguration(state, root);
  write(path.join(root, 'devcontainer.json'), config);
  write(path.join(root, 'state.json'), state);
  qualifyMetadata(JSON.parse(cli(state, root, ['read-configuration', '--include-merged-configuration'], { capture: true }).stdout), config);
  const upResult = cli(state, root, ['up', '--skip-post-create', '--default-user-env-probe', 'none', '--log-format', 'json'], {
    capture: true, allowFailure: true,
  });
  const up = JSON.parse(upResult.stdout || '{}');
  if (upResult.status) {
    if (typeof up.description === 'string') console.error(up.description);
    throw new Error('Dev Container startup failed; see the credential-free startup diagnostic');
  }
  if (!containerId.test(up.containerId || '')) throw new Error('CLI did not return a container identity');
  state.container = up.containerId;
  write(path.join(root, 'state.json'), state);
  const [container] = JSON.parse(invoke('docker', ['inspect', state.container], { capture: true }).stdout);
  if (container.Config?.Labels?.['rubio.project-command'] !== state.label) throw new Error('CLI container ownership mismatch');
  invoke('docker', ['exec', '--user', 'root', state.container, 'chown', '-R',
    `${process.getuid()}:${process.getgid()}`, '/usr/local/share/mise', '/usr/local/share/mise-shims']);
  cli(state, root, ['run-user-commands', '--default-user-env-probe', 'none', '--skip-post-attach']);
  const uid = cli(state, root, ['exec', 'id', '-u'], { capture: true }).stdout.trim();
  if (!/^\d+$/.test(uid) || uid === '0') throw new Error('Project commands must run as a nonroot user');
  state.prepared = true;
  state.startSeconds = seconds(start);
  state.prepareSeconds = seconds(prepareStart);
  write(path.join(root, 'state.json'), state);
  append(process.env.GITHUB_ENV, 'PROJECT_COMMAND_STATE', root);
  for (const [key, value] of Object.entries({
    image: state.image, reference: state.reference, 'pull-seconds': state.pullSeconds, 'start-seconds': state.startSeconds,
    'cli-seconds': state.cliSeconds, 'prepare-seconds': state.prepareSeconds,
  })) append(process.env.GITHUB_OUTPUT, key, value);
  console.log(`Repository Image: ${state.reference}; ID ${state.image}; pull ${state.pullSeconds}s; start ${state.startSeconds}s; CLI install ${state.cliSeconds}s; prepare total ${state.prepareSeconds}s`);
}

export function forwardNames(value) {
  const names = value.trim() ? value.trim().split(/\s+/) : [];
  if (names.some(name => !/^[A-Z_][A-Z0-9_]*$/.test(name) || protectedNames.has(name))) {
    throw new Error('forward-env must contain unprotected environment variable names, not values');
  }
  return [...new Set([...contextNames, ...names])];
}

function run(hosted) {
  if (!input('run').trim()) throw new Error('A caller-owned project command is required');
  if (!hosted) {
    const root = fs.mkdtempSync(path.join(fs.realpathSync(process.env.RUNNER_TEMP), 'project-command-native-'));
    try {
      const script = path.join(root, 'command.sh');
      fs.writeFileSync(script, input('run'), { mode: 0o600 });
      return invoke('bash', ['--noprofile', '--norc', '-e', '-o', 'pipefail', script], {
        env: process.env, cwd: process.env.GITHUB_WORKSPACE, allowFailure: true,
      });
    } finally { fs.rmSync(root, { recursive: true, force: true }); }
  }
  const root = process.env.PROJECT_COMMAND_STATE;
  const state = loadState(root);
  if (!state.prepared || !imageId.test(state.image || '') || !containerId.test(state.container || '')
      || fs.realpathSync(process.env.GITHUB_WORKSPACE) !== state.workspace) throw new Error('Hosted project environment is not prepared');
  const config = runtimeConfiguration(state, root);
  const env = environment();
  for (const key of forwardNames(input('forward-env'))) {
    if (Object.hasOwn(process.env, key)) {
      config.remoteEnv[key] = '${localEnv:' + key + '}';
      env[key] = process.env[key];
    }
  }
  const step = randomUUID();
  const directory = path.join(root, 'io', step);
  fs.mkdirSync(directory);
  const script = path.join(root, 'commands', step + '.sh');
  fs.writeFileSync(script, input('run'), { mode: 0o600 });
  const handles = new Map();
  for (const key of channels) {
    handles.set(key, fs.openSync(path.join(directory, key), 'wx+', 0o600));
    config.remoteEnv[key] = `/run/project-command/io/${step}/${key}`;
  }
  write(path.join(root, 'devcontainer.json'), config);
  try {
    return cli(state, root, ['exec', 'bash', '--noprofile', '--norc', '-e', '-o', 'pipefail',
      '/run/project-command/commands/' + step + '.sh'], { env, allowFailure: true });
  } finally {
    for (const [key, handle] of handles) {
      try {
        const bytes = fs.readFileSync(handle);
        if (bytes.length && process.env[key]) fs.appendFileSync(process.env[key], bytes);
      } finally { fs.closeSync(handle); }
    }
    write(path.join(root, 'devcontainer.json'), runtimeConfiguration(state, root));
    fs.rmSync(script, { force: true });
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

export function main() {
  const hosted = input('hosted-image');
  if (!['true', 'false'].includes(hosted)) throw new Error('hosted-image must be true or false');
  if (input('operation') === 'prepare') {
    if (hosted === 'true') prepare();
    return 0;
  }
  if (input('operation') !== 'run') throw new Error('operation must be prepare or run');
  return run(hosted === 'true');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.exitCode = main(); }
  catch (error) { console.error('::error::' + error.message); process.exitCode = 1; }
}
