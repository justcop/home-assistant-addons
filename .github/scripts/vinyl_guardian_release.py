"""Advertise updates only for changes to the installed main runtime."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
import yaml

CONFIG = Path('vinyl_guardian/config.yaml')
STATE = Path('.github/vinyl-guardian-release-state.json')


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def runtime_file(path):
    if path.startswith('vinyl_guardian/'):
        name = path.removeprefix('vinyl_guardian/')
        return (name.startswith('translations/') and name.endswith('.yaml')) or (
            '/' not in name and (name.endswith(('.py', '.sh')) or name in
            {'Dockerfile', 'config.yaml', 'build.yaml', 'requirements.txt'}))
    return path in {'.github/scripts/vinyl_guardian_release.py',
                    '.github/workflows/vinyl-guardian-release.yml'}


def fingerprint(ref='HEAD'):
    digest = hashlib.sha256()
    for path in sorted(git('ls-tree', '-r', '--name-only', ref).splitlines()):
        if not runtime_file(path):
            continue
        content = git('show', f'{ref}:{path}')
        if path == str(CONFIG):
            config = yaml.safe_load(content)
            config.pop('version', None)
            content = json.dumps(config, sort_keys=True)
        digest.update(path.encode() + b'\0' + content.encode() + b'\0')
    return digest.hexdigest()


def bump_kind(messages, requested='auto'):
    if requested != 'auto':
        return requested
    if any(re.search(r'(^[\w-]+(?:\([^\n]*\))?!:|^BREAKING[ -]CHANGE:)', message, re.M) for message in messages):
        return 'major'
    if any(re.match(r'feat(?:\([^\n]*\))?:', message) for message in messages):
        return 'minor'
    return 'patch'


def bump_version(version, kind):
    values = [int(n) for n in version.split('.')]
    if len(values) not in (2, 3):
        raise ValueError(f'Unsupported version: {version}')
    values += [0] * (3 - len(values))
    index = {'major': 0, 'minor': 1, 'patch': 2}[kind]
    values[index] += 1
    values[index + 1:] = [0] * (2 - index)
    return '.'.join(map(str, values))


def update(requested='auto', seed=False):
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    current = {'sha': git('rev-parse', 'HEAD'), 'fingerprint': fingerprint()}
    if previous.get('fingerprint') == current['fingerprint']:
        print('No main runtime changes to publish.')
        return
    old = previous.get('sha')
    if old:
        commits = git('rev-list', '--first-parent', f'{old}..HEAD').splitlines()
        messages = [git('show', '-s', '--format=%B', commit) for commit in commits
                    if any(runtime_file(p) for p in git('diff-tree', '--no-commit-id', '--name-only', '-r', '-m', commit).splitlines())]
    else:
        # Migrating the former branch catalog state: only the promotion commit
        # determines this release. There is no branch/config reconciliation.
        messages = [git('show', '-s', '--format=%B', 'HEAD')]
    if not seed:
        config = yaml.safe_load(CONFIG.read_text())
        version = bump_version(str(config['version']), bump_kind(messages, requested))
        content = CONFIG.read_text()
        content = re.sub(r'^version:.*$', f'version: "{version}"', content, count=1, flags=re.M)
        CONFIG.write_text(content)
        print(f'Publish main version {version}')
    STATE.write_text(json.dumps(current, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bump', choices=['auto', 'patch', 'minor', 'major'], default='auto')
    parser.add_argument('--seed', action='store_true')
    args = parser.parse_args()
    update(args.bump, args.seed)
