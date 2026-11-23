"""Shared image launcher. Control is a pinned file; HTTP exposes status only."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time

from contract import safe_path, runtime_path, SCHEMA_WORDS, digest

ROOT = Path("/opt/delivery")
STORE = Path(os.environ.get("ADDON_CODE_ROOT", "/media/storage/addon-code"))


def read_json(path):
    return json.loads(path.read_text())


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    with pending.open("w") as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)


def validate_release(folder, wanted, baked):
    if folder.is_symlink() or not re.fullmatch(r"[0-9a-f]{40}", wanted):
        raise ValueError("Invalid release identity")
    candidate = read_json(folder / "manifest.json")
    if candidate.get("sha") != wanted or candidate.get("id") != baked["id"]:
        raise ValueError("Release identity mismatch")
    if candidate["compatibility"] != baked["compatibility"]:
        raise ValueError("This revision requires a different image or an explicit data migration")
    if candidate.get("boundary") != baked.get("boundary") or candidate.get("spec") != baked.get("spec"):
        raise ValueError("Release changed the installed image contract")
    for path, expected in candidate["runtime"].items():
        if not runtime_path(path, baked["spec"]):
            raise ValueError("Unsafe release path")
        target = folder / "files" / path
        if target.is_symlink() or not target.resolve().is_relative_to((folder / "files").resolve()):
            raise ValueError("Unsafe release member")
        content = target.read_bytes()
        if digest(content) != expected:
            raise ValueError("Incomplete or damaged release")
        if path in baked["boundary"] and expected != baked["boundary"][path]:
            raise ValueError("Protected persistence code requires an image")
        if path.endswith((".py", ".mjs", ".cjs", ".js")) and any(w in content.upper() for w in SCHEMA_WORDS):
            if baked["boundary"].get(path) != expected:
                raise ValueError("New schema or migration code requires an image")
        if path.endswith(".py"):
            compile(target.read_bytes(), path, "exec")
    return candidate


def install_code(folder, candidate, baked):
    destination = Path(baked["destination"])
    strip = baked.get("strip", "")
    names = set(baked["runtime"]) | set(candidate["runtime"])
    for name in sorted(names):
        if strip and not name.startswith(strip):
            raise ValueError("Release path outside application layout")
        relative = name[len(strip):] if strip else name
        if not safe_path(relative):
            raise ValueError("Invalid runtime layout")
        target = destination / relative
        if target.is_symlink() or not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError("Runtime destination escapes application")
        if name not in candidate["runtime"]:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_name(target.name + ".rapid-next")
        pending.write_bytes((folder / "files" / name).read_bytes())
        os.replace(pending, target)


def main():
    os.umask(0o077)
    baked = read_json(ROOT / "manifest.json")
    active = baked
    state = {"id": baked["id"], "image_sha": baked["sha"], "version": baked["version"],
             "compatibility": baked["compatibility"], "sha": baked["sha"], "error": None,
             "started": time.time(), "running": False}
    last_path = Path("/data/delivery.json")
    try:
        pin_path = STORE / baked["id"] / "desired.json"
        pin = read_json(pin_path) if pin_path.exists() else None
        if pin:
            folder = STORE / baked["id"] / "releases" / pin["sha"]
            if (pin.get("image_sha") != baked["sha"] and pin["sha"] in baked.get("ancestors", [])
                    and pin["sha"] != baked["sha"]):
                # A newer standard image contains this commit's descendants.
                # Its startup/dependency boundary supersedes the previous pin.
                active = baked
            else:
                candidate = validate_release(folder, pin["sha"], baked)
                install_code(folder, candidate, baked)
                active = candidate
        elif last_path.exists() and read_json(last_path).get("sha") != baked["sha"]:
            raise ValueError("Previously deployed code is unavailable; refusing a silent downgrade")
        state["sha"] = active["sha"]
        state["runtime_hash"] = hashlib.sha256(json.dumps(active["runtime"], sort_keys=True).encode()).hexdigest()
        atomic_json(last_path, {"sha": active["sha"], "version": baked["version"]})
    except Exception as exc:
        state["error"] = str(exc)

    class Status(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/status":
                self.send_error(404)
                return
            payload = json.dumps(state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 18799), Status)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if state["error"]:
        print("Delivery blocked:", state["error"], flush=True)
        # Keep status readable so the updater can report the actual failure.
        while True:
            time.sleep(60)
    child = subprocess.Popen(baked["spec"]["command"], cwd=baked["destination"],
                             start_new_session=True)
    state["running"] = True

    def stop(signum, frame):
        state["running"] = False
        os.killpg(child.pid, signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    raise SystemExit(child.wait())


if __name__ == "__main__":
    main()
