import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

const run = (command, args) => execFileSync(command, args, { encoding: 'utf8' }).trim();
const CRANE = {
  version: '0.22.1',
  sha256: '0ab7a1d6932a213aed964ce97666c3077fe691c8606413674a8b3e0b9ec4cda0',
};

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


if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await installTools();
