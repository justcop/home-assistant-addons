"""Publish delivery versions from actual runtime changes across repository branches."""
import argparse
import copy
import hashlib
import json
import re
import subprocess
from pathlib import Path

import yaml

CONFIG = Path("vinyl_guardian/config.yaml")
STATE = Path(".github/vinyl-guardian-release-state.json")
SETTINGS_REPORT = Path(".github/vinyl-guardian-settings-report.json")


class Quoted(str):
    pass


class ConfigDumper(yaml.SafeDumper):
    pass


ConfigDumper.add_representer(Quoted, lambda dumper, value:
                           dumper.represent_scalar('tag:yaml.org,2002:str', value, style='"'))


def dump_config(config):
    def quote_values(value):
        if isinstance(value, dict):
            return {key: quote_values(item) for key, item in value.items()}
        if isinstance(value, list):
            return [quote_values(item) for item in value]
        return Quoted(value) if isinstance(value, str) else value
    return yaml.dump(quote_values(config), Dumper=ConfigDumper, sort_keys=False,
                     allow_unicode=True)


def flatten(schema, prefix=()):
    result = {}
    for key, value in schema.items():
        path = prefix + (key,)
        if isinstance(value, dict):
            # Keep empty groups too, to detect scalar/group conflicts.
            result[path] = {}
            result.update(flatten(value, path))
        else:
            result[path] = value
    return result


MISSING = object()


def lookup(mapping, path):
    for key in path:
        if not isinstance(mapping, dict) or key not in mapping:
            return MISSING
        mapping = mapping[key]
    return mapping


def assign(mapping, path, value):
    for key in path[:-1]:
        mapping = mapping.setdefault(key, {})
    mapping[path[-1]] = value


def compatible(left, right):
    if isinstance(left, dict) or isinstance(right, dict):
        return isinstance(left, dict) and isinstance(right, dict)
    return isinstance(left, str) and isinstance(right, str) and left.rstrip('?') == right.rstrip('?')


def merge_settings(config, branch_configs):
    """Extend the installed schema; never overwrite its existing defaults/types."""
    merged = copy.deepcopy(config)
    schemas = {branch: flatten(doc.get('schema', {}))
               for branch, doc in branch_configs.items()}
    baseline = flatten(config.get('schema', {}))
    report = {'added_options': [], 'conflicts': [], 'branch_options': {
        branch: sorted('.'.join(path) for path, spec in entries.items()
                       if not isinstance(spec, dict))
        for branch, entries in schemas.items()}}
    all_paths = sorted(set().union(*(set(entries) for entries in schemas.values())),
                       key=lambda path: (len(path), path))
    blocked = set()
    for path in all_paths:
        if path == ('code_branch',) or any(path[:len(parent)] == parent for parent in blocked):
            continue
        definitions = [(branch, entries[path]) for branch, entries in schemas.items() if path in entries]
        existing = baseline.get(path, MISSING)
        chosen = existing if existing is not MISSING else definitions[0][1]
        conflicting = [branch for branch, spec in definitions if not compatible(chosen, spec)]
        if conflicting:
            report['conflicts'].append({'option': '.'.join(path), 'reason': 'schema differs',
                                        'branches': conflicting,
                                        'resolution': 'keep main' if existing is not MISSING else 'not imported'})
            if existing is MISSING or isinstance(chosen, dict) or any(
                    isinstance(spec, dict) for branch, spec in definitions if branch in conflicting):
                blocked.add(path)
            if existing is MISSING:
                continue
        if existing is not MISSING or isinstance(chosen, dict):
            continue
        defaults = [(branch, lookup(branch_configs[branch].get('options', {}), path))
                    for branch, _ in definitions]
        supplied = [(branch, value) for branch, value in defaults if value is not MISSING]
        same_default = supplied and supplied[0][1] is not None and all(type(value) is type(supplied[0][1]) and value == supplied[0][1]
                                        for _, value in supplied)
        if same_default:
            assign(merged.setdefault('options', {}), path, copy.deepcopy(supplied[0][1]))
            assign(merged['schema'], path, chosen)
        else:
            # Branch-only fields cannot become mandatory for everyone.
            assign(merged['schema'], path, chosen.rstrip('?') + '?')
            if supplied:
                report['conflicts'].append({'option': '.'.join(path), 'reason': 'defaults differ',
                                            'branches': [branch for branch, _ in supplied],
                                            'resolution': 'optional field; no shared default'})
        report['added_options'].append('.'.join(path))
    return merged, report


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def runtime_file(path):
    if path.startswith("vinyl_guardian/"):
        name = path.removeprefix("vinyl_guardian/")
        return "/" not in name and (
            name.endswith((".py", ".sh"))
            or name in {"Dockerfile", "config.yaml", "build.yaml", "requirements.txt"}
        )
    return path in {
        ".github/scripts/vinyl_guardian_release.py",
        ".github/workflows/vinyl-guardian-release.yml",
    }


