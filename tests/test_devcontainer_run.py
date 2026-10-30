from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_native_config_workflows import condition
from test_ruby_workflows import ROOT, extract_run_block, extract_step

ASSETS = ROOT / 'actions/devcontainer-run'
WORKFLOWS = ROOT / '.github/workflows'


def invoke_route(raw, runner, task='lint-hooks', answers='', name='Mixed.Repo', repo_id='123'):
    source = f"import {{route}} from {json.dumps((ASSETS / 'route.mjs').as_uri())}; console.log(JSON.stringify(route(JSON.parse(process.argv[1]), process.argv[2])));"
    return subprocess.run(['node', '--input-type=module', '-e', source, json.dumps({
        'CI_IMAGE_REPOS': raw, 'ORIGINAL_RUNNER': json.dumps(runner),
        'PROJECT_TASK': task, 'GITHUB_REPOSITORY': 'Rubio-Enterprises/' + name,
        'GITHUB_REPOSITORY_ID': repo_id,
    }), answers], text=True, capture_output=True)


class Routing(unittest.TestCase):
    def test_unlisted_routes_are_identical(self):
        for raw in ('', '[]', '[456]', '["456"]'):
            for runner in (['custom'], ['ubuntu-slim'], ['linux-x64-small'], ['macos-arm64']):
                with self.subTest(raw=raw, runner=runner):
                    result = invoke_route(raw, runner)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), {'runner': runner, 'mode': ''})

    def test_selected_sizes_and_identity_match_inventory(self):
        for raw in ('all', '[123]', '["123"]'):
            for task, key in [('lint-hooks', 'lint_hooks'), ('typecheck', 'typecheck')]:
                for value, size in [('', 'small'), ('small', 'small'), ('light', 'small'),
                                    ('glue', 'small'), ('medium', 'medium'), ('heavy', 'medium'),
                                    ('glue-heavy', 'medium'), ('"MEDIUM"', 'medium'), ('medium-extra', 'small')]:
                    with self.subTest(raw=raw, task=task, value=value):
                        result = invoke_route(raw, ['linux-x64-' + size], task, f'{key}_workload_class: {value}\n')
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(json.loads(result.stdout), {
                            'runner': [f'ci-123-mixed-repo-{size}'], 'mode': 'direct'})
        result = invoke_route('all', ['self-hosted', 'Linux', 'X64', 'linux-x64-medium'], 'build', name='a' * 50)
        self.assertEqual(json.loads(result.stdout)['runner'], ['ci-123-' + 'a' * 40 + '-medium'])

    def test_hosted_only_and_fixed_exceptions(self):
        for runner in [['ubuntu-slim'], ['ubuntu-latest'], ['ubuntu-24.04']]:
            result = invoke_route('all', runner)
            self.assertEqual(json.loads(result.stdout), {'runner': ['ubuntu-latest'], 'mode': 'hosted'})
        for runner in [['linux-x64'], ['linux-arm64'], ['macos-arm64'], ['macos-26'], ['ubuntu-24.04-arm']]:
            result = invoke_route('all', runner)
            self.assertEqual(json.loads(result.stdout), {'runner': runner, 'mode': ''})
        for answer in ('has_swift: true\n', 'has_homebrew_formulae: true\n',
                       'has_swift: true # legacy\n', 'archetype: swift-cli\n'):
            result = invoke_route('all', ['macos-26'], answers=answer)
            self.assertEqual(json.loads(result.stdout)['mode'], '')

    def test_no_fallback_for_custom_or_malformed_contracts(self):
        for runner in (['custom'], ['self-hosted', 'ubuntu-latest'], ['linux-x64-small', 'other'], []):
            self.assertNotEqual(invoke_route('all', runner).returncode, 0)
        for raw in ('true', '{}', '["all"]', '[0]', '[1.5]', '[{}]', '[true]', '[null]', 'oops'):
            self.assertNotEqual(invoke_route(raw, ['linux-x64-small']).returncode, 0)


