import fs from 'node:fs';
import { environment, invoke, loadState } from './index.mjs';

const root = process.env.STATE_project_command_state;
if (root) {
  try {
    const state = loadState(root);
    const result = invoke('docker', ['ps', '-aq', '--filter', `label=rubio.project-command=${state.label}`], { capture: true });
    const ids = result.stdout.trim().split(/\s+/).filter(Boolean);
    if (ids.some(id => !/^[a-f0-9]{12,64}$/.test(id))) throw new Error('Invalid owned-container cleanup result');
    const images = new Set(state.image ? [state.image] : []);
    const volumes = new Set();
    for (const id of ids) {
      const [container] = JSON.parse(invoke('docker', ['inspect', id], { capture: true }).stdout);
      if (container.Config?.Labels?.['rubio.project-command'] !== state.label) throw new Error('Cleanup container ownership mismatch');
      images.add(container.Image);
      for (const mount of container.Mounts ?? []) if (mount.Type === 'volume') volumes.add(mount.Name);
      invoke('docker', ['rm', '--force', id], { env: environment() });
    }
    for (const name of volumes) {
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(name)) throw new Error('Invalid owned volume name');
      if (!(state.previousVolumes ?? []).includes(name)) invoke('docker', ['volume', 'rm', name]);
    }
    for (const id of [...images].reverse()) {
      if (!/^sha256:[a-f0-9]{64}$/.test(id || '')) throw new Error('Invalid owned image identity');
      if (!(state.previousImages ?? []).includes(id)) invoke('docker', ['image', 'rm', id]);
    }
    fs.rmSync(root, { recursive: true, force: true });
    console.log('Hosted project environment cleanup complete');
  } catch (error) {
    console.error('::error::Project environment cleanup failed: ' + error.message);
    process.exitCode = 1;
  }
}
