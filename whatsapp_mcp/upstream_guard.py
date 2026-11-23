#!/usr/bin/env python3
"""Check the pinned spare-cycles WhatsApp MCP upstream and prepare a guarded PR.

This script reads GitHub's REST API. No third-party code is executed here.
Only a docs/tests-only upstream diff is eligible for automatic merge.
All runtime, lockfile, schema, privacy, Baileys and unknown diffs need review.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "whatsapp_mcp" / "Dockerfile"
PIN = re.compile(r"(?m)^(ARG UPSTREAM_COMMIT=)([a-f0-9]{40})$")
API = "https://api.github.com/repos/spare-cycles/whatsapp-mcp"
SAFE_ROOTS = ("docs/", "examples/", ".github/ISSUE_TEMPLATE/")
SAFE_FILES = {"README.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE", "SECURITY.md",
              ".editorconfig", ".gitattributes"}
SAFE_EXTS = (".md", ".mdx", ".txt")
# Upstream test-only modifications are non-executable in our deployed image;
# nevertheless lockfiles and build scripts are categorically high risk.
SAFE_TEST_DIRS = ("tests/", "test/", "__tests__/")


def request_json(url: str) -> dict:
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "justcop-whatsapp-upstream-guard/1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def risk_for_paths(paths: list[str], compare_complete: bool) -> tuple[str, list[str]]:
    if not compare_complete or not paths:
        return "high", ["Missing, incomplete, or untrusted upstream diff"]
    risky = []
    for p in paths:
        if p in SAFE_FILES or (p.startswith(SAFE_ROOTS) and p.endswith(SAFE_EXTS)):
            continue
        if p.startswith(SAFE_TEST_DIRS) and (
                p.endswith((".test.ts", ".spec.ts", ".test.mjs", ".spec.mjs", ".md"))):
            continue
        risky.append(p)
    return ("high", risky) if risky else ("low", [])


def plan(head_sha: str | None = None, comparison: dict | None = None) -> dict:
    docker = DOCKERFILE.read_text()
    pins = PIN.findall(docker)
    if len(pins) != 1:
        raise ValueError("The upstream commit pin must appear exactly once")
    current = pins[0][1]
    latest = head_sha or request_json(API + "/commits/main")["sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", latest):
        raise ValueError("Invalid upstream commit SHA")
    if current == latest:
        return {"changed": False, "current": current, "latest": latest, "risk": "none"}
    diff = comparison or request_json(API + "/compare/" + current + "..." + latest)
    files = diff.get("files") or []
    changed = [f.get("filename", "") for f in files]
    # GitHub compare truncates file listings above 300. Also treat divergent
    # or failed compare results conservatively, never as safe.
    complete = diff.get("status") == "ahead" and 0 < diff.get("ahead_by", 0) <= 250 and len(files) < 300
    risk, flagged = risk_for_paths(changed, complete)
    return {"changed": True, "current": current, "latest": latest, "risk": risk,
            "files": changed, "high_risk_paths": flagged[:120],
            "upstream_compare": "https://github.com/spare-cycles/whatsapp-mcp/compare/" +
                                current + "..." + latest}


def update_pin(result: dict) -> None:
    if not result["changed"]:
        return
    original = DOCKERFILE.read_text()
    replaced, count = PIN.subn(lambda match: match.group(1) + result["latest"], original)
    if count != 1:
        raise ValueError("Pin anchor changed")
    DOCKERFILE.write_text(replaced)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/tmp/whatsapp-upstream-plan.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = plan()
    if args.apply:
        update_pin(result)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
