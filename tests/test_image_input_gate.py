"""The CI image is rebuilt on a push only when that push changed an image input."""

import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/repository-image.yml'
SHA = 'a' * 40
OTHER = 'b' * 40
ZEROS = '0' * 40


def workflow():
    return json.loads(subprocess.check_output(['yq', '-o=json', str(WORKFLOW)]))


def decide(*, before=SHA, after=OTHER, files=(), status='ahead', raises=False, sha=OTHER):
    """Run the gate's own script against a stubbed compare API."""
    script = workflow()['jobs']['inputs']['steps'][0]['with']['script']
    harness = '''
const payload = JSON.parse(process.argv[1]);
const core = { info: () => {}, notice: () => {} };
const context = {
  payload: { before: payload.before, after: payload.after },
  sha: payload.sha,
  repo: { owner: 'Rubio-Enterprises', repo: 'example' },
};
const github = { request: async () => {
  if (payload.raises) throw new Error('compare unavailable');
  return { data: { status: payload.status, files: payload.files } };
} };
(async () => {
  const result = await (async () => { %s })();
  process.stdout.write(String(result));
})();
''' % script
    payload = {'before': before, 'after': after, 'sha': sha, 'status': status,
               'files': [{'filename': name} for name in files], 'raises': raises}
    return subprocess.run(['node', '-e', harness, json.dumps(payload)],
                          capture_output=True, text=True, check=True).stdout


@pytest.mark.parametrize('path', [
    'Dockerfile', 'image/mise.toml', 'compose.yaml', 'compose.yml',
    'docker-compose.yaml', 'ci.Dockerfile', 'Dockerfile.ci', '.dockerignore',
    'sub/.dockerignore',
])
def test_an_image_input_builds(path):
    assert decide(files=['README.md', path]) == 'true'


@pytest.mark.parametrize('path', [
    'README.md', 'src/main.py', 'docs/compose.md', 'tests/test_image.py',
    'justfile', 'package.json',
])
def test_an_ordinary_file_does_not_build(path):
    assert decide(files=[path]) == 'false'


def test_no_file_at_all_does_not_build():
    assert decide(files=[]) == 'false'


@pytest.mark.parametrize('before', [ZEROS, '', 'not-a-sha', None])
def test_an_uncomparable_range_builds(before):
    assert decide(before=before, files=['README.md']) == 'true'


def test_a_failed_comparison_builds():
    assert decide(raises=True, files=['README.md']) == 'true'


@pytest.mark.parametrize('status', ['diverged', 'behind', None])
def test_an_unusable_comparison_builds(status):
    assert decide(status=status, files=['README.md']) == 'true'


def test_a_truncated_comparison_builds():
    """GitHub caps compare at 300 files, so a longer push cannot be judged."""
    assert decide(files=[f'file{n}.txt' for n in range(300)]) == 'true'


def test_a_rename_into_an_image_path_builds():
    script = workflow()['jobs']['inputs']['steps'][0]['with']['script']
    assert 'previous_filename' in script


def test_the_gate_runs_only_for_a_push_on_a_small_runner():
    job = workflow()['jobs']['inputs']
    assert "github.event_name == 'push'" in job['if']
    assert job['permissions'] == {'contents': 'read'}
    assert job['timeout-minutes'] == 5
    assert 'small' in job['runs-on']


@pytest.mark.parametrize('event,changed,expected', [
    ('push', 'true', True),
    ('push', 'false', False),
    ('push', '', True),          # the gate job itself failed or was skipped
    ('schedule', '', True),      # the weekly rebuild never consults the gate
    ('workflow_dispatch', '', True),
])
def test_the_build_honours_the_gate_only_on_a_push(event, changed, expected):
    expression = workflow()['jobs']['build']['if'].replace(
        'inputs.image-name', "inputs['image-name']")
    data = {
        'github': {'event_name': event, 'ref': 'refs/heads/main',
                   'event': {'repository': {'default_branch': 'main', 'private': True}}},
        'needs': {'inputs': {'outputs': {'changed': changed}}},
        'inputs': {'image-name': ''},
    }
    code = '''const {github, needs, inputs} = JSON.parse(process.argv[1]);
const contains = (a, b) => a.includes(b), fromJSON = JSON.parse;
const format = (s, v) => s.replace('{0}', v);
const cancelled = () => false;
process.stdout.write(String(Boolean(''' + expression + ''')));'''
    result = subprocess.run(['node', '-e', code, json.dumps(data)],
                            capture_output=True, text=True, check=True)
    assert result.stdout == str(expected).lower()


def test_a_skipped_build_publishes_nothing_to_verify():
    """verify requires a successful build, so a skipped build skips verification too."""
    assert "needs.build.result == 'success'" in workflow()['jobs']['verify']['if']
