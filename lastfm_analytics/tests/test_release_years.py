"""Release years are music metadata, not scrobble timestamps or compositions."""
from analytics.db import Database
from analytics.insights import release_year_analysis
from analytics.release_years import ReleaseYearWorker, release_year
from analytics.web import create_app


def play(ts, title, album="Album One", artist="Test Artist"):
    return {"ts": ts, "title": title, "album": album, "artist": artist, "raw": {}}


def catalogue(db):
    with db.connect() as conn:
        album_ids = dict(conn.execute(
            """SELECT DISTINCT s.album,av.group_id
               FROM scrobbles s JOIN resolved_variants av ON av.id=s.album_id"""
        ).fetchall())
        no_album_song = conn.execute(
            """SELECT sv.group_id FROM scrobbles s
               JOIN resolved_variants sv ON sv.id=s.song_id
               WHERE s.album='' LIMIT 1"""
        ).fetchone()[0]
        conn.executemany(
            "INSERT INTO release_years(kind,group_id,year,source,expires) VALUES (?,?,?,?,?)",
            [
                ("album", album_ids["Album One"], 1973, "MusicBrainz", 9999999999),
                ("album", album_ids["Album Two"], 1997, "MusicBrainz", 9999999999),
                ("song", no_album_song, 1969, "MusicBrainz", 9999999999),
            ],
        )


def test_repeated_scrobbles_weight_release_year_and_missing_is_not_a_year(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.apply_window(0, 5000, [
        play(100, "A"), play(101, "A"), play(102, "B"),
        play(103, "C", "Album Two"),
        play(104, "Single", ""), play(105, "Single", ""),
        play(106, "Unknown", "No Known Release"),
    ])
    catalogue(db)
    full = release_year_analysis(db, {"period": "all"}, "UTC")
    assert {entry["year"]: entry["plays"] for entry in full["years"]} == {
        1969: 2, 1973: 3, 1997: 1,
    }
    assert full["identified"] == 6
    assert full["unidentified"] == 1
    assert full["total"] == 7
    assert release_year_analysis(db, {"period": "all", "release_year": "1973"}, "UTC")[
        "top_songs"
    ][0]["plays"] == 2


def test_period_and_vinyl_filter_scrobble_time_not_release_time(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.apply_window(0, 5000, [play(100, "A"), play(101, "A"), play(102, "C", "Album Two")])
    # Use a dedicated album row for the first and second recorded years.
    with db.connect() as conn:
        ids = dict(conn.execute(
            "SELECT DISTINCT s.album,av.group_id FROM scrobbles s "
            "JOIN resolved_variants av ON av.id=s.album_id"
        ).fetchall())
        conn.execute(
            "INSERT INTO release_years VALUES (?,?,?,?,?)",
            ("album", ids["Album One"], 1973, "MusicBrainz", 9999999999),
        )
        conn.execute(
            "INSERT INTO release_years VALUES (?,?,?,?,?)",
            ("album", ids["Album Two"], 1997, "MusicBrainz", 9999999999),
        )
    db.record_source("Tester", {
        "username": "Tester", "source": "vinyl",
        "artist": "Test Artist", "title": "A", "timestamp": 100,
    })
    all_plays = release_year_analysis(db, {"period": "all"}, "UTC")
    vinyl = release_year_analysis(db, {"period": "all", "source": "vinyl"}, "UTC")
    assert all_plays["identified"] == 3
    assert vinyl["identified"] == 1
    assert vinyl["years"] == [{"year": 1973, "plays": 1}]
    assert release_year_analysis(db, {"period": "custom", "start": "1970-01-01", "end": "1970-01-01"}, "UTC")["total"] == 3


def test_artist_title_matching_excludes_compilations_and_undated_versions():
    worker = ReleaseYearWorker(enabled=False)
    worker.mb = lambda entity, **kwargs: {
        "release-groups": [
            {"title": "Album One", "primary-type": "Album",
             "secondary-types": ["Compilation"], "first-release-date": "1960-01-01",
             "artist-credit": [{"name": "Test Artist"}]},
            {"title": "Album One", "primary-type": "Album",
             "secondary-types": [], "first-release-date": "1973-03",
             "artist-credit": [{"name": "Test Artist"}]},
            {"title": "Album One", "primary-type": "Album",
             "secondary-types": [], "first-release-date": "1950-01-01",
             "artist-credit": [{"name": "Different Artist"}]},
        ]
    }
    assert worker.album_year("Test Artist", "Album One") == 1973
    assert worker.album_year("Test Artist", "Something Else") is None
    assert release_year("1720-01-01") is None
    assert release_year("1973-03") == 1973
    assert release_year("Unknown") is None


def test_http_endpoint_can_return_cached_unidentified_counts(tmp_path):
    app = create_app(tmp_path, config={"username": "Tester"}, development=True, start_worker=False)
    db = app.extensions["database"]
    db.apply_window(0, 5000, [play(100, "A"), play(101, "A")])
    client = app.test_client()
    response = client.get("/api/release-years?period=all")
    assert response.status_code == 200
    data = response.get_json()
    assert data["total"] == 2
    assert data["identified"] == 0
    assert data["unidentified"] == 2
    assert data["lookup"]["enabled"] is False
