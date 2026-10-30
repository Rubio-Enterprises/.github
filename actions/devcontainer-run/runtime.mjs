import fs from 'node:fs';
import path from 'node:path';

const state = process.env.PROJECT_IMAGE_STATE;
if (process.argv[2] === 'verify') {
  const config = JSON.parse(fs.readFileSync(path.join(state, 'devcontainer.json'), 'utf8'));
  const {configuration, mergedConfiguration: merged} = JSON.parse(fs.readFileSync(path.join(state, 'read.json'), 'utf8'));
  if (!merged || configuration.image !== config.image || merged.remoteUser !== 'vscode' ||
      merged.privileged || merged.capAdd?.length || merged.securityOpt?.length ||
      JSON.stringify(merged.mounts) !== JSON.stringify(config.mounts) ||
      merged.onCreateCommands?.some(command => command !== '/usr/local/share/rubio-baseline/claim-mise-shims') ||
      merged.updateContentCommands?.length || merged.postStartCommands?.length || merged.postAttachCommands?.length ||
      JSON.stringify(merged.postCreateCommands) !== JSON.stringify([config.postCreateCommand])) {
    throw new Error('Unqualified image metadata: CI requires the baseline lifecycle without extra host mounts or privileges');
  }
  process.exit(0);
}
const workspace = fs.realpathSync(process.env.GITHUB_WORKSPACE);
const remote = '/workspaces/' + process.env.GITHUB_REPOSITORY.split('/')[1];
if ([state, workspace].some(value => /[,\n\r]/.test(value))) throw new Error('Unsupported bind-mount path');
const remoteEnv = {
  CI: 'true', MISE_ENV: 'ci', TURBO_CONCURRENCY: '2', GIT_TERMINAL_PROMPT: '0',
  GCM_INTERACTIVE: 'never', STANDARDS_PIN: process.env.STANDARDS_PIN,
  GITHUB_WORKSPACE: remote, GIT_CONFIG_GLOBAL: '/run/project-image/gitconfig',
};
for (const key of ['GITHUB_ACTIONS', 'GITHUB_REPOSITORY', 'GITHUB_REPOSITORY_ID', 'GITHUB_SHA',
  'GITHUB_REF', 'GITHUB_BASE_REF', 'GITHUB_EVENT_NAME', 'IS_FORK', 'HAS_KNOWLEDGEBASE',
  'MISE_GITHUB_TOKEN', 'GOPRIVATE']) {
  if (process.env[key]) remoteEnv[key] = '${localEnv:' + key + '}';
}
const config = {
  image: process.env.PROJECT_IMAGE_ID,
  workspaceFolder: remote,
  workspaceMount: 'type=bind,source=' + workspace + ',target=' + remote,
  mounts: ['type=bind,source=' + state + ',target=/run/project-image,readonly'],
  containerUser: 'vscode', remoteUser: 'vscode', updateRemoteUserUID: true, userEnvProbe: 'none',
  remoteEnv,
  postCreateCommand: 'bash /run/project-image/preflight.sh && mise install && mise run setup',
};
fs.writeFileSync(path.join(state, 'devcontainer.json'), JSON.stringify(config), {mode: 0o600});
