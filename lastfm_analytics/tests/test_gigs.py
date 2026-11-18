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
              "setlist_daily_request_budget": 10, "setlistfm_api_key": ""}
    store = GigStore(db, config)
    rows = [{"artist": "Muse", "date": "2025-06-15", "venue": "Wembley",
             "festival": "", "setlist_id": "abc123"},
            {"artist": "Muse", "date": "2024-06-15", "festival": "Glastonbury"}]
    assert store.import_rows(rows) == 2
    assert store.import_rows(rows) == 2
    assert len(store.list()) == 2
    assert store.list("Muse")[0]["date"] == "2025-06-15"
    assert store.refresh()["processed"] == 0


def test_infer_festival_same_day_and_consecutive_dates(tmp_path):
    db = Database(tmp_path / "festivals.sqlite3")
    store = GigStore(db, {
        "timezone": "Europe/London", "setlist_cache_retention_days": 30,
        "setlist_daily_request_budget": 10, "setlistfm_api_key": ""
    })
    gigs = [
        {"artist": "Artist 1", "date": "2025-07-01", "venue": "Festival Park"},
        {"artist": "Artist 2", "date": "2025-07-01", "venue": "festival  park"},
        {"artist": "Artist 3", "date": "2025-07-01", "venue": "FESTIVAL PARK"},
        {"artist": "Artist 4", "date": "2025-08-09", "venue": "Outdoor Arena"},
        {"artist": "Artist 5", "date": "2025-08-10", "venue": "Outdoor Arena"},
        {"artist": "Artist 6", "date": "2025-08-10", "venue": "Outdoor Arena"},
        {"artist": "Artist 7", "date": "2025-09-01", "venue": "Local Bar"},
        {"artist": "Artist 8", "date": "2025-09-03", "venue": "Local Bar"},
        {"artist": "Artist 9", "date": "2025-09-03", "venue": "Local Bar"},
        {"artist": "Artist 10", "date": "2025-10-01", "venue": "The Hall"},
        {"artist": "Artist 11", "date": "2025-10-01", "venue": "Different Hall"},
        {"artist": "Artist 12", "date": "2025-10-01", "venue": "Another Hall"},
        {"artist": "Artist 13", "date": "2025-11-01", "venue": ""},
        {"artist": "Artist 14", "date": "2025-11-01", "venue": ""},
        {"artist": "Artist 15", "date": "2025-11-01", "venue": ""},
        {"artist": "Artist 16", "date": "2025-12-01", "venue": "Festival Field", "festival": "Confirmed Fest"},
    ]
    store.import_rows(gigs)
    indexed = {r["artist"]: r for r in store.list()}
    for name in ("Artist 1", "Artist 2", "Artist 3",
                 "Artist 4", "Artist 5", "Artist 6"):
        assert indexed[name]["is_festival"]
        assert indexed[name]["festival_inferred"]
        assert indexed[name]["festival"] == ""
    for name in ("Artist 7", "Artist 8", "Artist 9", "Artist 10",
                 "Artist 11", "Artist 12", "Artist 13", "Artist 14", "Artist 15"):
        assert indexed[name]["is_festival"] is False
        assert indexed[name]["festival_inferred"] is False
    assert indexed["Artist 16"]["is_festival"] is True
    assert indexed["Artist 16"]["festival_inferred"] is False
    assert indexed["Artist 16"]["festival"] == "Confirmed Fest"

    # An individual artist's timeline must see the surrounding performers.
    solo = store.list("Artist 1")
    assert len(solo) == 1 and solo[0]["festival_inferred"]
    assert store.timeline("Artist 4")["gigs"][0]["festival_inferred"]

    # No inferred festival name is saved; classification is reversible.
    with db.connect() as conn:
        assert conn.execute(
            "SELECT festival FROM gigs WHERE artist='Artist 1'"
        ).fetchone()[0] == ""
