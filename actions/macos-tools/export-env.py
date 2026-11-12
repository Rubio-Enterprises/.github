import json
import os
from pathlib import Path
import re
import sys

ISOLATION = (
    'MISE_DATA_DIR', 'MISE_CACHE_DIR', 'MISE_STATE_DIR', 'MISE_CONFIG_DIR',
    'MISE_SYSTEM_CONFIG_DIR', 'MISE_SHIMS_DIR', 'MISE_GLOBAL_CONFIG_FILE',
    'MISE_TRUSTED_CONFIG_PATHS', 'MISE_YES', 'MISE_AUTO_INSTALL',
    'MISE_OVERRIDE_CONFIG_FILENAMES',
)


def export(filename):
    values = json.loads(Path(filename).read_text())
    data = Path(os.environ['MISE_DATA_DIR'])
    paths = [path for path in values.pop('PATH', '').split(os.pathsep)
             if Path(path).is_relative_to(data)]
    values.update({name: os.environ[name] for name in ISOLATION})
    # Tokens are step-scoped inputs, not job-wide exported tool environment.
    for name in ('GITHUB_TOKEN', 'MISE_GITHUB_TOKEN', 'GITHUB_API_TOKEN', 'NODE_AUTH_TOKEN'):
        values.pop(name, None)
    for name, value in values.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) or not isinstance(value, str):
            raise ValueError('invalid mise environment entry')
    if any('\n' in value or '\r' in value for value in [*values.values(), *paths]):
        raise ValueError('multiline mise environment is not supported')
    with open(os.environ['GITHUB_ENV'], 'a') as stream:
        stream.writelines(f'{name}={value}\n' for name, value in values.items())
    # The runner prepends each entry; reverse to retain mise's PATH precedence.
    with open(os.environ['GITHUB_PATH'], 'a') as stream:
        stream.writelines(f'{path}\n' for path in reversed(paths))


if __name__ == '__main__':
    export(sys.argv[1])
