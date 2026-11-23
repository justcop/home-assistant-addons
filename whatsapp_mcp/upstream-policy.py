#!/usr/bin/env python3
"""Read-only upstream comparison and strict WhatsApp change-risk classifier.

Never selects a floating upstream branch for an installed build.
Only an explicit immutable commit SHA is written by pin().
"""
import argparse
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

UPSTREAM = "spare-cycles/whatsapp-mcp"
DOCKERFILE = Path(__file__).resolve().parent / "Dockerfile"
PIN = re.compile(r"(?m)^ARG UPSTREAM_COMMIT=([0-9a-f]{40})$")
DOCS = re.compile(r"(^|/)(README|CHANGELOG|CONTRIBUTING|LICENSE|NOTICE)(\.[^/]+)?$|\.md$", re.I)
COMPAT_CRITICAL = re.compile(
    r"(^|/)(pnpm-lock\.yaml|package\.json|Dockerfile|.*sqlite.*|.*migration.*|.*schema.*|"
    r".*ingest.*|.*message.*|.*privacy.*|.*auth.*|.*security.*|.*baileys.*)$", re.I
)


def pinned(source):
    hits = PIN.findall(source)
    if len(hits) != 1:
        raise ValueError("Expected exactly one immutable UPSTREAM_COMMIT pin")
    return hits[0]


def classify(files, status, commits, truncated=False):
    """Default-deny: only files with no runtime effect can be low risk."""
    if status != "ahead" or commits <= 0 or commits > 100 or truncated:
        return "high", "History divergence, incomplete comparison or too many commits"
    if not files or len(files) >= 300:
        return "high", "Changed-file list absent or possibly truncated"
    names = [f.get("filename", "") for f in files]
    if any(f.get("status") != "modified" for f in files):
        return "high", "File additions, removals or renames require review"
    if all(DOCS.search(name) and not COMPAT_CRITICAL.search(name) for name in names):
        return "low", "Documentation-only upstream changes, no application or dependencies changed"
    return "high", "Upstream runtime, dependency, protocol or privacy changes need review"


def api_json(path):
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "whatsapp-upstream-change-gate"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request("https://api.github.com" + path, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def scan():
    old = pinned(DOCKERFILE.read_text())
    latest = api_json("/repos/" + UPSTREAM + "/commits/main")["sha"]
    if not re.fullmatch("[0-9a-f]{40}", latest):
        raise ValueError("Upstream API supplied invalid commit SHA")
    report = {"upstream": UPSTREAM, "old": old, "latest": latest,
              "changed": latest != old, "risk": "none", "reason": "Already current",
              "changed_files": [], "commits": 0}
    if latest != old:
        comparison = api_json("/repos/" + UPSTREAM + "/compare/" + old + "..." +
                              latest + "?per_page=100")
        files = comparison.get("files") or []
        commits = comparison.get("total_commits", 0)
        risk, reason = classify(files, comparison.get("status"), commits,
                                len(comparison.get("commits") or []) < commits)
        report.update(risk=risk, reason=reason, commits=commits,
                      changed_files=[f["filename"] for f in files][:300],
                      compare_url="https://github.com/" + UPSTREAM + "/compare/" +
                                  old + "..." + latest)
    return report


def pin(newsha):
    if not re.fullmatch("[0-9a-f]{40}", newsha):
        raise ValueError("Expected full immutable commit SHA")
    original = DOCKERFILE.read_text()
    current = pinned(original)
    if newsha == current:
        return
    DOCKERFILE.write_text(original.replace("ARG UPSTREAM_COMMIT=" + current,
                                           "ARG UPSTREAM_COMMIT=" + newsha, 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["scan", "pin", "classify"])
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    if args.command == "scan":
        print(json.dumps(scan(), indent=2))
    elif args.command == "pin":
        pin(args.value or "")
    else:
        values = json.loads(args.value or "{}")
        risk, reason = classify(values.get("files", []), values.get("status"),
                                values.get("commits", 0), values.get("truncated", False))
        print(json.dumps({"risk": risk, "reason": reason}))


if __name__ == "__main__":
    main()
