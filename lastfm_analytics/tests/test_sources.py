from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from analytics.db import Database
from analytics.insights import details, history, overview, period, rankings, scope
from analytics.web import create_app


def report(ts=1000, **extra):
    return dict(username="Justin", timestamp=ts, artist="The Beatles",
                title="Come Together", source="vinyl", **extra)


def play(ts=1000, album="Abbey Road"):
    return dict(ts=ts, artist="The Beatles", title="Come Together", album=album)


def test_before_sync_idempotence_edits_and_reconciliation(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    db.record_source("Justin", report())
    db.record_source("Justin", report())
    db.apply_window(0, 2000, [play(), play(1001)])
    # Album differences don't invalidate an exact timestamp and track match.
    db.apply_window(0, 2000, [play(album="Remastered"), play(1001)], reconcile=True)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_reports").fetchone()[0] == 1
        p = period({"period": "all"}, ZoneInfo("UTC"), earliest=1000)
        assert history(conn, p, {"source": "vinyl"}, ZoneInfo("UTC"))["total"] == 1
        assert history(conn, p, {"source": "unknown"}, ZoneInfo("UTC"))["total"] == 1
    db = Database(db.path)
    assert overview(db, {"period": "all", "source": "vinyl"}, "UTC")["current"]["plays"] == 1


def test_filters_apply_to_rankings_timeline_heatmap_and_versions(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    db.apply_window(0, 2000, [play(), play(1001)])
    db.record_source("Justin", report())
    args = {"period": "all", "source": "vinyl"}
    data = overview(db, args, "UTC", datetime(1970, 1, 2, tzinfo=timezone.utc))
    assert data["current"]["plays"] == 1
    assert sum(item["plays"] for item in data["timeline"]) == 1
    assert sum(map(sum, data["hours"])) == 1
    with db.connect() as conn:
        extra, params = scope(args)
        rows = rankings(conn, data["period"], "song", extra=extra, params=params)
        assert rows[0]["plays"] == 1
        result = details(conn, "song", rows[0]["id"], False, args)
        assert sum(v["plays"] for v in result["versions"]) == 1
    assert overview(db, {"period": "all", "source": "all"}, "UTC")["current"]["plays"] == 2
    with pytest.raises(ValueError):
        scope({"source": "spotify"})


def test_authenticated_ingestion_is_separate_from_browser_access(tmp_path):
    app = create_app(tmp_path, config={"username": "Justin", "source_api_token": "test-token"}, start_worker=False)
    client = app.test_client()
    endpoint = "/api/source-reports"
    headers = {"Authorization": "Bearer test-token"}
    assert client.post(endpoint, json=report()).status_code == 403
    assert client.post(endpoint, json=report(), headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.get("/api/history", headers=headers).status_code == 403
    assert client.post(endpoint, json=report(), headers=headers).status_code == 200
    wrong = report(); wrong["username"] = "another-user"
    assert client.post(endpoint, json=wrong, headers=headers).status_code == 400
    assert client.post(endpoint, json=[], headers=headers).status_code == 400
    wrong = report(); wrong["timestamp"] = True
    assert client.post(endpoint, json=wrong, headers=headers).status_code == 400
    disabled = create_app(tmp_path / "disabled", config={"username": "Justin"}, start_worker=False)
    assert disabled.test_client().post(endpoint, json=report(), headers=headers).status_code == 403


def test_guardian_to_analyser_delivery_and_source_filter(tmp_path):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace
    path = Path(__file__).resolve().parents[2] / "vinyl_guardian" / "source_reporting.py"
    spec = importlib.util.spec_from_file_location("guardian_reporting", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    app = create_app(tmp_path / "analyser", config={"username": "Justin", "source_api_token": "shared-token"}, start_worker=False)
    client = app.test_client()
    sender = module.SourceReporter(tmp_path / "guardian" / "pending.sqlite3", "http://analyser:8099", "shared-token", "Justin")
    event = report(); event.pop("username")
    sender.enqueue(event)
    def deliver(url, json, headers, **kwargs):
        result = client.post("/api/source-reports", json=json, headers=headers)
        return SimpleNamespace(status_code=result.status_code, json=result.get_json)
    assert sender.flush(deliver)
    db = app.extensions["database"]
    db.apply_window(0, 2000, [play(), play(1001)])
    response = client.get("/api/history?period=all&source=vinyl", environ_base={"REMOTE_ADDR": "172.30.32.2"})
    assert response.status_code == 200
    assert response.json["total"] == 1
    assert response.json["rows"][0]["source"] == "vinyl"
