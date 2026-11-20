#!/usr/bin/env python3
"""At 05:00 Europe/London, validate only add-ons changed in the last 24h.

Dispatch existing comprehensive workflow_dispatch checks, and wait for their
actual conclusions. Never dispatch publishing workflows (notably the Android
helper APK publisher and Vinyl Guardian release).
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


WORKFLOWS = {
    "audioshelf": "audioshelf-tests.yml",
    "lastfm_analytics": "listening-analytics-tests.yml",
    "vinyl_guardian": "vinyl-guardian-tests.yml",
    "money_locations": "money-locations-tests.yml",
    "music_video_companion": "music-video-companion-tests.yml",
    "work_audit": "work-audit-tests.yml",
}
# These have no dedicated comprehensive test workflow. Validate the actual
# source, and build images where the directory provides a Dockerfile.
SMOKE_ONLY = ("govee-palette-studio", "vikunja_chatgpt_bridge",
              "github_actions_cleanup", "python")
# Android helper is deliberately excluded: its manual workflow would publish
# a signed APK. Its full Gradle test/lint/build already runs on main pushes.
ALREADY_FULL_ON_PUSH = ("audioshelf/android-helper/",)


def changed_files():
    result = subprocess.run(
        ["git", "log", "--since=24 hours ago", "--format=", "--name-only", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def classify(paths):
    dispatched = sorted(
        (directory, workflow)
        for directory, workflow in WORKFLOWS.items()
        if any(path.startswith(directory + "/")
               and not (directory == "audioshelf"
                        and path.startswith("audioshelf/android-helper/"))
               for path in paths)
    )
    # Discover future add-ons automatically, rather than silently omitting a
    # new app until somebody adds it to this orchestration script.
    discovered = {
        child.name for child in Path(".").iterdir()
        if child.is_dir() and (child / "config.yaml").is_file()
        and child.name not in WORKFLOWS
    }
    smoke = sorted(directory for directory in set(SMOKE_ONLY) | discovered
                   if any(path.startswith(directory + "/") for path in paths))
    android = any(path.startswith("audioshelf/android-helper/") for path in paths)
    return dispatched, smoke, android


def api(method, endpoint, payload=None):
    token = os.environ["GITHUB_TOKEN"]
    request = Request(
        "https://api.github.com/" + endpoint.lstrip("/"),
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except HTTPError as exc:
        raise RuntimeError(f"GitHub API {method} {endpoint}: HTTP {exc.code}") from None


def smoke_test(directory):
    path = Path(directory)
    print(f"Running source checks for {directory}", flush=True)
    for file in path.rglob("*"):
        if not file.is_file():
            continue
        if file.suffix in (".js", ".cjs", ".mjs"):
            subprocess.run(["node", "--check", str(file)], check=True)
        elif file.suffix == ".sh":
            subprocess.run(["bash", "-n", str(file)], check=True)
    python_files = list(path.rglob("*.py"))
    if python_files:
        subprocess.run([sys.executable, "-m", "compileall", "-q", str(path)], check=True)
    if (path / "Dockerfile").is_file():
        subprocess.run([
            "docker", "build", "--build-arg", "BUILD_VERSION=nightly",
            "--build-arg", "BUILD_ARCH=amd64", "-t",
            "nightly-" + directory.replace("_", "-") + ":test", str(path),
        ], check=True)


def write_summary(lines):
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    paths = changed_files()
    dispatched, smoke, android = classify(paths)
    summary = ["## 05:00 UK time — recently changed app checks", "",
               f"Changed tracked paths in preceding 24 hours: {len(paths)}.", ""]
    if not dispatched and not smoke and not android:
        write_summary(summary + ["No app source changed. All expensive checks skipped."])
        return 0
    summary += ["| App | Validation | Result |", "|---|---|---|"]
    if android:
        summary.append("| AudioShelf Android helper | Full Gradle checks already run on main; "
                       "nightly publishing intentionally disabled | Covered by main CI |")
    if args.dry_run:
        summary += [f"| {app} | {workflow} | Would run |" for app, workflow in dispatched]
        summary += [f"| {app} | Local syntax/container smoke | Would run |" for app in smoke]
        write_summary(summary)
        return 0

    repository = os.environ["GITHUB_REPOSITORY"]
    started = []
    problems = []
    for app, workflow in dispatched:
        try:
            result = api("POST", f"repos/{repository}/actions/workflows/{workflow}/dispatches",
                         {"ref": "main", "return_run_details": True})
            run_id = result.get("workflow_run_id")
            if not isinstance(run_id, int):
                raise RuntimeError(f"Workflow {workflow} did not return a run ID")
            started.append((app, run_id, result.get("html_url", "")))
            print(f"Dispatched {app} comprehensive checks: {run_id}", flush=True)
        except Exception as exc:
            problems.append(f"{app}: {exc}")
            summary.append(f"| {app} | {workflow} | Dispatch failed |")

    for app in smoke:
        try:
            smoke_test(app)
            summary.append(f"| {app} | Source syntax and Docker build where available | Passed |")
        except (OSError, subprocess.CalledProcessError) as exc:
            problems.append(f"{app}: {exc}")
            summary.append(f"| {app} | Source syntax and Docker build | Failed |")

    pending = {run_id: (app, url) for app, run_id, url in started}
    deadline = time.monotonic() + 3600
    while pending and time.monotonic() < deadline:
        for run_id, (app, url) in list(pending.items()):
            try:
                details = api("GET", f"repos/{repository}/actions/runs/{run_id}")
            except Exception as exc:
                problems.append(f"{app}: cannot read run {run_id}: {exc}")
                summary.append(f"| {app} | {url or run_id} | Status unavailable |")
                del pending[run_id]
                continue
            if details.get("status") == "completed":
                conclusion = details.get("conclusion")
                summary.append(f"| {app} | {details.get('html_url', url)} | {conclusion} |")
                if conclusion != "success":
                    problems.append(f"{app}: run {run_id} finished {conclusion}")
                del pending[run_id]
        if pending:
            time.sleep(20)
    for run_id, (app, url) in pending.items():
        summary.append(f"| {app} | {url or run_id} | Timed out |")
        problems.append(f"{app}: checks did not complete within one hour")

    write_summary(summary)
    if problems:
        print("Nightly validation failed:\n" + "\n".join(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
