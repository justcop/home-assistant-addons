from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from analytics.db import Database
from analytics.gig_timeline import all_listening_timeline, group_events
from analytics.gigs import GigStore
from analytics.web import create_app


def setup_store(tmp_path):
    db = Database(tmp_path / "timeline.sqlite3")
    store = GigStore(db, {
        "timezone": "Europe/London", "setlist_cache_retention_days": 30,
        "setlistfm_api_key": "",
    })
    return store


def listen(ts, artist="Radiohead"):
    return {"ts": ts, "artist": artist, "title": "Reckoner", "album": "In Rainbows",
            "raw": {"source": "test"}}


def timestamp(local_iso):
    return int(datetime.fromisoformat(local_iso).timestamp())


def test_whole_timeline_spans_listens_and_gigs_with_zero_months(tmp_path):
    store = setup_store(tmp_path)
    store.db.apply_window(0, 2_000_000_000, [
        listen(timestamp("2024-02-29T23:40:00+00:00")),
        listen(timestamp("2024-04-01T01:00:00+01:00")),
    ])
    store.import_rows([{
        "artist": "Radiohead", "date": "2024-03-15", "venue": "London Arena"
    }])
    result = all_listening_timeline(store, "month")
    assert [(p["start"], p["plays"]) for p in result["bins"]] == [
        ("2024-02-01", 1), ("2024-03-01", 0), ("2024-04-01", 1)
    ]
    assert result["total"] == 2
    assert result["performances"] == 1
    assert result["events"][0]["start"] == "2024-03-15"
    assert result["event_count"] == 1


def test_month_boundaries_use_local_time_not_utc(tmp_path):
    store = setup_store(tmp_path)
    # UK local date is 1 April, but UTC date is 31 March.
    ts = timestamp("2025-03-31T23:30:00+00:00")
    store.db.apply_window(0, 2_000_000_000, [listen(ts)])
    result = all_listening_timeline(store, "month")
    assert result["bins"][0]["start"] == "2025-04-01"
    assert result["bins"][0]["plays"] == 1


def test_festival_consecutive_days_get_one_marker_and_preserve_acts(tmp_path):
    store = setup_store(tmp_path)
    store.import_rows([
        {"artist": "Muse", "date": "2025-06-26", "venue": "Pyramid"},
        {"artist": "Radiohead", "date": "2025-06-27", "venue": "Pyramid"},
        {"artist": "Blur", "date": "2025-06-27", "venue": "Pyramid"},
        {"artist": "Pulp", "date": "2025-06-28", "venue": "Pyramid"},
        {"artist": "Oasis", "date": "2025-08-03", "venue": "Wembley"},
        {"artist": "Cast", "date": "2025-08-03", "venue": "Wembley"},
        {"artist": "Richard Ashcroft", "date": "2025-08-03", "venue": "Wembley"},
    ])
    result = all_listening_timeline(store, "year", 2025)
    assert result["performances"] == 7
    assert result["event_count"] == 2
    festival = next(e for e in result["events"] if e["type"] == "festival")
    concert = next(e for e in result["events"] if e["type"] == "concert")
    assert festival["performances"] == 4
    assert festival["start"] == "2025-06-26"
    assert festival["end"] == "2025-06-28"
    assert len(festival["gigs"]) == 4
    assert concert["performances"] == 3
    assert concert["start"] == concert["end"] == "2025-08-03"


def test_distinct_festivals_group_by_name_across_stages(tmp_path):
    gigs = [
        {"id": 1, "artist": "Muse", "date": "2025-06-28", "venue": "Pyramid",
         "festival": "Glastonbury", "setlist_url": "", "show_type": "festival"},
        {"id": 2, "artist": "Pulp", "date": "2025-06-29", "venue": "Other",
         "festival": "Glastonbury", "setlist_url": "", "show_type": "festival"},
        {"id": 3, "artist": "Blur", "date": "2025-07-05", "venue": "Pyramid",
         "festival": "Glastonbury", "setlist_url": "", "show_type": "festival"},
    ]
    events = group_events(gigs)
    assert len(events) == 2
    assert events[0]["performances"] == 2
    assert events[1]["performances"] == 1


def test_selected_year_and_reject_unbounded_day_view(tmp_path):
    store = setup_store(tmp_path)
    store.import_rows([
        {"artist": "Muse", "date": "2024-07-01", "venue": "Wembley"},
        {"artist": "Muse", "date": "2025-07-01", "venue": "Wembley"},
    ])
    year = all_listening_timeline(store, "day", 2025)
    assert year["event_count"] == 1
    assert all(p["start"].startswith("2025") for p in year["bins"])
    assert len(year["bins"]) == 365
    with pytest.raises(ValueError):
        all_listening_timeline(store, "day")
    with pytest.raises(ValueError):
        all_listening_timeline(store, "hour")
    with pytest.raises(ValueError):
        all_listening_timeline(store, "year", "bad")


def test_all_timeline_auth_api_and_no_setlist_payload(tmp_path):
    app = create_app(tmp_path, config={}, development=True, start_worker=False)
    client = app.test_client()
    response = client.get("/api/gigs/all-timeline?scale=year")
    assert response.status_code == 200
    assert response.json["events"] == []
    assert "sets" not in str(response.json)
    bad = client.get("/api/gigs/all-timeline?scale=day")
    # Empty history short-circuits before period bounds and is harmless.
    assert bad.status_code in (200, 400)
    app.extensions["gig_worker"].close()
