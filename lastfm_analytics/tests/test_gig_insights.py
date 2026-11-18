import json
from datetime import datetime, timezone

from analytics.db import Database
from analytics.gig_insights import impact_for_artist, listening_summary
from analytics.gigs import GigStore


def song(ts, title, artist="Muse"):
    return {"ts": ts, "title": title, "artist": artist, "album": "Fixture",
            "raw": {"name": title, "source": "test"}}


def timestamp(text):
    return int(datetime.fromisoformat(text + "T12:00:00+00:00").timestamp())


def create_store(tmp_path):
    db = Database(tmp_path / "gigs.sqlite3")
    store = GigStore(db, {"timezone": "Europe/London",
                          "setlist_cache_retention_days": 30,
                          "setlistfm_api_key": ""})
    store.import_rows([{"artist": "Muse", "date": "2025-06-15",
                        "venue": "Wembley", "setlist_id": "123abc"}])
    return store


def cache_setlist(store, sets, fetched_at):
    gig_id = store.list()[0]["id"]
    with store.db.connect() as conn:
        conn.execute("""INSERT INTO gig_cache (gig_id,payload,fetched_at)
                       VALUES (?,?,?)""", (gig_id, json.dumps({"sets": {"set": sets}}), fetched_at))


def test_live_songs_match_actual_scrobbles_without_mistaking_covers(tmp_path):
    store = create_store(tmp_path)
    now = int(datetime.now(timezone.utc).timestamp())
    cache_setlist(store, [{"song": [
        {"name": "Hysteria"},
        {"name": "Hysteria"},
        {"name": "Starlight"},
        {"name": "Feeling Good", "cover": {"name": "Nina Simone"}},
    ]}], now)
    plays = [song(timestamp("2025-05-25") + n, "Hysteria") for n in range(3)]
    plays += [song(timestamp("2025-07-05"), "Hysteria (2010 Remaster)"),
              song(timestamp("2025-07-06"), "Uprising"),
              song(timestamp("2025-07-07"), "Feeling Good")]
    store.db.apply_window(0, 2_000_000_000, plays)
    summary = listening_summary(store, "Muse")
    assert summary["cached_setlists"] == 1
    assert summary["cached_songs"] == 3
    matched = {x["title"]: x for x in summary["live_songs"]}
    assert matched["Hysteria"]["listens"] == 4
    assert matched["Hysteria"]["performances"] == 1
    assert matched["Feeling Good"]["cover"] is True
    assert any(x["title"] == "Uprising" for x in summary["not_in_cached_setlists"])


def test_missing_or_expired_cache_never_means_never_played_live(tmp_path):
    store = create_store(tmp_path)
    store.db.apply_window(0, 2_000_000_000, [song(timestamp("2025-07-01"), "Uprising")])
    summary = listening_summary(store, "Muse")
    assert summary["cached_setlists"] == 0
    assert summary["live_songs"] == []
    assert "unverified" in summary["coverage_note"]
    cache_setlist(store, [{"song": [{"name": "Uprising"}]}], 1)
    expired = listening_summary(store, "Muse")
    assert expired["cached_setlists"] == 0
    with store.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM gig_cache").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM gigs").fetchone()[0] == 1


def test_impact_normalises_for_overall_scrobble_volume(tmp_path):
    store = create_store(tmp_path)
    rows = [song(timestamp("2025-05-01"), "Other", "Other artist")]
    rows += [song(timestamp("2025-07-20"), "Other", "Other artist")]
    rows += [song(timestamp("2025-05-25") + i, "Hysteria") for i in range(2)]
    rows += [song(timestamp("2025-05-26") + i, "Other", "Other artist") for i in range(8)]
    rows += [song(timestamp("2025-07-02") + i, "Hysteria") for i in range(4)]
    rows += [song(timestamp("2025-07-03") + i, "Other", "Other artist") for i in range(6)]
    store.db.apply_window(0, 2_000_000_000, rows)
    impact = impact_for_artist(store, "Muse")[0]
    assert impact["available"] is True
    assert impact["artist_before"] == 2
    assert impact["artist_after"] == 4
    assert impact["share_before"] == 20.0
    assert impact["share_after"] == 40.0
    assert impact["share_change_pp"] == 20.0


def test_impact_marks_unfinished_or_missing_history_unavailable(tmp_path):
    store = create_store(tmp_path)
    store.db.apply_window(0, 2_000_000_000,
                          [song(timestamp("2025-06-10"), "Hysteria")])
    result = impact_for_artist(store, "Muse")
    assert len(result) == 1
    assert result[0]["available"] is False
    assert "history" in result[0]["reason"].lower()
