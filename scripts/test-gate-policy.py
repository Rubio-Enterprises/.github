#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import sys

CONCERNS = ('test', 'typecheck', 'e2e', 'package')
POLICY = Path('.github/standards.toml')


def read_policy():
    if not POLICY.exists() and not POLICY.is_symlink():
        return None, set()
    import tomllib

    with POLICY.open('rb') as stream:
        document = tomllib.load(stream)
    for name in ('test-gate', 'app-image'):
        section = document.get(name, {})
        if not isinstance(section, dict):
            raise ValueError(f'{name} must be a table')
        if 'off' in section:
            reason(section['off'], f'{name}.off')
    gate = document.get('test-gate', {})
    excluded = set(gate) - {'off'}
    for concern in excluded:
        if concern not in CONCERNS:
            raise ValueError(f'unknown Test Gate concern: {concern}')
        section = gate[concern]
        if not isinstance(section, dict):
            raise ValueError(f'test-gate.{concern} must be a table')
        reason(section.get('off'), f'test-gate.{concern}.off')
        if 'off' in gate:
            raise ValueError('test-gate.off cannot be combined with per-concern entries')
        if not Path(f'bin/{concern}.sh').is_file():
            raise ValueError(f'test-gate.{concern} requires existing bin/{concern}.sh')
    return gate.get('off'), excluded


def reason(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a non-empty reason string')


def select(mode, excluded):
    declared = os.environ.get('MACOS_CONCERNS', '').split() if mode == 'macos' else []
    selected = declared or CONCERNS
    # Declared macOS concerns must still reach the executable-script check when absent.
    return [name for name in CONCERNS if name in selected and name not in excluded
            and (declared or Path(f'bin/{name}.sh').exists() or Path(f'bin/{name}.sh').is_symlink())]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--select', choices=('linux', 'macos'))
    args = parser.parse_args()
    try:
        whole, excluded = read_policy()
    except (OSError, ValueError) as error:
        sys.exit(f'::error::invalid {POLICY}: {error}')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        if args.select is None:
            output.write(f'opted-out={str(whole is not None).lower()}\n')
            if whole is not None:
                print('::notice::Test Gate opted out in .github/standards.toml (reason recorded in policy)')
        else:
            selected = [] if whole is not None else select(args.select, excluded)
            evidence = ''.join(f' {name}' for name in selected if name in ('test', 'e2e'))
            output.write(f'concerns={evidence}\n')
            output.write(f'artifact-concerns={"-".join(selected) or "none"}\n')
            for name in CONCERNS:
                if name in excluded:
                    print(f'::notice::Test Gate concern {name} opted out; reason recorded in {POLICY}', file=sys.stderr)
            print(' '.join(selected))


if __name__ == '__main__':
    main()
