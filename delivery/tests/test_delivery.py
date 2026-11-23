"""Behavioral contracts for compatibility, atomic deployment and recovery."""
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "delivery"))
sys.path.insert(0, str(ROOT / "addon_updater/app"))
from contract import digest, manifest, runtime_path
import launcher
import release
import updater
from server import create_app

SPEC = {"roots": ["app"], "protected": ["app/db.py"], "command": ["python3", "server.py"],
        "health_port": 8099, "health_path": "/health"}
CONFIG = {"slug": "demo", "version": "1.2.3", "map": []}
FILES = {"config.yaml": b"version: 1.2.3", "Dockerfile": b"FROM stable",
         "requirements.txt": b"flask==3.1.1", "app/server.py": b"answer=1",
         "app/db.py": b"def save(): pass", "app/static/site.css": b"body{}"}


def build(files=None):
    return manifest(files or FILES, SPEC, b"launcher", CONFIG)


@pytest.mark.parametrize("path,body", [
    ("app/static/site.css", b"body{color:red}"), ("app/server.py", b"answer=2"),
    ("app/templates/index.html", b"hello"), ("app/new.py", b"new=1")])
def test_compatible_application_changes(path, body):
    a, b = build(), build({**FILES, path: body})
    assert a["compatibility"] == b["compatibility"]
    assert a["runtime"] != b["runtime"]


@pytest.mark.parametrize("path,body", [
    ("Dockerfile", b"FROM changed"), ("requirements.txt", b"new-dependency"),
    ("new-system-install", b"apk add something"), ("run.sh", b"new startup"),
    ("app/requirements.txt", b"new-dependency"), ("app/package.json", b"{}"),
    ("app/db.py", b"def save(): return 1"),
    ("app/server.py", b"query='CREATE TABLE hidden (id int)'"),
    ("translations/en.yaml", b"new translated option"),
])
def test_build_and_persistence_boundaries(path, body):
    assert build()["compatibility"] != build({**FILES, path: body})["compatibility"]


def test_config_permissions_and_shared_launcher_are_build_inputs():
    a = build()["compatibility"]
    assert manifest(FILES, SPEC, b"launcher", {**CONFIG, "host_network": True})["compatibility"] != a
    assert manifest(FILES, SPEC, b"new-launcher", CONFIG)["compatibility"] != a
    assert manifest(FILES, SPEC, b"launcher", {**CONFIG, "version":"2.0.0", "image":"generated"})["compatibility"] == a


def test_docs_tests_android_do_not_trigger_images():
    for name in ("README.md", "tests/test_thing.py", "android-helper/app/build.gradle"):
        assert build({**FILES, name: b"changed"}) == build()


def test_path_traversal_and_nested_dependency_rejected():
    for path in ("/app/a.py", "app/../../data/key", "app/x/requirements.txt", "app/package-lock.json"):
        assert not runtime_path(path, SPEC)


def staged(tmp_path, files=None):
    files = files or FILES
    value = build(files)
    value.update(id="demo", directory="demo", sha="a"*40)
    client = updater.Updater(tmp_path / "state.json")
    updater.ROOT = tmp_path / "media/storage/addon-code"
    updater.ROOT.parent.mkdir(parents=True)
    original_stat = updater.os.stat
    # The test filesystem has no external mount. Only the mount guard gets an
    # alternate device; all file operations still exercise the real filesystem.
    def stat(path, *args, **kwargs):
        if str(path) == "/media":
            return type("Stat", (), {"st_dev": -1})()
        return original_stat(path, *args, **kwargs)
    return client, value, {"demo/"+k:v for k,v in files.items()}, stat


def test_staging_corruption_never_changes_existing_pointer(tmp_path, monkeypatch):
    client, value, files, stat = staged(tmp_path)
    monkeypatch.setattr(updater.os, "stat", stat)
    folder = client.stage(value, files)
    assert launcher.validate_release(folder, value["sha"], value) == value
    (folder / "files/app/server.py").write_text("corrupted")
    with pytest.raises(ValueError, match="Incomplete"):
        client.stage(value, files)
    assert not (folder.parent.parent / "desired.json").exists()


