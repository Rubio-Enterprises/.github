import copy
import json
from pathlib import Path
import subprocess

import pytest

from test_project_route import ROOT, RUNNERS, run_route

FIXTURES = ROOT / 'tests/fixtures/project-route'
IMAGE_PR = json.loads((FIXTURES / 'image-pr.json').read_text())
SOURCE_PR = json.loads((FIXTURES / 'source-pr.json').read_text())


def route_event(record=IMAGE_PR, *, route='linux', event_name='pull_request', files=None,
                properties=None, inspect_image_inputs=True, **kwargs):
    event = copy.deepcopy(record['event'])
    comparison = copy.deepcopy(record['comparison'])
    if files is not None:
        comparison['files'] = files
    if event_name == 'merge_group':
        pr = event.pop('pull_request')
        event['merge_group'] = {'base_sha': pr['base']['sha'], 'head_sha': pr['head']['sha']}
    repository = {**event['repository'], 'custom_properties': {
        'ci-platform': 'linux', 'ci-size': 'small', 'ci-test-route': None,
        'ci-test-macos': None, **(properties or {}),
    }, **kwargs.pop('repository', {})}
    if inspect_image_inputs is not None:
        kwargs['inspect_image_inputs'] = inspect_image_inputs
    return run_route(route=route, event_name=event_name, event=event,
                     comparison=kwargs.pop('comparison', comparison),
                     name=record['repo']['repo'], repository=repository,
                     admitted=kwargs.pop('admitted', [repository['id']]), **kwargs)


def assert_selection(result, runner, image=False, inputs=None):
    code, data = result
    assert code == 0, data
    assert json.loads(data['output']['runner']) == runner
    assert data['output']['ci-image'] == str(image).lower()
    if inputs is not None:
        assert data['output']['image-inputs'] == inputs
    return data


@pytest.mark.parametrize('route', ['linux', 'test'])
@pytest.mark.parametrize('record', [IMAGE_PR, SOURCE_PR], ids=['recorded-image', 'recorded-source'])
def test_recorded_pull_request_comparisons(route, record):
    image = record is SOURCE_PR
    repo_id = record['event']['repository']['id']
    expected = [f"ci-{repo_id}-{record['repo']['repo']}-small"] if image else RUNNERS['docker']
    result = assert_selection(route_event(record, route=route), expected, image,
                              'unchanged' if image else 'changed')
    pr = record['event']['pull_request']
    assert result['calls'] == [{
        'path': 'GET /repos/{owner}/{repo}/compare/{basehead}',
        'args': {**record['repo'], 'basehead': f"{pr['base']['sha']}...{pr['head']['sha']}"},
    }]


@pytest.mark.parametrize('route', ['linux', 'test'])
@pytest.mark.parametrize('event_name', ['pull_request', 'merge_group'])
@pytest.mark.parametrize('inspect_image_inputs', [None, False], ids=['omitted', 'disabled'])
@pytest.mark.parametrize('compare_error', [False, True])
def test_other_linux_and_test_callers_keep_the_image_without_opt_in(
        route, event_name, inspect_image_inputs, compare_error):
    repo_id = IMAGE_PR['event']['repository']['id']
    data = assert_selection(route_event(route=route, event_name=event_name,
                                       inspect_image_inputs=inspect_image_inputs,
                                       compare_error=compare_error),
                            [f'ci-{repo_id}-aw-server-rust-small'], True, 'not-checked')
    assert data['calls'] == []


@pytest.mark.parametrize('path', [
    'Dockerfile', 'image/mise.toml', 'image/mise.lock', 'image/.mise/locks/python.lock',
    '.devcontainer/Dockerfile', 'containers/dev.Dockerfile', 'docker/Dockerfile.dev',
    'docs/Dockerfile.md',
    'compose.yaml', 'compose.yml', 'docker-compose.yaml', 'docker-compose.yml',
    '.dockerignore', 'containers/dev.Dockerfile.dockerignore',
])
@pytest.mark.parametrize('status', ['added', 'modified', 'removed'])
def test_image_paths_include_deleted_inputs_and_compose_build_configuration(path, status):
    assert_selection(route_event(files=[{'filename': path, 'status': status}]),
                     RUNNERS['docker'], inputs='changed')


@pytest.mark.parametrize('path', [
    'docs/Dockerfile-guide.md', 'src/image/mise.lock', 'images/mise.toml', 'image.md',
    'src/main.py', 'bin/test.sh', 'uv.lock', 'docs/compose.yaml.md',
])
def test_unrelated_paths_keep_the_ci_image(path):
    record = SOURCE_PR
    repo_id = record['event']['repository']['id']
    assert_selection(route_event(record, files=[{'filename': path, 'status': 'modified'}]),
                     [f'ci-{repo_id}-org-ops-small'], True, 'unchanged')


