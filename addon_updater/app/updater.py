"""Repository-scoped coordinator. It never edits installed options or data."""
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import threading
import time
import uuid

import requests
import yaml

from contract import manifest, safe_path
from notifier import reconcile

REPO = "justcop/home-assistant-addons"
PREFIX = "64b28acc_"
# The WhatsApp archive has privacy-critical upstream patches. A successful
# image build or a commit on main is not permission to deploy it live.
MANUAL_DEPLOY_APPS = frozenset({"whatsapp_mcp"})
ROOT = Path(os.environ.get("ADDON_CODE_ROOT", "/media/storage/addon-code"))
SESSION = requests.Session()


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def supervisor(path, method="GET", body=None):
    response = SESSION.request(method, "http://supervisor" + path,
                               headers={"Authorization": "Bearer " + os.environ["SUPERVISOR_TOKEN"]},
                               json=body, timeout=180)
    response.raise_for_status()
    result = response.json()
    if result.get("result") != "ok":
        raise RuntimeError("Supervisor rejected " + path + ": " + str(result.get("message", "Unknown error")))
    return result.get("data", {})


def snapshot(ref):
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise ValueError("Use a complete 40-character GitHub commit")
    response = SESSION.get(f"https://api.github.com/repos/{REPO}/tarball/{ref}", timeout=60, stream=True)
    response.raise_for_status()
    chunks, size = [], 0
    for chunk in response.iter_content(1024 * 1024):
        size += len(chunk)
        if size > 64 * 1024 * 1024:
            raise ValueError("Source archive is too large")
        chunks.append(chunk)
    result, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(b"".join(chunks)), mode="r:gz") as archive:
        for member in archive:
            name = member.name.partition("/")[2]
            if not name or member.isdir():
                continue
            if not member.isfile() or not safe_path(name):
                raise ValueError("Unsupported source archive member")
            total += member.size
            if member.size > 16 * 1024 * 1024 or total > 128 * 1024 * 1024:
                raise ValueError("Expanded archive exceeds limits")
            result[name] = archive.extractfile(member).read()
    return result


def candidate(files, directory, sha):
    specs = json.loads(files["delivery/apps.json"])
    if directory not in specs:
        return None
    config = yaml.safe_load(files[directory + "/config.yaml"])
    subset = {k[len(directory)+1:]:v for k,v in files.items() if k.startswith(directory + "/")}
    shared = files["delivery/contract.py"] + files["delivery/launcher.py"]
    result = manifest(subset, specs[directory], shared, config)
    result.update(sha=sha, id=config["slug"], directory=directory)
    return result


