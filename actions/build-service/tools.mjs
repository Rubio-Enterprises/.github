import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

const run = (command, args) => execFileSync(command, args, { encoding: 'utf8' }).trim();
const releases = fileURLToPath(new URL('../release-asset/', import.meta.url));
const pins = JSON.parse(fs.readFileSync(path.join(releases, 'pins.json'), 'utf8'));
const release = name => ({ ...pins[`${name}-Linux-X64`], name });

export const CLIENT_TOOLS = [
  {
    name: 'docker', version: '29.9.2', url: 'https://download.docker.com/linux/static/stable/x86_64/docker-29.9.2.tgz',
    sha256: '9ac225999fd5e863f7694c728ef23d480616b1e5639328fa309befd4243d69eb', member: 'docker/docker',
  },
  { ...release('docker-buildx'), plugin: true },
  release('crane'),
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
    if (versionMatches(tool, env)) {
      console.log(`${tool.name}: already available`);
      continue;
    }
    let bytes;
    if (tool.repo) {
      const archive = path.join(root, `${tool.name}.download`);
      execFileSync('bash', [path.join(releases, 'fetch.sh'), tool.repo, tool.tag,
        tool.asset.replace('{version}', tool.tag.replace(/^v/, '')), tool.sha256, archive], { env, stdio: 'inherit' });
      bytes = fs.readFileSync(archive);
      fs.rmSync(archive);
    } else {
      const response = await fetch(tool.url);
      if (!response.ok) throw new Error(`${tool.name} download failed: ${response.status}`);
      bytes = Buffer.from(await response.arrayBuffer());
      if (createHash('sha256').update(bytes).digest('hex') !== tool.sha256) throw new Error(`${tool.name} checksum mismatch`);
    }
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
    console.log(`${tool.name}: installed pinned ${tool.tag || tool.version}`);
  }
  fs.appendFileSync(env.GITHUB_PATH, `${bin}\n`);
}

function versionMatches(tool, env) {
  const command = tool.plugin ? 'docker' : tool.name;
  if (!onPath(command, env)) return false;
  try {
    const args = tool.plugin ? ['buildx', 'version'] : tool.name === 'crane' ? ['version'] : ['--version'];
    const output = execFileSync(command, args, { env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
    const version = (tool.tag || tool.version).replace(/^v/, '');
    if (tool.plugin) return output.split(/\s+/)[1] === `v${version}`;
    if (tool.name === 'docker') return output.split(/\s+/)[2] === `${version},`;
    return output === version;
  } catch { return false; }
}


if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await installTools();
