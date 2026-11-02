from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / 'actions/project-command'
MOCK = r'''#!/usr/bin/env python3
import json, os, pathlib, re, shutil, subprocess, sys
args = sys.argv[1:]
name = pathlib.Path(sys.argv[0]).name
fixture = pathlib.Path(os.environ['MOCK_FIXTURE'])
trace = fixture / 'trace'
with trace.open('a') as out:
    out.write(json.dumps({'tool': name, 'args': args, 'auth': bool(os.getenv('NODE_AUTH_TOKEN'))}) + '\n')
fail = os.getenv('FAIL_AT')
if name == 'npm':
    prefix = pathlib.Path(args[args.index('--prefix') + 1])
    cli = prefix / 'node_modules/.bin/devcontainer'
    cli.parent.mkdir(parents=True)
    shutil.copyfile(__file__, cli)
    cli.chmod(0o755)
    sys.exit(0)
if name == 'docker':
    if args[:1] == ['--config']:
        auth = pathlib.Path(args[1])
        args = args[2:]
        if args[0] == 'login':
            assert sys.stdin.read() == 'package-token-fixture'
            (auth / 'config.json').write_text('package-token-fixture')
        if args[0] == 'pull' and fail == 'pull': sys.exit(2)
    elif args[:2] == ['image', 'ls']:
        print('sha256:' + 'e' * 64)
    elif args[:2] == ['volume', 'ls']:
        print('existing-volume')
    elif args[:2] == ['image', 'inspect']:
        print('sha256:' + 'a' * 64)
    elif args[:1] == ['inspect']:
        state = json.loads(pathlib.Path((fixture / 'state-path').read_text()).read_text())
        print(json.dumps([{'Image': 'sha256:' + 'c' * 64,
            'Config': {'Labels': {'rubio.project-command': state['label']}},
            'Mounts': [{'Type': 'volume', 'Name': 'owned-volume'}, {'Type': 'volume', 'Name': 'existing-volume'}]}]))
    elif args[:1] == ['ps']:
        if (fixture / 'started').exists(): print('b' * 64)
    elif args[:1] == ['rm']:
        (fixture / 'started').unlink(missing_ok=True)
    sys.exit(0)
if name == 'devcontainer':
    config_path = pathlib.Path(args[args.index('--config') + 1])
    root = config_path.parent
    assert not (root / 'auth').exists()
    config = json.loads(config_path.read_text())
    assert 'package-token-fixture' not in config_path.read_text()
    if args[0] == 'read-configuration':
        merged = {**config, 'onCreateCommands': ['/usr/local/share/rubio-baseline/claim-mise-shims']}
        if fail == 'metadata': merged['privileged'] = True
        print(json.dumps({'configuration': config, 'mergedConfiguration': merged}))
    elif args[0] == 'up':
        (fixture / 'state-path').write_text(str(root / 'state.json'))
        (fixture / 'started').touch()
        if fail == 'up':
            print(json.dumps({'outcome': 'error', 'description': 'UID build fixture diagnostic'}))
            sys.exit(3)
        print(json.dumps({'containerId': 'b' * 64}))
    elif args[0] == 'exec':
        command = args[args.index('--id-label') + 2:]
        if command == ['id', '-u']:
            print('1001')
        else:
            assert command[:6] == ['bash', '--noprofile', '--norc', '-e', '-o', 'pipefail']
            env = {'PATH': os.environ['PATH'], 'HOME': str(fixture)}
            for key, value in config['remoteEnv'].items():
                match = re.fullmatch(r'\$\{localEnv:([A-Z_][A-Z0-9_]*)\}', value)
                if match: value = os.environ.get(match[1], '')
                value = value.replace('/run/project-command', str(root))
                value = value.replace(config['workspaceFolder'], os.environ['GITHUB_WORKSPACE'])
                env[key] = value
            script = command[-1].replace('/run/project-command', str(root))
            sys.exit(subprocess.run(command[:-1] + [script], env=env, cwd=os.environ['GITHUB_WORKSPACE']).returncode)
    sys.exit(0)
raise AssertionError((name, args))
'''


class ProjectCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('docker', 'npm'):
            file = self.bin / name
            file.write_text(MOCK)
            file.chmod(0o755)
        event = self.root / 'event.json'
        event.write_text('{}')
        self.env = {
            **os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
            'MOCK_FIXTURE': str(self.root), 'RUNNER_TEMP': str(self.root),
            'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64',
            'GITHUB_WORKSPACE': str(self.repo), 'GITHUB_REPOSITORY': 'Rubio-Enterprises/fixture',
            'GITHUB_ACTOR': 'fixture', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_JOB': 'test',
            'GITHUB_EVENT_PATH': str(event), 'INPUT_OPERATION': 'prepare', 'INPUT_HOSTED-IMAGE': 'true',
            'INPUT_TOKEN': 'package-token-fixture',
        }
        for name in ('GITHUB_STATE', 'GITHUB_ENV', 'GITHUB_OUTPUT', 'GITHUB_PATH', 'GITHUB_STEP_SUMMARY'):
            file = self.root / name
            file.touch()
            self.env[name] = str(file)

    def call(self, **env):
        return subprocess.run(['node', str(ACTION / 'index.mjs')], env={**self.env, **env},
                              text=True, capture_output=True, check=False)

    def state(self):
        return next(self.root.glob('project-command-*/state.json')).parent

    def prepare(self):
        result = self.call()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.env['PROJECT_COMMAND_STATE'] = str(self.state())
        self.env['INPUT_TOKEN'] = ''
        return self.state()

    def cleanup(self, root):
        return subprocess.run(['node', str(ACTION / 'cleanup.mjs')],
                              env={**self.env, 'STATE_project_command_state': str(root)},
                              text=True, capture_output=True, check=False)

    def calls(self):
        file = self.root / 'trace'
        return [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []

    def test_hosted_reuses_one_image_and_forwards_only_invocation_auth(self):
        root = self.prepare()
        result = self.call(INPUT_OPERATION='run', INPUT_RUN='test -n "$NODE_AUTH_TOKEN"',
                           **{'INPUT_FORWARD-ENV': 'NODE_AUTH_TOKEN'}, NODE_AUTH_TOKEN='package-token-fixture')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.call(INPUT_OPERATION='run', INPUT_RUN='test -z "${NODE_AUTH_TOKEN:-}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(row['args'][0] == 'up' for row in self.calls()), 1)
        self.assertEqual(sum(row['tool'] == 'docker' and 'pull' in row['args'] for row in self.calls()), 1)
        self.assertNotIn('package-token-fixture', (root / 'devcontainer.json').read_text())
        self.assertNotIn('package-token-fixture', (root / 'state.json').read_text())
        self.assertFalse((root / 'auth').exists())
        self.assertNotIn('package-token-fixture', (self.root / 'trace').read_text())
        result = self.cleanup(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(root.exists())
        calls = self.calls()
        self.assertIn(['volume', 'rm', 'owned-volume'], [row['args'] for row in calls])
        self.assertNotIn(['volume', 'rm', 'existing-volume'], [row['args'] for row in calls])
        self.assertFalse(any(row['auth'] for row in calls if row['tool'] == 'docker'))

    def test_environment_multiline_channels_failure_and_config_reset(self):
        root = self.prepare()
        script = '''test "$PR_TITLE" = "$(printf 'first\nsecond')"
printf 'answer=42\n' >> "$GITHUB_OUTPUT"
printf 'summary\n' >> "$GITHUB_STEP_SUMMARY"
exit 7
'''
        result = self.call(INPUT_OPERATION='run', INPUT_RUN=script, **{'INPUT_FORWARD-ENV': 'PR_TITLE'},
                           PR_TITLE='first\nsecond')
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn('answer=42\n', (self.root / 'GITHUB_OUTPUT').read_text())
        self.assertEqual((self.root / 'GITHUB_STEP_SUMMARY').read_text(), 'summary\n')
        self.assertNotIn('PR_TITLE', json.loads((root / 'devcontainer.json').read_text())['remoteEnv'])
        self.assertEqual(list((root / 'commands').iterdir()), [])
        self.assertEqual(self.cleanup(root).returncode, 0)

    def test_replaced_output_path_cannot_make_the_host_read_another_file(self):
        root = self.prepare()
        result = self.call(INPUT_OPERATION='run', INPUT_RUN='rm "$GITHUB_STEP_SUMMARY"; ln -s /etc/passwd "$GITHUB_STEP_SUMMARY"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'GITHUB_STEP_SUMMARY').read_text(), '')
        self.assertEqual(self.cleanup(root).returncode, 0)

    def test_partial_prepare_and_pull_or_metadata_failures_clean_owned_state(self):
        for failure in ('pull', 'metadata', 'up'):
            with self.subTest(failure=failure):
                result = self.call(FAIL_AT=failure)
                self.assertNotEqual(result.returncode, 0)
                if failure == 'up': self.assertIn('UID build fixture diagnostic', result.stderr)
                root = self.state()
                self.assertFalse((root / 'auth').exists())
                result = self.cleanup(root)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(root.exists())
                self.assertFalse((self.root / 'started').exists())

    def test_native_runs_without_docker_and_keeps_fail_fast_shell(self):
        result = self.call(INPUT_OPERATION='run', **{'INPUT_HOSTED-IMAGE': 'false'},
                           INPUT_RUN='false\ntouch should-not-exist')
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.repo / 'should-not-exist').exists())
        self.assertEqual(self.calls(), [])
        self.assertEqual(list(self.root.glob('project-command-native-*')), [])

    def test_missing_hosted_state_never_falls_through_to_native(self):
        result = self.call(INPUT_OPERATION='run', INPUT_RUN='touch should-not-exist')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / 'should-not-exist').exists())
        self.assertEqual(self.calls(), [])

    def test_wrong_runner_and_unsafe_forwarding_fail(self):
        for env in ({'RUNNER_ENVIRONMENT': 'self-hosted'}, {'RUNNER_ARCH': 'ARM64'}, {'RUNNER_OS': 'macOS'}):
            result = self.call(**env)
            self.assertNotEqual(result.returncode, 0)
        root = self.prepare()
        for value in ('NODE_AUTH_TOKEN=value', 'GITHUB_STATE', 'HOME', 'BASH_ENV'):
            result = self.call(INPUT_OPERATION='run', INPUT_RUN='true', **{'INPUT_FORWARD-ENV': value})
            self.assertNotEqual(result.returncode, 0, value)
        self.assertEqual(self.cleanup(root).returncode, 0)

    def test_post_hook_runs_after_failure_and_needs_no_repository_credentials(self):
        action = (ACTION / 'action.yml').read_text()
        self.assertIn('post: cleanup.mjs', action)
        self.assertIn('post-if: always()', action)
        self.assertIn('using: node24', action)
        result = subprocess.run(['node', str(ACTION / 'cleanup.mjs')], env=self.env,
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main()