def fingerprint(ref, config_override=None):
    digest = hashlib.sha256()
    for path in sorted(git("ls-tree", "-r", "--name-only", ref).splitlines()):
        if not runtime_file(path):
            continue
        content = git("show", f"{ref}:{path}")
        if path == str(CONFIG):
            document = yaml.safe_load(config_override if config_override is not None else content)
            document.pop('version', None)
            document.get('schema', {}).pop('code_branch', None)
            content = json.dumps(document, sort_keys=True, separators=(',', ':'))
        digest.update((path + "\0" + content + "\0").encode())
    return digest.hexdigest()


def bump_kind(messages, requested="auto"):
    if requested != "auto":
        return requested
    if any(re.search(r"(^[\w-]+(?:\([^\n]*\))?!:|^BREAKING[ -]CHANGE:)",
                     message, re.M) for message in messages):
        return "major"
    if any(re.match(r"feat(?:\([^\n]*\))?:", message) for message in messages):
        return "minor"
    return "patch"


def bump_version(version, kind):
    values = [int(n) for n in version.split(".")]
    if len(values) not in (2, 3):
        raise ValueError(f"Unsupported version: {version}")
    values += [0] * (3 - len(values))
    index = {"major": 0, "minor": 1, "patch": 2}[kind]
    values[index] += 1
    values[index + 1:] = [0] * (2 - index)
    return ".".join(map(str, values))


def collect():
    result = {}
    for branch in git("for-each-ref", "--format=%(refname:strip=3)",
                      "refs/remotes/origin").splitlines():
        if branch == "HEAD":
            continue
        ref = f"refs/remotes/origin/{branch}"
        # List only branches containing the add-on, not unrelated future branches.
        if not git("ls-tree", ref, "vinyl_guardian/config.yaml"):
            continue
        if any(char in branch for char in '|()"\\'):
            raise ValueError(f"Branch cannot be represented in HA list schema: {branch}")
        result[branch] = {"sha": git("rev-parse", ref), "fingerprint": fingerprint(ref)}
    return result


def load_branch_configs(current):
    return {branch: yaml.safe_load(git('show', f"origin/{branch}:{CONFIG}"))
            for branch in current}


def update(requested="auto", seed=False):
    current = collect()
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    changed = [branch for branch, item in current.items()
               if item["fingerprint"] != previous.get(branch, {}).get("fingerprint")]
    removed = set(previous) - set(current)
    original_config = CONFIG.read_text()
    installed = yaml.safe_load(original_config)
    branch_configs = load_branch_configs(current)
    document, report = merge_settings(installed, branch_configs)
    branches = ["main"] + sorted(set(current) - {"main"})
    document['schema']['code_branch'] = 'list(' + '|'.join(branches) + ')'
    config_changed = document != installed
    messages = []
    for branch in changed:
        old = previous.get(branch, {}).get("sha")
        if not old:
            continue
        # Force-pushed or removed history must not prevent a delivery update.
        exists = subprocess.run(["git", "cat-file", "-e", old],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if exists.returncode == 0:
            commits = git("rev-list", f"{old}..{current[branch]['sha']}").splitlines()
            for commit in commits:
                paths = git("diff-tree", "--root", "--no-commit-id", "--name-only",
                            "-r", commit).splitlines()
                if any(runtime_file(path) for path in paths):
                    messages.append(git("show", "-s", "--format=%B", commit))
    if not seed and (changed or removed or config_changed
                     or requested != "auto"):
        old_version = document['version']
        version = bump_version(old_version, bump_kind(messages, requested))
        document['version'] = version
        print(f"Publish {version}; changed branches: {', '.join(changed) or 'catalog only'}")
    CONFIG.write_text(dump_config(document) if document != installed else original_config)
    # Store the resulting main fingerprint, so our generated schema is not
    # mistaken for another developer change on the next scheduled run.
    current['main']['fingerprint'] = fingerprint('origin/main', config_override=CONFIG.read_text())
    if current['main']['fingerprint'] != previous.get('main', {}).get('fingerprint') and 'main' not in changed:
        changed.append('main')
    SETTINGS_REPORT.parent.mkdir(parents=True, exist_ok=True)
    # Added paths are historical provenance, not a one-run-only list.
    old_report = json.loads(SETTINGS_REPORT.read_text()) if SETTINGS_REPORT.exists() else {}
    report['added_options'] = sorted(set(report['added_options']) | set(old_report.get('added_options', [])))
    SETTINGS_REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    for conflict in report['conflicts']:
        print(f"::warning::Setting {conflict['option']}: {conflict['reason']}; {conflict['resolution']}")
    # Avoid commits just because a branch received a documentation/bot commit.
    state = {branch: previous.get(branch, item)
             if branch not in changed else item for branch, item in current.items()}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bump", choices=["auto", "patch", "minor", "major"], default="auto")
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    update(args.bump, args.seed)
