import fs from 'node:fs';
import { standardsShape } from '../repository-image/image.mjs';

export function selected(raw, id) {
  if (!raw) return false;
  if (raw === 'all') return true;
  const ids = JSON.parse(raw);
  if (!Array.isArray(ids) || ids.some(value => !/^[1-9][0-9]*$/.test(String(value)) ||
      (typeof value !== 'string' && !Number.isSafeInteger(value)))) {
    throw new Error('CI_IMAGE_REPOS must be a JSON array of repository IDs, or all');
  }
  return ids.some(value => String(value) === id);
}

export function route(env, answers, shape = 'legacy') {
  const runner = JSON.parse(env.ORIGINAL_RUNNER);
  if (!selected(env.CI_IMAGE_REPOS, env.GITHUB_REPOSITORY_ID)) return {runner, mode: ''};
  if (!Array.isArray(runner) || !runner.length || runner.some(x => typeof x !== 'string')) {
    throw new Error('The existing runner route must be a nonempty label array');
  }
  if (!['lint-hooks', 'typecheck', 'build'].includes(env.PROJECT_TASK)) throw new Error('Unknown project task');
  if (shape !== 'v2' && env.PROJECT_TASK === 'lint-hooks' &&
      /^(has_(swift|homebrew_formulae):\s*true|archetype:\s*swift)/m.test(answers)) return {runner, mode: ''};
  if (shape !== 'v2' && env.PROJECT_TASK === 'build' && /^has_swift:\s*true\s*$/m.test(answers)) return {runner, mode: ''};
  const machine = ['linux-x64', 'linux-arm64', 'ubuntu-24.04-arm'];
  if (shape === 'v2' && runner.some(label => ['linux-x64-large', 'linux-kvm'].includes(label))) return {runner, mode: ''};
  if (runner.some(label => machine.includes(label) || /^macos-(\d+|arm64|tart)$/.test(label))) return {runner, mode: ''};
  const key = env.PROJECT_TASK === 'lint-hooks' ? 'lint_hooks' : 'typecheck';
  const medium = shape === 'v2' ? runner.includes('linux-x64-medium') : env.PROJECT_TASK === 'build' ||
    new RegExp(`^${key}_workload_class:[ \\t]*["']?(medium|heavy|glue-heavy)["']?[ \\t]*$`, 'mi').test(answers);
  const size = medium ? 'medium' : 'small';
  if (runner.every(label => /^ubuntu-(slim|latest|24\.04)$/.test(label))) {
    return {runner: ['ubuntu-latest'], mode: 'hosted'};
  }
  if (runner.some(label => label === 'linux-x64-' + size) &&
      runner.every(label => ['self-hosted', 'Linux', 'X64', 'linux-x64-' + size].includes(label))) {
    if (!/^[1-9][0-9]*$/.test(env.GITHUB_REPOSITORY_ID) ||
        !/^Rubio-Enterprises\/[A-Za-z0-9_.-]+$/.test(env.GITHUB_REPOSITORY)) {
      throw new Error('Invalid repository identity');
    }
    const slug = env.GITHUB_REPOSITORY.split('/')[1].toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
    if (!slug) throw new Error('Empty repository label slug');
    return {runner: ['ci-' + env.GITHUB_REPOSITORY_ID + '-' + slug + '-' + size], mode: 'direct'};
  }
  throw new Error('CI Image route-contract error: custom self-hosted labels require qualification');
}

if (process.argv[1] === new URL(import.meta.url).pathname) {
  try {
    const answers = fs.existsSync('.copier-answers.yml') ? fs.readFileSync('.copier-answers.yml', 'utf8') : '';
    const shape = !answers && selected(process.env.CI_IMAGE_REPOS, process.env.GITHUB_REPOSITORY_ID)
      ? standardsShape(process.cwd()).shape : 'legacy';
    const result = route(process.env, answers, shape);
    fs.appendFileSync(process.env.GITHUB_OUTPUT, 'runner=' + JSON.stringify(result.runner) + '\nmode=' + result.mode + '\n');
  } catch (error) {
    console.error('::error::' + error.message);
    process.exitCode = 1;
  }
}