@pytest.mark.parametrize('old,new', [('image/mise.lock', 'old/mise.lock'),
                                     ('old/mise.lock', 'image/mise.lock'),
                                     ('Dockerfile', 'archive/build.txt')])
def test_renames_check_both_names(old, new):
    assert_selection(route_event(files=[{'filename': new, 'previous_filename': old,
                                        'status': 'renamed'}]), RUNNERS['docker'], inputs='changed')


@pytest.mark.parametrize('comparison', [
    None, {}, {'files': []}, {'files': 'not a list'},
    {'files': [{'filename': 'README.md'}]},
    {'files': [{'filename': None, 'status': 'modified'}]},
    {'files': [{'filename': 'README.md', 'status': 'renamed'}]},
    {'files': [{'filename': 'README.md', 'status': 'unexpected'}]},
    {'files': [{'filename': f'src/{i}', 'status': 'modified'} for i in range(300)]},
])
@pytest.mark.parametrize('route', ['linux', 'test'])
def test_incomplete_or_capped_comparisons_choose_docker(comparison, route):
    assert_selection(route_event(route=route, comparison=comparison),
                     RUNNERS['docker'], inputs='unknown')


@pytest.mark.parametrize('count', [299, 300, 301])
@pytest.mark.parametrize('event_name', ['pull_request', 'merge_group'])
def test_file_cap_never_treats_a_large_comparison_as_complete(count, event_name):
    result = route_event(SOURCE_PR, event_name=event_name,
                         files=[{'filename': f'src/{i}', 'status': 'modified'} for i in range(count)])
    repo_id = SOURCE_PR['event']['repository']['id']
    assert_selection(result, [f'ci-{repo_id}-org-ops-small'] if count < 300 else RUNNERS['docker'],
                     count < 300, 'unchanged' if count < 300 else 'unknown')


def test_commit_pagination_does_not_limit_the_first_page_file_list():
    comparison = {**SOURCE_PR['comparison'], 'total_commits': 1000}
    repo_id = SOURCE_PR['event']['repository']['id']
    assert_selection(route_event(SOURCE_PR, comparison=comparison),
                     [f'ci-{repo_id}-org-ops-small'], True, 'unchanged')


@pytest.mark.parametrize('labels', [[], [''], 'ubuntu-latest', [42]])
def test_invalid_docker_labels_fail_before_outputs(labels):
    code, result = route_event(runners={**RUNNERS, 'docker': labels})
    assert code == 1
    assert result['output'] == {}


@pytest.mark.parametrize('event_name', ['pull_request', 'merge_group'])
@pytest.mark.parametrize('route', ['linux', 'test'])
def test_lookup_failure_chooses_docker_not_the_stale_image(route, event_name):
    assert_selection(route_event(route=route, event_name=event_name, compare_error=True),
                     RUNNERS['docker'], inputs='unknown')


@pytest.mark.parametrize('pr', [None, {}, {'base': {'sha': 'main'}, 'head': {'sha': 'x'}}])
def test_missing_or_ambiguous_event_shas_choose_docker_without_a_request(pr):
    code, data = run_route(route='linux', inspect_image_inputs=True,
                           event_name='pull_request', event={'pull_request': pr})
    assert_selection((code, data), RUNNERS['docker'], inputs='unknown')
    assert data['calls'] == []


@pytest.mark.parametrize('route', ['linux', 'test'])
@pytest.mark.parametrize('record', [IMAGE_PR, SOURCE_PR], ids=['image-in-group', 'source-only-group'])
def test_merge_group_compares_the_whole_base_to_head_not_just_one_pr(route, record):
    image = record is SOURCE_PR
    repo_id = record['event']['repository']['id']
    expected = [f'ci-{repo_id}-org-ops-small'] if image else RUNNERS['docker']
    result = assert_selection(route_event(record, route=route, event_name='merge_group'), expected, image)
    pr = record['event']['pull_request']
    assert result['calls'][0]['args']['basehead'] == f"{pr['base']['sha']}...{pr['head']['sha']}"


@pytest.mark.parametrize('route', ['linux', 'test'])
@pytest.mark.parametrize('size', ['small', 'medium'])
def test_image_classes_use_the_owner_docker_route_and_hosted_fallback(route, size):
    assert_selection(route_event(route=route, properties={'ci-size': size},
                                 runners={**RUNNERS, 'docker': ['ubuntu-latest']}),
                     ['ubuntu-latest'], inputs='changed')
    assert_selection(route_event(route=route, properties={'ci-size': size},
                                 runners={size: RUNNERS[size]}), ['ubuntu-latest'], inputs='changed')


@pytest.mark.parametrize('override,expected', [('small', RUNNERS['docker']),
                                             ('medium', RUNNERS['docker']),
                                             ('docker', RUNNERS['docker']),
                                             ('arm', RUNNERS['arm']), ('macos', RUNNERS['macos'])])
