"""Live asset updates do not restart Flask or change backend code."""
import json

from app import server
from app.live_assets import LiveAssets


def marker(path, sha):
    (path / ".dev-manager.json").write_text(json.dumps({
        "id": "audioshelf", "slug": "local_audioshelf",
        "sha": sha, "version": "0.6.49-dev.test",
    }))


def test_hot_css_and_javascript_appear_without_restarting_app(tmp_path, monkeypatch):
    root = tmp_path / "devmgr_audioshelf"
    static = root / "app" / "static"
    static.mkdir(parents=True)
    (static / "style.css").write_text("body { color: red }")
    (static / "app.js").write_text("'use strict'; // old")
    first = "a" * 40
    marker(root, first)
    monkeypatch.setattr(server, "LIVE_SOURCE_ROOT", root)
    app = server.create_app({
        "data_directory": str(tmp_path / "data"),
        "cache_directory": str(tmp_path / "cache"),
        "private_directory": str(tmp_path / "private"),
    })
    client = app.test_client()
    assert b"color: red" in client.get("/static/style.css").data
    assert b"old" in client.get("/static/app.js").data
    old_version = client.get("/api/status").json["build"]["asset_version"]
    assert first[:16] in old_version
    assert old_version in client.get("/").text

    (static / "style.css.tmp").write_text("body { color: blue }")
    (static / "style.css.tmp").replace(static / "style.css")
    (static / "app.js.tmp").write_text("'use strict'; // new")
    (static / "app.js.tmp").replace(static / "app.js")
    marker(root, "b" * 40)
    assert b"color: blue" in client.get("/static/style.css").data
    assert b"new" in client.get("/static/app.js").data
    new_version = client.get("/api/status").json["build"]["asset_version"]
    assert new_version != old_version
    assert "b" * 16 in new_version
    assert new_version in client.get("/").text


def test_invalid_live_marker_is_not_used(tmp_path):
    root = tmp_path / "devmgr_audioshelf"
    static = root / "app" / "static"
    static.mkdir(parents=True)
    (static / "app.js").write_text("untrusted JS")
    marker(root, "invalid")
    live = LiveAssets(root, "compiled-version")
    assert live.static_root() is None
    assert live.version() == "compiled-version"


def test_missing_live_source_uses_compiled_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LIVE_SOURCE_ROOT", tmp_path / "missing")
    app = server.create_app({
        "data_directory": str(tmp_path / "data"),
        "cache_directory": str(tmp_path / "cache"),
        "private_directory": str(tmp_path / "private"),
    })
    assert b"strict" in app.test_client().get("/static/app.js").data
