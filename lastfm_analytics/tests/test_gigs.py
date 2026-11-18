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