def test_interrupted_download_does_not_publish_release(tmp_path, monkeypatch):
    client, value, files, stat = staged(tmp_path)
    monkeypatch.setattr(updater.os, "stat", stat)
    files.pop("demo/app/server.py")
    with pytest.raises(KeyError):
        client.stage(value, files)
    assert not list(updater.ROOT.rglob("manifest.json"))
    assert not list(updater.ROOT.rglob(".pending-*"))


def test_invalid_python_rejected_before_switching(tmp_path, monkeypatch):
    client, value, files, stat = staged(tmp_path, {**FILES, "app/server.py": b"def broken("})
    monkeypatch.setattr(updater.os, "stat", stat)
    with pytest.raises(SyntaxError):
        client.stage(value, files)
    assert not list(updater.ROOT.rglob("desired.json"))


def test_runtime_refuses_incompatible_or_symlinked_payload(tmp_path, monkeypatch):
    client, value, files, stat = staged(tmp_path)
    monkeypatch.setattr(updater.os, "stat", stat)
    folder = client.stage(value, files)
    with pytest.raises(ValueError, match="different image"):
        launcher.validate_release(folder, value["sha"], {**value, "compatibility": "other"})
    target=folder / "files/app/server.py"
    target.unlink()
    target.symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="Unsafe release"):
        launcher.validate_release(folder, value["sha"], value)


def test_runtime_installs_only_code_and_preserves_data(tmp_path, monkeypatch):
    client, value, files, stat = staged(tmp_path)
    monkeypatch.setattr(updater.os, "stat", stat)
    folder = client.stage(value, files)
    dest = tmp_path / "runtime"
    dest.mkdir()
    database = tmp_path / "application-data/database.sqlite"
    database.parent.mkdir()
    database.write_bytes(b"irreplaceable-state")
    launcher.install_code(folder, value, {**value, "destination": str(dest), "strip":""})
    assert (dest / "app/server.py").read_bytes() == FILES["app/server.py"]
    assert database.read_bytes() == b"irreplaceable-state"


def test_earlier_commit_removes_new_files_from_previous_or_interrupted_update(tmp_path, monkeypatch):
    client, value, files, stat = staged(tmp_path)
    monkeypatch.setattr(updater.os, "stat", stat)
    folder = client.stage(value, files)
    dest = tmp_path / "runtime"
    (dest / "app").mkdir(parents=True)
    obsolete = dest / "app/introduced_later.py"
    obsolete.write_text("obsolete=True")
    launcher.install_code(folder, value, {**value, "destination":str(dest), "strip":""},
                          known=["app/introduced_later.py"])
    assert not obsolete.exists()
    assert (dest / "app/server.py").exists()


def test_failed_restart_retains_durable_recovery_transaction(tmp_path, monkeypatch):
    client, value, files, stat = staged(tmp_path)
    monkeypatch.setattr(updater.os, "stat", stat)
    client.state.update(latest=value["sha"], apps={"demo":{"slug":"64b28acc_demo", "directory":"demo"}})
    monkeypatch.setattr(updater, "candidate", lambda *args: value)
    monkeypatch.setattr(client, "status", lambda *args: ({"sha":"b"*40,"image_sha":"b"*40,"compatibility":value["compatibility"]}, {}))
    monkeypatch.setattr(client, "restart_and_verify", Mock(side_effect=TimeoutError("restart outcome uncertain")))
    with pytest.raises(TimeoutError):
        client.deploy("demo", files=files)
    disk = json.loads(client.state_path.read_text())
    assert disk["transactions"]["demo"]["before"] == "b"*40
    assert disk["apps"]["demo"]["mode"] == "failed"
    with pytest.raises(ValueError, match="Interrupted"):
        client.deploy("demo", files=files)


def test_release_version_is_automatic_and_respects_manual_major():
    assert release.next_version("1.2.3", "1.2.3") == "1.2.4"
    assert release.next_version("2.0.0", "1.2.3") == "2.0.0"
    assert release.next_version("1.2.3", None) == "1.2.4"


