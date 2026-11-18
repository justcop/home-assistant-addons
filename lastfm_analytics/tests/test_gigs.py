from datetime import date
from analytics.gigs import anniversary, due, GigStore
from analytics.db import Database


def gig(day, checked=None, retry=None):
    return {"date": day, "last_checked": checked, "next_retry": retry}


def test_age_based_schedule():
    assert due(gig("2026-11-17", "2026-11-17"), date(2026, 11, 18))
    assert not due(gig("2026-11-10", "2026-11-17"), date(2026, 11, 18))
    assert due(gig("2026-11-10", "2026-11-16"), date(2026, 11, 18))
    assert due(gig("2026-10-01", "2026-10-15"), date(2026, 11, 18))
    assert not due(gig("2025-06-15", "2026-06-15"), date(2026, 11, 18))
    assert due(gig("2025-06-15", "2026-06-15"), date(2027, 6, 15))
    assert due(gig("2024-02-29", "2027-02-28"), date(2028, 2, 29))
    assert anniversary(date(2024, 2, 29), 2027) == date(2027, 2, 28)


def test_import_and_cache_expiry(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    config = {"timezone": "Europe/London", "setlist_cache_retention_days": 30,
              "setlistfm_api_key": ""}
    store = GigStore(db, config)
    rows = [{"artist": "Muse", "date": "2025-06-15", "venue": "Wembley",
             "festival": "", "setlist_id": "abc123"},
            {"artist": "Muse", "date": "2024-06-15", "festival": "Glastonbury"}]
    assert store.import_rows(rows) == 2
    assert store.import_rows(rows) == 2
    assert len(store.list()) == 2
    assert store.list("Muse")[0]["date"] == "2025-06-15"
    assert store.refresh()["processed"] == 0


def test_festival_rule_applies_on_manual_import_without_erasing_overrides(tmp_path):
    db = Database(tmp_path / "festivals.sqlite3")
    store = GigStore(db, {
        "timezone": "Europe/London", "setlist_cache_retention_days": 30,
        "setlistfm_api_key": ""
    })
    records = [
        {"artist": "Band A", "date": "2025-06-10", "venue": "Open Air Park"},
        {"artist": "Band B", "date": "2025-06-11", "venue": "Open Air Park"},
        {"artist": "Band C", "date": "2025-06-11", "venue": "Open Air Park"},
        {"artist": "Band D", "date": "2025-06-11", "venue": "Another Park"},
        {"artist": "Band E", "date": "2025-07-01", "venue": "Solo Arena",
         "festival": "Explicit Festival"},
    ]
    assert store.import_rows(records) == 5
    initially = {g["artist"]: g for g in store.list()}
    for band in ("Band A", "Band B", "Band C"):
        assert initially[band]["show_type"] == "concert"
    records.append({"artist": "Band F", "date": "2025-06-11", "venue": "Open Air Park"})
    assert store.import_rows(records) == 6
    by_artist = {g["artist"]: g for g in store.list()}
    for band in ("Band A", "Band B", "Band C", "Band F"):
        assert by_artist[band]["show_type"] == "festival"
        assert by_artist[band]["type_override"] == 0
    assert by_artist["Band D"]["show_type"] == "concert"
    assert by_artist["Band E"]["show_type"] == "festival"
    assert by_artist["Band E"]["type_override"] == 1
    assert store.list("Band A")[0]["show_type"] == "festival"

    # A user's correction stays authoritative even after importing again.
    store.classify(by_artist["Band A"]["id"], "concert")
    store.import_rows(records)
    updated = {g["artist"]: g for g in store.list()}
    assert updated["Band A"]["show_type"] == "concert"
    assert updated["Band E"]["festival"] == "Explicit Festival"
    assert updated["Band E"]["type_override"] == 1
    assert updated["Band B"]["show_type"] == "festival"


def test_gig_page_loads_external_script_under_csp(tmp_path):
    from analytics.web import create_app

    app = create_app(tmp_path, config={}, development=True, start_worker=False)
    client = app.test_client()
    response = client.get("/gigs")
    assert response.status_code == 200
    assert b'<script src="static/gigs.js" defer></script>' in response.data
    assert b"<script>" not in response.data
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert client.get("/static/gigs.js").status_code == 200
    app.extensions["gig_worker"].close()
