"""Fail-closed, deterministic structural eligibility rules. No merge capability.

The AI must never expand this allowlist. Actual merging is disabled until
branch protection, current-SHA checks, age, and rate limits are independently verified.
"""
from __future__ import annotations

import re

ALLOWLIST = frozenset({"lastfm_analytics/requirements-dev.txt"})
PIN = re.compile(r"^([A-Za-z][A-Za-z0-9_.-]*)==([0-9]+)\.([0-9]+)\.([0-9]+)$")
BLOCKED_PREFIXES = (
    "vinyl_guardian/", "audioshelf/", ".github/workflows/",
    "lastfm_analytics/requirements.txt",
)


def structural_eligibility(pr: dict, files: list[dict],
                           before: str | None, after: str | None) -> tuple[bool, str]:
    """Only a single patch-level pin bump in one test-only file may qualify.

    This is not sufficient for merging. A future trusted merger must additionally
    check latest-head required CI, branch protection, age, and daily rate limit.
    """
    user = pr.get("user") or {}
    if user.get("login") != "dependabot[bot]" or user.get("type") != "Bot":
        return False, "Not an authenticated Dependabot-authored PR"
    if pr.get("base", {}).get("ref") != "main":
        return False, "Not targeting main"
    repo = (pr.get("base", {}).get("repo") or {}).get("full_name")
    head_repo = (pr.get("head", {}).get("repo") or {}).get("full_name")
    if not repo or repo != head_repo:
        return False, "Head repository differs from the target repository"
    if len(files) != 1 or files[0].get("filename") not in ALLOWLIST:
        return False, "Not an isolated allowlisted test-only dependency change"
    if files[0].get("status") != "modified":
        return False, "File was added, deleted, or renamed"
    if not isinstance(before, str) or not isinstance(after, str):
        return False, "Unable to verify complete old and new file contents"
    a, b = before.splitlines(), after.splitlines()
    if len(a) != len(b):
        return False, "File line count changed"
    changes = [(x, y) for x, y in zip(a, b) if x != y]
    if len(changes) != 1:
        return False, "Update is not exactly one changed dependency pin"
    old, new = changes[0]
    x, y = PIN.fullmatch(old), PIN.fullmatch(new)
    if not x or not y or x.group(1).lower() != y.group(1).lower():
        return False, "Changed line is not the same strictly pinned package"
    if x.group(2, 3) != y.group(2, 3):
        return False, "Major or minor version changed"
    if int(y.group(4)) <= int(x.group(4)):
        return False, "Patch version did not increase"
    return True, f"Isolated test-only patch update: {x.group(1)} {x.group(0).split('==')[1]} → {y.group(0).split('==')[1]}"


def merge_safety_gate(*, structurally_eligible: bool, ai_approved: bool,
                      required_checks_passed: bool, checks_current: bool,
                      branch_protected: bool, conflict_free: bool,
                      age_hours: float, merges_last_24h: int,
                      automerge_enabled: bool = False) -> bool:
    """All independent conditions are required. Default is permanently OFF."""
    return all((
        automerge_enabled, structurally_eligible, ai_approved,
        required_checks_passed, checks_current, branch_protected, conflict_free,
        age_hours >= 48, merges_last_24h == 0,
    ))
