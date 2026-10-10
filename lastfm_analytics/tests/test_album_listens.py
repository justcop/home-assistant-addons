"""Estimated album listens: complete tracklists, period/source scopes and ranking."""
import json
from collections import Counter
from datetime import datetime, timezone

from analytics.album_listens import (
    TracklistWorker, album_key, estimate, track_breakdown, track_key,
)
from analytics.db import Database
from analytics.insights import rankings, overview, scope


def play(ts, artist, album, title):
    return {"ts": ts, "artist": artist, "album": album, "title": title, "raw": {}}


def period(start=0, end=5000):
    return {"start": start, "end": end, "previous_start": 0, "compare": False}


def add_tracks(db, album, tracks, source="MusicBrainz"):
    with db.connect() as conn:
        album_id = conn.execute(
            "SELECT av.group_id FROM resolved_variants av "
            "JOIN scrobbles s ON s.album_id=av.id WHERE s.album=? LIMIT 1",
            (album,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO album_tracklists(album_id,tracks_json,source,expires) VALUES (?,?,?,?)",
            (album_id, json.dumps(tracks), source, 9999999999),
        )


def test_third_least_count_includes_unplayed_tracks_and_requires_six(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    tracks = ["A", "B", "C", "D", "E", "F"]
    rows = []
    ts = 100
    for track, count in zip(tracks, (0, 0, 2, 3, 4, 5)):
        for _ in range(count):
            rows.append(play(ts, "Example", "Long Record", track))
            ts += 1
    db.apply_window(0, 5000, rows)
    with db.connect() as conn:
        before = rankings(conn, period(), "album")
        assert before[0]["estimated_listens"] is None
        assert before[0]["estimate_status"] == "pending"
    add_tracks(db, "Long Record", tracks)
    with db.connect() as conn:
        result = rankings(conn, period(), "album", include_breakdown=True)[0]
    assert result["plays"] == 14
    assert result["estimated_listens"] == 2
    assert result["track_count"] == 6
    assert [t["plays"] for t in result["track_breakdown"]] == [0, 0, 2, 3, 4, 5]
    assert result["tracklist_source"] == "MusicBrainz"
    assert estimate(tracks[:5], {}) is None
    assert estimate(tracks, Counter()) == 0
    assert track_key("Hello (2009 Remaster)") == track_key("Hello")
    assert track_key("Hello (Live)") != track_key("Hello")
    assert album_key("Revolver (Deluxe Edition)") == album_key("Revolver")


def test_album_ranks_estimates_without_favouring_long_popular_singles(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    rows = []
    for i in range(100):
        rows.append(play(i + 100, "Example", "Singles Heavy", "Hit One" if i % 2 else "Hit Two"))
    for i, title in enumerate("ABCDEF"):
        for n in range(3):
            rows.append(play(300 + i * 3 + n, "Example", "Whole Album", title))
    db.apply_window(0, 5000, rows)
    add_tracks(db, "Singles Heavy", ["Hit One", "Hit Two", "C", "D", "E", "F"])
    add_tracks(db, "Whole Album", list("ABCDEF"))
    with db.connect() as conn:
        plays = rankings(conn, period(), "album")
        listens = rankings(conn, period(), "album", sort="estimated")
    assert [r["name"] for r in plays] == ["Singles Heavy", "Whole Album"]
    assert [r["name"] for r in listens] == ["Whole Album", "Singles Heavy"]
    assert [r["estimated_listens"] for r in listens] == [3, 0]
    assert rankings is not None  # Explicit mode does not alter raw scrobbles.


def test_album_with_fewer_than_six_canonical_tracks_is_ineligible(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    db.apply_window(0, 5000, [play(100, "Example", "EP", "A")])
    add_tracks(db, "EP", ["A", "B", "C", "D", "E"])
    with db.connect() as conn:
        album = rankings(conn, period(), "album")[0]
    assert album["estimated_listens"] is None
    assert album["estimate_status"] == "ineligible"


def test_date_and_vinyl_filters_apply_to_track_counts(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    tracks = list("ABCDEF")
    rows = []
    for i, track in enumerate(tracks):
        rows.extend([
            play(100 + i, "Example", "Album", track),
            play(1000 + i, "Example", "Album", track),
        ])
    db.apply_window(0, 5000, rows)
    add_tracks(db, "Album", tracks)
    db.record_source("Justin", {
        "username": "Justin", "source": "vinyl", "timestamp": 100,
        "artist": "Example", "title": "A",
    })
    with db.connect() as conn:
        full = rankings(conn, period(), "album")[0]
        early = rankings(conn, period(0, 500), "album")[0]
        vinyl_extra, vinyl_params = scope({"source": "vinyl"})
        vinyl = rankings(conn, period(), "album", extra=vinyl_extra,
                         params=vinyl_params)[0]
    assert full["estimated_listens"] == 2
    assert early["estimated_listens"] == 1
    assert vinyl["estimated_listens"] == 0 and vinyl["plays"] == 1


def test_artist_and_album_overview_display_estimates(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    rows = [play(100 + i, "Example", "First", name) for i, name in enumerate("ABCDEF")]
    rows += [play(200 + i, "Another", "Second", name) for i, name in enumerate("ABCDEF")]
    db.apply_window(0, 5000, rows)
    add_tracks(db, "First", list("ABCDEF"))
    add_tracks(db, "Second", list("ABCDEF"))
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    with db.connect() as conn:
        artist_id = conn.execute(
            "SELECT artist_group_key FROM scrobbles WHERE artist='Example' LIMIT 1"
        ).fetchone()[0]
        album_id = conn.execute(
            "SELECT av.group_id FROM scrobbles s "
            "JOIN resolved_variants av ON av.id=s.album_id "
            "WHERE s.album='First' LIMIT 1"
        ).fetchone()[0]
    globally = overview(db, {"period": "all"}, "Europe/London", now)
    artist = overview(db, {"period": "all", "entity": "artist", "id": artist_id},
                      "Europe/London", now)
    album = overview(db, {"period": "all", "entity": "album", "id": str(album_id)},
                     "Europe/London", now)
    assert len(globally["top_albums_estimated"]) == 2
    assert len(artist["top_albums_estimated"]) == 1
    assert artist["top_albums_estimated"][0]["name"] == "First"
    assert album["album_estimate"]["estimated_listens"] == 1
    assert [r["plays"] for r in album["album_estimate"]["track_breakdown"]] == [1] * 6


def test_musicbrainz_chooses_standard_audio_release_not_deluxe():
    worker = TracklistWorker(enabled=False)
    canonical_id = "11111111-1111-1111-1111-111111111111"
    deluxe_id = "22222222-2222-2222-2222-222222222222"
    group_id = "33333333-3333-3333-3333-333333333333"
    requests = []

    def fake_mb(entity, **params):
        requests.append((entity, params))
        if entity == "release-group":
            return {"release-groups": [{
                "id": group_id, "title": "Revolver", "primary-type": "Album",
                "artist-credit": [{"name": "The Beatles"}],
                "first-release-date": "1966-08-05",
            }]}
        if entity == "release":
            return {"releases": [
                {"id": deluxe_id, "title": "Revolver (Deluxe Edition)",
                 "country": "GB", "date": "2022", "media": [{"format": "CD"}]},
                {"id": canonical_id, "title": "Revolver",
                 "country": "GB", "date": "1966", "media": [{"format": "CD"}]},
            ]}
        if entity == "release/" + canonical_id:
            return {"media": [{"position": 1, "format": "CD",
                    "tracks": [{"position": i + 1, "title": title}
                               for i, title in enumerate("ABCDEF")]}]}
        raise AssertionError("Wrong release selected: " + entity)

    worker.mb = fake_mb
    tracks, source = worker.musicbrainz("The Beatles", "Revolver")
    assert source == "MusicBrainz" and tracks == list("ABCDEF")
    assert not any(name == "release/" + deluxe_id for name, _ in requests)


def test_lastfm_metadata_needs_exact_artist_album_and_six_tracks():
    worker = TracklistWorker(api_key="test", enabled=False)
    worker.request_json = lambda url: {"album": {
        "name": "Revolver", "artist": "The Beatles",
        "tracks": {"track": [{"name": s} for s in "ABCDEF"]},
    }}
    assert worker.lastfm("The Beatles", "Revolver")[0] == list("ABCDEF")
    assert worker.lastfm("Radiohead", "Revolver") is None
    assert track_breakdown(["A", "B"], {track_key("A"): 8}) == [
        {"title": "A", "plays": 8}, {"title": "B", "plays": 0}
    ]


def test_album_tracklist_status_counts_pending_ready_short_and_unresolved(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    db.apply_window(0, 5000, [
        play(100, "Example", "Waiting", "A"),
        play(200, "Example", "Complete", "B"),
        play(300, "Example", "Too Short", "C"),
        play(400, "Example", "Not Found", "D"),
    ])
    add_tracks(db, "Complete", list("ABCDEF"))
    add_tracks(db, "Too Short", list("ABCDE"))
    with db.connect() as conn:
        album_id = conn.execute(
            "SELECT av.group_id FROM resolved_variants av "
            "JOIN scrobbles s ON s.album_id=av.id WHERE s.album='Not Found'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO album_tracklists(album_id,tracks_json,source,expires) VALUES (?,?,?,?)",
            (album_id, None, None, 9999999999),
        )
    worker = TracklistWorker(enabled=True)
    initial = worker.status(db)
    assert {key: initial[key] for key in ("total", "ready", "ineligible",
                                          "unresolved", "pending")} == {
        "total": 4, "ready": 1, "ineligible": 1,
        "unresolved": 1, "pending": 1,
    }
    assert initial["enabled"] and initial["queued"] == 0
    waiting_key = (str(db.path), 12345)
    with worker.lock:
        worker.pending.add(waiting_key)
    queued = worker.status(db)
    assert queued["queued"] == 1 and queued["processing"] == 0
    with worker.lock:
        worker.processing = waiting_key
    working = worker.status(db)
    assert working["queued"] == 0 and working["processing"] == 1
    worker.close()


def test_album_ranking_endpoint_exposes_real_progress_even_when_cache_is_static(tmp_path):
    from analytics.web import create_app

    app = create_app(tmp_path, config={"username": "example", "api_key": "fixture"},
                     development=True, start_worker=False)
    db = app.extensions["database"]
    try:
        db.apply_window(0, 5000, [play(100, "Example", "Unprocessed", "A")])
        client = app.test_client()
        response = client.get("/api/rankings?kind=album&period=all&album_sort=estimated")
        assert response.status_code == 200
        body = response.get_json()
        assert len(body["rows"]) == 1
        assert body["rows"][0]["estimated_listens"] is None
        assert body["album_progress"]["total"] == 1
        assert body["album_progress"]["pending"] == 1
        assert body["album_progress"]["ready"] == 0
        assert body["album_progress"]["enabled"] is False

        # The progress is live even if rankings are already stored in cache.
        add_tracks(db, "Unprocessed", list("ABCDEF"))
        response = client.get("/api/rankings?kind=album&period=all&album_sort=estimated")
        assert response.status_code == 200
        assert response.get_json()["album_progress"]["ready"] == 1
    finally:
        app.extensions["tracklist_worker"].close()
        app.extensions["artwork_worker"].close()
        app.extensions["view_cache"].close()
