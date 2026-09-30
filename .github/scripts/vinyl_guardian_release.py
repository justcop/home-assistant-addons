"""Publish delivery versions from actual runtime changes across repository branches."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

CONFIG = Path("vinyl_guardian/config.yaml")
STATE = Path(".github/vinyl-guardian-release-state.json")


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


def fingerprint(ref):
    digest = hashlib.sha256()
    for path in sorted(git("ls-tree", "-r", "--name-only", ref).splitlines()):
        if not runtime_file(path):
            continue
        content = git("show", f"{ref}:{path}")
        if path == str(CONFIG):
            content = re.sub(r'^version:.*$', '', content, flags=re.M)
            # Only the schema enum is generated. The default branch is runtime config.
            content = re.sub(r'^  code_branch: "list\([^\n]*\)"$',
                             '  code_branch: "str"', content, flags=re.M)
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


def update(requested="auto", seed=False):
    current = collect()
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    changed = [branch for branch, item in current.items()
               if item["fingerprint"] != previous.get(branch, {}).get("fingerprint")]
    removed = set(previous) - set(current)
    config = CONFIG.read_text()
    branches = ["main"] + sorted(set(current) - {"main"})
    config, count = re.subn(r'^  code_branch: "(?:str|list\([^\n]*\))"$',
                           '  code_branch: "list(' + '|'.join(branches) + ')"',
                           config, flags=re.M)
    if count != 1:
        raise ValueError("Expected exactly one code_branch schema")
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
    if not seed and (changed or removed or config != CONFIG.read_text()
                     or requested != "auto"):
        old_version = re.search(r'^version: "([\d.]+)"$', config, re.M).group(1)
        version = bump_version(old_version, bump_kind(messages, requested))
        config = re.sub(r'^version:.*$', f'version: "{version}"', config, flags=re.M)
        print(f"Publish {version}; changed branches: {', '.join(changed) or 'catalog only'}")
    CONFIG.write_text(config)
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
