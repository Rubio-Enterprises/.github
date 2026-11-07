import json
from pathlib import Path
import subprocess


def test_probe_is_scoped_and_read_only():
    root = Path(__file__).resolve().parents[1]
    workflow = json.loads(subprocess.check_output(
        ['yq', '-o=json', str(root / '.github/workflows/test-gate.yml')], text=True))
    probe = next(step for step in workflow['jobs']['test-gate']['steps']
                 if step.get('name') == 'S53 injected secret and package-read probe')
    assert probe['if'] == (
        "github.repository == 'Rubio-Enterprises/org-ops' && "
        "github.event_name == 'pull_request' && github.head_ref == 'chore/s53-canaries'"
    )
    assert probe['env']['CANARY_PROBE'] == '${{ secrets.CANARY_PROBE }}'
    assert workflow['permissions'] == {'contents': 'read', 'packages': 'read'}
    assert 'toJSON(secrets)' not in probe['run']
    assert "os.environ.get('CANARY_PROBE')" in probe['run']
    assert "'blobs/' + manifest['config']['digest']" in probe['run']