def test_admin_ui_requires_ingress_and_csrf(tmp_path):
    client=updater.Updater(tmp_path / "state.json")
    web=create_app(client, {}, testing=True).test_client()
    assert web.get("/api/status").status_code == 403
    env={"REMOTE_ADDR":"172.30.32.2"}
    assert web.get("/api/status", environ_base=env).status_code == 200
    assert web.post("/api/action",json={"action":"check"},environ_base=env).status_code == 403
    assert b"meta name=\"csrf\"" in web.get("/",environ_base=env).data


def test_all_repository_live_layouts_have_existing_entry_points():
    apps = release.catalog()
    assert {"ocr_tool", "audioshelf", "lastfm_analytics", "money_locations", "addon_updater"} <= apps.keys()
    for name,item in apps.items():
        if item["live"]:
            assert item["runtime"], name
            assert name in release.LAYOUT, name


def test_top_level_wildcard_cannot_write_into_persistent_directories():
    spec = {"roots": ["*.py", "artwork"]}
    assert runtime_path("run.py", spec)
    assert not runtime_path("data/options.py", spec)
    assert not runtime_path("media/other.py", spec)


@pytest.mark.parametrize("kind", ["protected", "schema", "dependency", "boundary"])
def test_launcher_independently_rejects_forged_contract(tmp_path, monkeypatch, kind):
    client, value, files, stat = staged(tmp_path)
    monkeypatch.setattr(updater.os, "stat", stat)
    folder = client.stage(value, files)
    forged = json.loads(json.dumps(value))
    if kind == "protected":
        name, content = "app/db.py", b"def save(): return 'changed'"
    elif kind == "schema":
        name, content = "app/new.py", b"sql='ALTER TABLE users ADD password text'"
    elif kind == "dependency":
        name, content = "app/requirements.txt", b"secret-dependency"
    else:
        forged["boundary"]["Dockerfile"] = "forged"
        name, content = None, None
    if name:
        path = folder / "files" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        forged["runtime"][name] = digest(content)
    (folder / "manifest.json").write_text(json.dumps(forged))
    with pytest.raises(ValueError):
        launcher.validate_release(folder, value["sha"], value)


@pytest.mark.parametrize("scenario,expected", [
    ("newer-image", "b"*40), ("older-image", "a"*40), ("explicit-rollback", "a"*40)])
def test_image_updates_and_compatible_rollback_keep_correct_code(tmp_path, monkeypatch, scenario, expected):
    client, value, files, stat = staged(tmp_path)
    monkeypatch.setattr(updater.os, "stat", stat)
    client.stage(value, files)
    data, root, dest = tmp_path / "data", tmp_path / "baked", tmp_path / "application"
    root.mkdir()
    dest.mkdir()
    baked = {**value, "sha":"b"*40, "version":"1.2.4", "destination":str(dest), "strip":""}
    baked["ancestors"] = ["a"*40] if scenario != "older-image" else []
    (root / "manifest.json").write_text(json.dumps(baked))
    pin = {"sha":"a"*40, "image_sha":("b"*40 if scenario=="explicit-rollback" else "c"*40)}
    updater.atomic(updater.ROOT / "demo/desired.json", pin)
    monkeypatch.setattr(launcher, "ROOT", root)
    monkeypatch.setattr(launcher, "DATA", data)
    monkeypatch.setattr(launcher, "STORE", updater.ROOT)
    monkeypatch.setattr(launcher, "ThreadingHTTPServer", Mock())
    monkeypatch.setattr(launcher.threading, "Thread", Mock())
    monkeypatch.setattr(launcher.signal, "signal", Mock())
    child = Mock()
    child.wait.return_value = 0
    monkeypatch.setattr(launcher.subprocess, "Popen", Mock(return_value=child))
    previous_umask = launcher.os.umask(0o077)
    try:
        with pytest.raises(SystemExit):
            launcher.main()
    finally:
        launcher.os.umask(previous_umask)
    assert json.loads((data / "delivery.json").read_text())["sha"] == expected
    assert child.wait.called