class Runtime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'checkout with spaces'
        self.repo.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = {**os.environ, 'GIT_CONFIG_GLOBAL': str(self.root / 'gitconfig'),
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_SYSTEM': '/dev/null',
                    'GIT_AUTHOR_NAME': 'test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
                    'GIT_COMMITTER_NAME': 'test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid',
                    'GITHUB_ACTION_PATH': str(ASSETS), 'GITHUB_WORKSPACE': str(self.repo),
                    'RUNNER_TEMP': str(self.root), 'RUNNER_ENVIRONMENT': 'github-hosted',
                    'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64', 'IMAGE_MODE': 'hosted',
                    'PROJECT_TASK': 'typecheck', 'GITHUB_REPOSITORY': 'Rubio-Enterprises/Test',
                    'GITHUB_RUN_ID': '1', 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_JOB': 'test',
                    'GITHUB_ACTOR': 'test', 'GITHUB_BASE_REF': '', 'IMAGE_PULL_TOKEN': 'not-a-real-token',
                    'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                    'TRACE': str(self.root / 'trace'), 'FIXTURE': str(self.root)}
        for key in ['CONFIG', 'DATA', 'STATE', 'CACHE']:
            self.env[f'MISE_{key}_DIR'] = str(self.root / f'mise-{key.lower()}')
        self.env['MISE_GLOBAL_CONFIG_FILE'] = str(self.root / 'mise-global.toml')
        self.git('init', '-q', str(self.repo / '.standards-config'))
        self.shared = self.repo / '.standards-config'
        (self.shared / 'config/lefthook').mkdir(parents=True)
        (self.shared / 'config/lefthook/base.yml').write_text('pre-commit: {}\n')
        self.git('-C', str(self.shared), 'add', '.')
        self.git('-C', str(self.shared), 'commit', '-qm', 'fixture')
        self.pin = self.git('-C', str(self.shared), 'rev-parse', 'HEAD').strip()
        self.git('-C', str(self.shared), 'remote', 'add', 'origin', 'https://github.com/Rubio-Enterprises/standards.git')
        self.env['STANDARDS_PIN'] = self.pin
        self.executable(self.bin / 'mise', '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['TRACE'], 'a') as f: f.write('mise ' + ' '.join(sys.argv[1:]) + '\\n')
if sys.argv[1:3] == ['tasks', 'info'] and os.getenv('NO_TASK'): sys.exit(1)
if sys.argv[1] == 'exec':
 if os.getenv('REWRITE_TRACKED'):
  from pathlib import Path
  Path('tracked.txt').write_text('formatted')
 sys.exit(int(os.getenv('HOOKS_EXIT', '0')))
if sys.argv[1:5] == ['bootstrap', 'repos', 'status', '--missing']:
 p = os.environ['STANDARDS_PIN']
 print(json.dumps({'repos': [] if os.getenv('BAD_STATUS') else [{'path_raw': '.standards-config', 'url': 'https://github.com/Rubio-Enterprises/standards.git', 'ref': p, 'current_sha': p, 'state': 'current'}]}))
''')
        self.executable(self.bin / 'docker', '''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ['FIXTURE'])
with open(os.environ['TRACE'], 'a') as f: f.write('docker ' + ' '.join(args) + '\\n')
if 'login' in args:
 sys.stdin.read()
elif 'pull' in args:
 sys.exit(int(os.getenv('PULL_FAIL', '0')))
elif args[:2] == ['image', 'inspect']:
 print('sha256:' + 'a'*64)
elif args[0] == 'inspect':
 print('project-image-1-1-test')
elif args[0] == 'ps' and (root / 'started').exists():
 print('b'*64)
''')
        cli = self.root / 'project-devcontainer-cli/node_modules/.bin/devcontainer'
        cli.parent.mkdir(parents=True)
        self.executable(cli, '''#!/usr/bin/env python3
import json, os, pathlib, signal, sys, time
args = sys.argv[1:]
root = pathlib.Path(os.environ['FIXTURE'])
config = json.loads(pathlib.Path(args[args.index('--config') + 1]).read_text())
assert not os.getenv('IMAGE_PULL_TOKEN')
assert not (pathlib.Path(args[args.index('--config') + 1]).parent / 'auth').exists()
with open(os.environ['TRACE'], 'a') as f: f.write('cli ' + args[0] + '\\n')
if args[0] == 'read-configuration':
 (root / 'config-copy.json').write_text(json.dumps(config))
 (root / 'gitconfig-copy').write_text((pathlib.Path(args[args.index('--config') + 1]).parent / 'gitconfig').read_text())
 merged = dict(config, onCreateCommands=['/usr/local/share/rubio-baseline/claim-mise-shims'], postCreateCommands=[config['postCreateCommand']])
 if os.getenv('BAD_METADATA'): merged['privileged'] = True
 print(json.dumps({'configuration': config, 'mergedConfiguration': merged}))
elif args[0] == 'up':
 assert '--skip-post-create' in args
 (root / 'started').touch()
 if os.getenv('WAIT_UP'):
  while True: time.sleep(1)
 print(json.dumps({'containerId': 'b'*64}))
elif args[0] == 'exec':
 if args[-1].endswith('preflight.sh'):
  (root / 'preflight').touch()
  sys.exit(int(os.getenv('INNER_FAIL', '0')))
 else:
  assert (root / 'lifecycle').exists()
  sys.exit(int(os.getenv('TASK_EXIT', '0')))
elif args[0] == 'run-user-commands':
 assert (root / 'preflight').exists()
 (root / 'lifecycle').touch()
''')

    def git(self, *args):
        return subprocess.check_output(['git', *args], env=self.env, text=True)

    def executable(self, path, contents):
        path.write_text(contents)
        path.chmod(0o755)

    def run_image(self, **extra):
        return subprocess.run(['bash', str(ASSETS / 'run.sh')], cwd=self.repo,
                              env={**self.env, **extra}, capture_output=True, text=True)

    def trace(self):
        return (self.root / 'trace').read_text() if (self.root / 'trace').exists() else ''

    def assert_clean(self):
        self.assertFalse(list(self.root.glob('project-image.*')))
        self.assertNotIn('not-a-real-token', self.trace())

    def test_image_task_semantics_for_fork_rewrites_and_missing_tasks(self):
        self.git('init', '-q', str(self.repo))
        (self.repo / '.gitignore').write_text('.standards-config/\n')
        (self.repo / 'tracked.txt').write_text('original')
        self.git('-C', str(self.repo), 'add', '.')
        self.git('-C', str(self.repo), 'commit', '-qm', 'consumer fixture')
        env = {**self.env, 'IS_FORK': 'true', 'HAS_KNOWLEDGEBASE': 'true'}
        result = subprocess.run(['bash', str(ASSETS / 'lint-hooks.sh')], cwd=self.repo,
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('lefthook run pre-commit --no-stage-fixed --files-from-stdin', self.trace())
        self.assertIn('lefthook run kb-check', self.trace())
        result = subprocess.run(['bash', str(ASSETS / 'lint-hooks.sh')], cwd=self.repo,
                                env={**env, 'REWRITE_TRACKED': '1'}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('hooks rewrote tracked files', result.stdout)
        for task, status in [('typecheck', 0), ('build', 1)]:
            result = subprocess.run(['bash', str(ASSETS / (task + '.sh'))], cwd=self.repo,
                                    env={**self.env, 'NO_TASK': '1'}, text=True, capture_output=True)
            self.assertEqual(result.returncode, status, result.stderr)

    def test_hosted_order_mounts_permissions_and_exit(self):
        result = self.run_image(TASK_EXIT='42')
        self.assertEqual(result.returncode, 42, result.stderr)
        trace = self.trace()
        order = ['cli read-configuration', 'cli up', 'docker exec --user root',
                 'cli exec', 'cli run-user-commands']
        self.assertEqual([trace.index(x) for x in order], sorted(trace.index(x) for x in order))
        config = json.loads((self.root / 'config-copy.json').read_text())
        self.assertEqual(config['image'], 'sha256:' + 'a' * 64)
        self.assertIn(str(self.repo), config['workspaceMount'])
        self.assertTrue(config['mounts'][0].endswith(',readonly'))
        self.assertEqual(config['remoteUser'], 'vscode')
        self.assertTrue(config['updateRemoteUserUID'])
        self.assertEqual(config['remoteEnv']['STANDARDS_PIN'], self.pin)
        self.assertNotIn('build', config)
        self.assertNotIn('features', config)
        self.assertNotIn('initializeCommand', config)
        self.assertIn('docker rm -f ' + 'b' * 64, trace)
        self.assert_clean()

    def test_host_git_credentials_are_never_forwarded(self):
        for repo in ['private-module', 'knowledgebase-framework', 'knowledgebase-framework.git', 'standards', 'unrelated']:
            self.git('config', '--global', 'url.https://x-access-token:fixture@github.com/Rubio-Enterprises/' + repo + '.insteadOf',
                     'https://github.com/Rubio-Enterprises/' + repo)
        result = self.run_image(GO_PRIVATE_MODULE_REPOS='private-module')
        self.assertEqual(result.returncode, 0, result.stderr)
        copied = (self.root / 'gitconfig-copy').read_text()
        self.assertEqual(copied, '')
        self.assertNotIn('fixture@', self.trace())
        self.assert_clean()

    def test_bad_pins_and_missing_declarations_precede_docker(self):
        for pin in ('', 'a' * 39, 'A' * 40, '0' * 40):
            with self.subTest(pin=pin):
                result = self.run_image(STANDARDS_PIN=pin)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('docker ', self.trace())
        result = self.run_image(BAD_STATUS='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('docker ', self.trace())

    def test_dirty_wrong_origin_and_shallow_fail(self):
        (self.shared / 'untracked').write_text('unexpected')
        self.assertNotEqual(self.run_image().returncode, 0)
        (self.shared / 'untracked').unlink()
        self.git('-C', str(self.shared), 'remote', 'set-url', 'origin', 'https://example.invalid/standards')
        self.assertNotEqual(self.run_image().returncode, 0)
        self.assertNotIn('docker ', self.trace())
        self.git('-C', str(self.shared), 'remote', 'set-url', 'origin', 'https://github.com/Rubio-Enterprises/standards.git')
        (self.shared / '.git/shallow').write_text(self.pin + '\n')
        self.assertNotEqual(self.run_image().returncode, 0)
        self.assertNotIn('docker ', self.trace())

    def test_image_missing_and_inner_pin_fail_closed(self):
        result = self.run_image(PULL_FAIL='17')
        self.assertEqual(result.returncode, 17)
        self.assertNotIn('cli ', self.trace())
        self.assert_clean()
        result = self.run_image(INNER_FAIL='23')
        self.assertEqual(result.returncode, 23)
        self.assertNotIn('cli run-user-commands', self.trace())
        self.assertFalse((self.root / 'lifecycle').exists())
        self.assert_clean()

    def test_direct_execution_has_no_cli_and_preserves_nonroot_contract(self):
        hook = self.root / 'bash-env'
        hook.write_text("""test() {
  case "${2:-}" in
    /opt/actions-runner/bin/Runner.Listener|/usr/local/share/mise|/var/cache/devcontainer) return 0 ;;
    *) builtin test "$@" ;;
  esac
}
""")
        self.executable(self.bin / 'id', '#!/bin/sh\necho "${TEST_UID:-1000}"\n')
        result = self.run_image(IMAGE_MODE='direct', RUNNER_ENVIRONMENT='self-hosted',
                                BASH_ENV=str(hook), UV_LINK_MODE='copy')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('mise install\nmise run setup\n', self.trace())
        self.assertIn('mise run typecheck', self.trace())
        self.assertNotIn('docker ', self.trace())
        result = self.run_image(IMAGE_MODE='direct', RUNNER_ENVIRONMENT='self-hosted',
                                BASH_ENV=str(hook), UV_LINK_MODE='copy', TEST_UID='0')
        self.assertNotEqual(result.returncode, 0)

    def test_unqualified_privileged_metadata_fails_before_up(self):
        result = self.run_image(BAD_METADATA='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('cli up', self.trace())
        self.assert_clean()

    def test_hosted_mode_refuses_self_hosted_capacity(self):
        result = self.run_image(RUNNER_ENVIRONMENT='self-hosted')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('docker ', self.trace())

    def test_cancellation_removes_owned_container(self):
        process = subprocess.Popen(['bash', str(ASSETS / 'run.sh')], cwd=self.repo,
                                   env={**self.env, 'WAIT_UP': '1'}, start_new_session=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not (self.root / 'started').exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    self.fail(process.communicate())
                time.sleep(0.02)
            self.assertTrue((self.root / 'started').exists())
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=10)
            self.assertEqual(process.returncode, 143)
            self.assertIn('docker rm -f', self.trace())
            self.assert_clean()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()


class Wiring(unittest.TestCase):
    def test_unaffected_workflows_and_required_contexts(self):
        fixture = json.loads((ROOT / 'tests/fixtures/image-opt-out.json').read_text())
        for name, digest in fixture['unchanged'].items():
            self.assertEqual(hashlib.sha256((WORKFLOWS / name).read_bytes()).hexdigest(), digest)
        for name, job in [('lint-hooks.yml', 'lint-hooks'), ('typecheck-ts.yml', 'typecheck'), ('build.yml', 'bd-01')]:
            text = (WORKFLOWS / name).read_text()
            self.assertIn('\n  ' + job + ':\n', text)
            self.assertIn("vars.CI_IMAGE_REPOS == 'all'", text)
            if name == 'typecheck-ts.yml':
                self.assertNotIn('packages: read', text)
            else:
                self.assertIn('packages: read', text)
            self.assertNotIn('packages: write', text)
            self.assertNotIn('pull_request_target', text)
            for step in ['Select the declared CI Image route', 'Run project commands in the Repository Image']:
                self.assertIn('Rubio-Enterprises/.github/actions/devcontainer-run@main', extract_step(text, step))
            self.assertLess(text.index('- name: Provision standards for image execution'),
                            text.index('- name: Run project commands in the Repository Image'))

    def test_callable_typecheck_preserves_contents_only_caller_permissions(self):
        text = (WORKFLOWS / 'typecheck-ts.yml').read_text()
        self.assertIn('\n  workflow_call:\n', text)
        header = text.split('\n  typecheck:\n', 1)[1].split('\n    steps:', 1)[0]
        self.assertIn('permissions:\n      contents: read\n', header)
        self.assertNotIn('packages:', header)
        for name in ['lint-hooks.yml', 'build.yml']:
            self.assertNotIn('\n  workflow_call:', (WORKFLOWS / name).read_text())

    def test_native_step_bodies_and_cache_auth_inputs_are_unchanged(self):
        fixture = json.loads((ROOT / 'tests/fixtures/image-opt-out.json').read_text())
        for name, steps in fixture['legacy_steps'].items():
            text = (WORKFLOWS / name).read_text()
            for step, digest in steps.items():
                with self.subTest(workflow=name, step=step):
                    actual = canonical_step(extract_step(text, step))
                    self.assertEqual(hashlib.sha256(actual.encode()).hexdigest(), digest)
                    guard = step_condition(text, step)
                    route = 'route' if name == 'typecheck-ts.yml' else 'detect'
                    prefix = f"needs.{route}.outputs.image_mode == ''"
                    if guard == prefix:
                        guard = ''
                    elif guard.startswith(prefix + ' && (') and guard.endswith(')'):
                        guard = guard[len(prefix + ' && ('):-1]
                    self.assertEqual(guard, fixture['legacy_conditions'][name][step])

    def test_cli_pin_and_auth_cleanup_before_lifecycle(self):
        action = (ASSETS / 'action.yml').read_text()
        self.assertIn('@devcontainers/cli@0.89.0', action)
        run = (ASSETS / 'run.sh').read_text()
        self.assertLess(run.index('rm -rf "$state/auth"'), run.index('"$cli" up'))
        self.assertLess(run.index('bash /run/project-image/preflight.sh'), run.index('"$cli" run-user-commands'))
        direct = run.split('[ "$IMAGE_MODE" = hosted ]', 1)[0]
        self.assertNotIn('"$cli"', direct)
        self.assertIn('mise install\n  mise run setup', direct)
        self.assertNotIn('bootstrap --only', run)


def step_condition(text, name):
    lines = []
    for line in extract_step(text, name).splitlines():
        if line.strip() and not line.startswith('      '):
            break
        lines.append(line)
    return condition('\n'.join(lines), name)


def canonical_step(step):
    lines = step.splitlines()
    output = []
    skip_condition = False
    for line in lines:
        if line.strip() and not line.startswith('      '):
            break
        if line.startswith('        if:'):
            skip_condition = line.split(':', 1)[1].strip() in ('>-', '>', '|')
            continue
        if skip_condition and (not line.strip() or line.startswith('          ')):
            continue
        skip_condition = False
        if line.strip() and not line.lstrip().startswith('#'):
            output.append(line.rstrip())
    return '\n'.join(output).strip()