def test_test_overrides_keep_arm_and_macos_and_rebuild_on_existing_compose_routes(override, expected):
    result = assert_selection(route_event(route='test', properties={
        'ci-test-route': override, 'ci-test-macos': None,
    }), expected)
    assert len(result['calls']) == (1 if override in ('small', 'medium') else 0)
    assert result['output']['platform'] == ('macos' if override == 'macos' else 'linux')


@pytest.mark.parametrize('route', ['linux', 'test'])
def test_declared_macos_shape_is_unchanged(route):
    result = route_event(route=route, properties={'ci-platform': 'macos'})
    if route == 'linux':
        repo_id = IMAGE_PR['event']['repository']['id']
        data = assert_selection(result, [f'ci-{repo_id}-aw-server-rust-small'], True, 'not-checked')
    else:
        data = assert_selection(result, RUNNERS['macos'], inputs='not-checked')
    assert data['calls'] == []


@pytest.mark.parametrize('route,expected', [('auto', None), ('small', None), ('medium', None),
                                          ('docker', RUNNERS['docker']), ('kvm', RUNNERS['kvm']),
                                          ('arm', RUNNERS['arm']), ('macos', RUNNERS['macos'])])
def test_other_callers_keep_existing_routing_without_a_file_lookup(route, expected):
    repo_id = IMAGE_PR['event']['repository']['id']
    expected = expected or [f"ci-{repo_id}-aw-server-rust-{'medium' if route == 'medium' else 'small'}"]
    data = assert_selection(route_event(route=route), expected, route in ('auto', 'small', 'medium'), 'not-checked')
    assert data['calls'] == []


@pytest.mark.parametrize('kwargs,expected', [
    ({'repository': {'private': False}}, ['ubuntu-latest']),
    ({'admitted': []}, RUNNERS['docker']),
    ({'runners': {}}, ['ubuntu-latest']),
    ({'runners': {'small': ['ubuntu-latest']}}, ['ubuntu-latest']),
    ({'runners': {'small': ['self-hosted', 'custom']}}, ['self-hosted', 'custom']),
])
@pytest.mark.parametrize('route', ['linux', 'test'])
def test_public_unadmitted_hosted_and_custom_routes_are_unchanged(kwargs, expected, route):
    data = assert_selection(route_event(route=route, **kwargs), expected, inputs='not-checked')
    assert data['calls'] == []


def test_mac_concerns_survive_the_linux_image_change():
    data = assert_selection(route_event(route='test', properties={'ci-test-macos': ['e2e', 'test']}),
                            RUNNERS['docker'])
    assert data['output']['macos-concerns'] == 'test e2e'
    assert json.loads(data['output']['macos-runner']) == RUNNERS['macos']


def test_both_injected_workflows_use_the_router_result_for_all_image_branches():
    for name, job in [('standards', 'standards-linux'), ('test-gate', 'test-gate-linux')]:
        workflow = json.loads(subprocess.check_output(
            ['yq', '-o=json', str(ROOT / f'.github/workflows/{name}.yml')], text=True))
        jobs = workflow['jobs']
        assert jobs['route']['uses'] == 'Rubio-Enterprises/.github/.github/workflows/project-route.yml@main'
        assert jobs['route']['permissions'] == {'contents': 'read'}
        assert 'fromJSON(needs.route.outputs.runner)' in jobs[job]['runs-on']
        execution = json.dumps(jobs[job])
        assert 'needs.route.outputs.ci-image' in execution
        assert 'just build' in execution
        assert '/compare/' not in json.dumps(workflow)
        assert jobs['route']['with']['inspect-image-inputs'] is True
        assert 'image-inputs' not in execution


def test_image_inspection_input_defaults_to_false_and_only_injected_callers_enable_it():
    workflow = json.loads(subprocess.check_output(
        ['yq', '-o=json', str(ROOT / '.github/workflows/project-route.yml')], text=True))
    assert workflow['on']['workflow_call']['inputs']['inspect-image-inputs'] == {
        'description': 'Inspect image inputs for injected Standards Check and Test Gate calls',
        'type': 'boolean', 'default': False,
    }
    env = workflow['jobs']['route']['steps'][0]['env']
    assert env['INSPECT_IMAGE_INPUTS'] == '${{ inputs.inspect-image-inputs }}'
    callers = set()
    for path in (ROOT / '.github/workflows').glob('*.yml'):
        caller = json.loads(subprocess.check_output(['yq', '-o=json', str(path)], text=True))
        for job in caller.get('jobs', {}).values():
            if 'project-route.yml@' in job.get('uses', ''):
                if job.get('with', {}).get('inspect-image-inputs'):
                    callers.add(path.name)
    assert callers == {'standards.yml', 'test-gate.yml'}