class Updater:
    def __init__(self, state_path=Path("/data/updater.json")):
        self.state_path = state_path
        self.state = json.loads(state_path.read_text()) if state_path.exists() else {"apps": {}, "transactions": {}}
        self.state.setdefault("transactions", {})
        self.lock = threading.Lock()
        self.busy = False
        self.cache = None

    def save(self):
        atomic(self.state_path, self.state)

    def status(self, slug):
        info = supervisor("/addons/" + slug + "/info")
        address = info.get("ip_address")
        if info.get("state") != "started" or not address:
            raise ValueError("Application is not running")
        result = SESSION.get(f"http://{address}:18799/status", timeout=4)
        result.raise_for_status()
        return result.json(), info

    def check(self):
        response = SESSION.get(f"https://api.github.com/repos/{REPO}/commits/main", timeout=20)
        response.raise_for_status()
        sha = response.json()["sha"]
        if self.cache is None or self.cache[0] != sha:
            self.cache = (sha, snapshot(sha))
        files = self.cache[1]
        release_hash = hashlib.sha256(files.get("delivery/releases.json", b"")).hexdigest()
        if release_hash != self.state.get("release_catalog"):
            supervisor("/store/reload", "POST", {})
            self.state["release_catalog"] = release_hash
        definitions = {}
        for path, body in files.items():
            if path.count("/") == 1 and path.endswith("/config.yaml"):
                config = yaml.safe_load(body)
                definitions[config["slug"]] = (path.split("/")[0], config)
        installed = supervisor("/addons")["addons"]
        seen = set()
        for app in installed:
            slug = app["slug"]
            if not slug.startswith(PREFIX) or slug[len(PREFIX):] not in definitions:
                continue
            ident = slug[len(PREFIX):]
            directory, config = definitions[ident]
            row = self.state["apps"].setdefault(ident, {})
            row.update(id=ident, slug=slug, directory=directory, name=app["name"],
                       version=app["version"], latest=sha, checked=now(), state=app.get("state"),
                       version_latest=config["version"])
            seen.add(ident)
            row["deployment_policy"] = "manual" if ident in MANUAL_DEPLOY_APPS else "automatic"
            if ident == "addon_updater":
                row.update(mode="standard", reason="Updater uses standard image updates", error=None)
                continue
            try:
                running, info = self.status(slug)
                if not running.get("running") and not running.get("error"):
                    raise ValueError("Application process is not running")
                spec = candidate(files, directory, sha)
                if spec and running.get("running"):
                    health = SESSION.get(f"http://{info['ip_address']}:{spec['spec']['health_port']}{spec['spec']['health_path']}",
                                         timeout=4, allow_redirects=False)
                    if health.status_code not in {200, 301, 302, 401, 403}:
                        raise ValueError("Application readiness check failed")
                row.update(deployed=running["sha"], image_sha=running["image_sha"], error=running.get("error"))
                desired = candidate(files, directory, sha)
                if desired is None or desired["compatibility"] != running["compatibility"]:
                    row.update(mode="rebuild", reason="Image, startup, configuration or persistence boundary changed")
                elif running.get("error"):
                    row.update(mode="failed", reason=running["error"])
                else:
                    same_code = hashlib.sha256(json.dumps(desired["runtime"], sort_keys=True).encode()).hexdigest() == running.get("runtime_hash")
                    row.update(mode="current" if same_code else "live", reason="Compatible code; no image build")
            except Exception as exc:
                row.update(mode="unavailable", reason=str(exc))
        self.state["apps"] = {k:v for k,v in self.state["apps"].items() if k in seen}
        self.state.update(checked=now(), latest=sha, error=None)
        self.save()
        return files

    def stage(self, value, files):
        if not ROOT.parent.is_dir() or os.stat(ROOT.parent).st_dev == os.stat("/media").st_dev:
            raise ValueError("External storage is not mounted; refusing internal-drive fallback")
        folder = ROOT / value["id"] / "releases" / value["sha"]
        if folder.exists():
            # Reuse only a complete identical release. Corruption is an error.
            self.validate_stage(folder, value)
            return folder
        staging = folder.with_name(".pending-" + uuid.uuid4().hex)
        staging.mkdir(parents=True)
        try:
            for path, expected in value["runtime"].items():
                content = files[value["directory"] + "/" + path]
                if path.endswith(".py"):
                    compile(content, path, "exec")
                target = staging / "files" / path
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            atomic(staging / "manifest.json", value)
            self.validate_stage(staging, value)
            os.rename(staging, folder)
            return folder
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @staticmethod
    def validate_stage(folder, value):
        recorded = json.loads((folder / "manifest.json").read_text())
        if recorded != value:
            raise ValueError("Existing release manifest differs")
        for path, expected in value["runtime"].items():
            target = folder / "files" / path
            if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError("Incomplete release; current application was left untouched")

    def restart_and_verify(self, slug, sha, spec):
        # Do not replay a restart after an uncertain HTTP outcome.
        supervisor("/addons/" + slug + "/restart", "POST", {})
        deadline = time.monotonic() + 120
        consecutive = 0
        while time.monotonic() < deadline:
            try:
                running, info = self.status(slug)
                if running.get("error"):
                    raise ValueError(running["error"])
                if running["sha"] == sha and running["running"]:
                    response = SESSION.get(f"http://{info['ip_address']}:{spec['health_port']}{spec['health_path']}", timeout=3,
                                           allow_redirects=False)
                    # Authenticated apps legitimately reject an anonymous request.
                    if response.status_code in {200, 301, 302, 401, 403}:
                        consecutive += 1
                        if consecutive >= 3:
                            return
                    else:
                        consecutive = 0
            except ValueError:
                raise
            except requests.RequestException:
                consecutive = 0
            time.sleep(2)
        raise RuntimeError("Application did not become healthy; recovery checkpoint retained")

    def deploy(self, ident, sha=None, files=None):
        row = self.state["apps"][ident]
        explicit = sha is not None
        if ident in self.state["transactions"]:
            raise ValueError("Interrupted deployment needs recovery before another update")
        sha = sha or self.state["latest"]
        if files is None:
            files = snapshot(sha)
        value = candidate(files, row["directory"], sha)
        running, info = self.status(row["slug"])
        if value is None or value["compatibility"] != running["compatibility"]:
            raise ValueError("That commit requires an image update or explicit persistence migration")
        if running.get("error"):
            raise ValueError("Resolve the application's reported error before deploying")
        if running["sha"] == sha:
            if explicit:
                row["pinned"] = True
                self.save()
            return
        self.stage(value, files)
        pin = ROOT / ident / "desired.json"
        previous = json.loads(pin.read_text()) if pin.exists() else None
        # Save the previous code identity before switching the pointer. /data
        # survives restarts, so interrupted deployments are never treated as done.
        self.state["transactions"][ident] = {"previous": previous, "target": sha,
                                              "before": running["sha"], "started": now()}
        self.save()
        atomic(pin, {"sha": sha, "image_sha": running["image_sha"]})
        try:
            self.restart_and_verify(row["slug"], sha, value["spec"])
        except Exception as exc:
            row.update(mode="failed", error=str(exc), reason="Deployment failed; recover the previous compatible code")
            self.save()
            raise
        self.state["transactions"].pop(ident)
        row.update(deployed=sha, updated=now(), error=None, mode="current", pinned=explicit, reason="Compatible code deployed")
        self.save()
        self.prune(ident, sha)

    def recover(self, ident):
        transaction = self.state["transactions"].get(ident)
        if not transaction:
            raise ValueError("No interrupted deployment to recover")
        row = self.state["apps"][ident]
        previous_sha = transaction["before"]
        # If the original code was baked, stage it too: the launcher's persisted
        # last commit otherwise correctly refuses a missing-pointer downgrade.
        files = snapshot(previous_sha)
        value = candidate(files, row["directory"], previous_sha)
        running, info = self.status(row["slug"])
        if value is None or value["compatibility"] != running["compatibility"]:
            raise ValueError("Previous code no longer matches the installed image")
        self.stage(value, files)
        atomic(ROOT / ident / "desired.json", {"sha": previous_sha, "image_sha": running["image_sha"]})
        self.restart_and_verify(row["slug"], previous_sha, value["spec"])
        self.state["transactions"].pop(ident)
        row.update(deployed=previous_sha, mode="current", pinned=True, error=None, updated=now())
        self.save()

    def prune(self, ident, keep):
        folder = ROOT / ident / "releases"
        if ident in self.state["transactions"]:
            return
        for old in folder.iterdir():
            if old.is_dir() and old.name != keep:
                shutil.rmtree(old)

    def run(self, action="check", ident=None, sha=None):
        if not self.lock.acquire(blocking=False):
            raise ValueError("An update operation is already running")
        self.busy = True
        try:
            if action == "recover":
                self.recover(ident)
            else:
                files = self.check()
                if action == "resume":
                    self.state["apps"][ident]["pinned"] = False
                    self.save()
                    self.deploy(ident, files=files)
                if action == "deploy":
                    self.deploy(ident, sha, files if sha is None else None)
                elif action == "automatic":
                    for name, row in list(self.state["apps"].items()):
                        if (name not in MANUAL_DEPLOY_APPS and row["mode"] == "live"
                                and not row.get("pinned") and name not in self.state["transactions"]):
                            try:
                                self.deploy(name, files=files)
                            except Exception as exc:
                                row.update(error=str(exc), mode="failed")
                                self.save()
        except Exception as exc:
            self.state["error"] = str(exc)
            self.save()
        finally:
            try:
                reconcile(self.state)
                self.save()
            except Exception:
                # Preserve the deployment error even if alert persistence fails.
                self.state["notification_error"] = "Unable to persist notification status; inspect updater storage."
            self.busy = False
            self.lock.release()
