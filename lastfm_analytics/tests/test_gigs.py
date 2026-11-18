import io
import json
from datetime import date, datetime, timezone

import pytest

from analytics.db import Database
from analytics.gigs import GigService, extract_id, import_row, next_due


def service(tmp_path, **config):
    opts = {"setlist_cache_retention_days": 7,
            "setlist_daily_request_budget": 10,
            "setlist_fm_api_key": ""}
    opts.update(config)
    return GigService(Database(tmp_path / "gigs.sqlite3"), opts, enabled=False)


def test_anniversaries_never_drift():
    assert next_due("2023-06-15", None, date(2026, 11, 18)) == date(2026, 11, 18)
    checked = datetime(2026, 6, 20, tzinfo=timezone.utc).timestamp()
    assert next_due("2023-06-15", checked, date(2026, 11, 18)) == date(2027, 6, 15)
    checked = datetime(2026, 6, 14, tzinfo=timezone.utc).timestamp()
    assert next_due("2023-06-15", checked, date(2026, 11, 18)) == date(2026, 6, 15)


def test_leap_year_festival_anniversary():
    checked = datetime(2024, 12, 31, tzinfo=timezone.utc).timestamp()
    assert next_due("2024-02-29", checked, date(2025, 2, 28)) == date(2025, 2, 28)
    checked = datetime(2025, 3, 2, tzinfo=timezone.utc).timestamp()
    assert next_due("2024-02-29", checked, date(2026, 11, 18)) == date(2026, 2, 28)


def test_import_validation_and_idempotence(tmp_path):
    svc = service(tmp_path)
    url = "https://www.setlist.fm/setlist/oasis/2025/venue-manchester-england-63e50a1b.html"
    assert extract_id(url) == "63e50a1b"
    gig = {"Artist": "Oasis", "Date": "15/06/2025",
           "Venue": "Manchester", "type": "concert", "Setlist URL": url}
    svc.import_records([gig, gig])
    rows = svc.list_gigs()
    assert len(rows) == 1 and rows[0]["setlist_id"] == "63e50a1b"
    assert rows[0]["event_type"] == "concert"
    svc.import_records([{"artist": "Muse", "date": "2024-06-28",
                         "festival": "Glastonbury", "type": "festival"}])
    assert svc.list_gigs()[0]["festival"] or svc.list_gigs()[1]["festival"]
    with pytest.raises(ValueError):
        import_row({"artist": "Muse", "date": "2025-05-01",
                    "setlist_url": "https://evil.example/secret"})


def test_api_budget_setlist_cache_expiry_and_immutable_attendance(tmp_path):
    today = datetime.now(timezone.utc).date()
    stamp = int(datetime(today.year, today.month, today.day, 12,
                         tzinfo=timezone.utc).timestamp())
    concert = today.replace(year=today.year - 3)
    svc = service(tmp_path, setlist_fm_api_key="fake", setlist_daily_request_budget=1)
    svc.import_records([
        {"artist": "Radiohead", "date": concert.isoformat(), "venue": "Test",
         "setlist_id": "1234abcd"},
        {"artist": "The Beatles", "date": concert.isoformat(), "venue": "Another",
         "setlist_id": "2345abcd"}])
    payload = {"id": "1234abcd", "artist": {"name": "Radiohead"},
               "eventDate": concert.strftime("%d-%m-%Y"),
               "url": "https://www.setlist.fm/setlist/radiohead/2020/test-1234abcd.html",
               "versionId": "v1", "sets": {"set": [{"song": [{"name": "Creep"}]}]}}
    called = []
    def opener(req, timeout):
        called.append((req.full_url, req.get_header("X-api-key")))
        return io.BytesIO(json.dumps(payload).encode())
    svc.opener = opener
    assert svc.run_one(stamp) is True
    assert svc.run_one(stamp + 30) is False
    assert len(called) == 1
    with svc.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gig_setlist_cache").fetchone()[0] == 1
    svc.purge(stamp + 8 * 86400)
    with svc.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gig_setlist_cache").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM gigs").fetchone()[0] == 2
    svc.close()


def test_gig_events_link_artist_identity(tmp_path):
    svc = service(tmp_path)
    svc.import_records([{"artist": "The Beatles", "date": "1969-01-30",
                         "venue": "Apple Corps", "type": "concert"}])
    with svc.db.connect() as conn:
        conn.execute("""INSERT INTO artist_aliases(artist_key,canonical_key,display_name)
                     VALUES (?,?,?)""", ("beatles", "beatles", "The Beatles"))
    events = svc.events_for_artist("beatles")
    assert len(events) == 1 and events[0]["event_date"] == "1969-01-30"
    assert svc.events_for_artist("radiohead") == []
