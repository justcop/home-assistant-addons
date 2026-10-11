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


def test_blackbird_uses_1968_recording_before_1994_album(tmp_path):
    """A later album must never make the original Blackbird a 1994 song."""
    db = Database(tmp_path / "original.sqlite3")
    db.apply_window(0, 5000, [
        play(100, "Blackbird", "Later Collection", "The Beatles"),
        play(101, "Blackbird", "Later Collection", "The Beatles"),
    ])
    with db.connect() as conn:
        album_id = conn.execute(
            "SELECT av.group_id FROM scrobbles s "
            "JOIN resolved_variants av ON av.id=s.album_id LIMIT 1"
        ).fetchone()[0]
        song_id = conn.execute(
            "SELECT sv.group_id FROM scrobbles s "
            "JOIN resolved_variants sv ON sv.id=s.song_id LIMIT 1"
        ).fetchone()[0]
        conn.execute("INSERT INTO release_years VALUES (?,?,?,?,?)",
                     ("album", album_id, 1994, "MusicBrainz", 9999999999))
    assert release_year_analysis(db, {"period": "all"}, "UTC")["years"] == [
        {"year": 1994, "plays": 2}
    ]
    with db.connect() as conn:
        conn.execute("INSERT INTO release_years VALUES (?,?,?,?,?)",
                     ("song", song_id, 1968, "MusicBrainz", 9999999999))
    result = release_year_analysis(db, {"period": "all"}, "UTC")
    assert result["years"] == [{"year": 1968, "plays": 2}]
    assert result["unidentified"] == 0


def test_earliest_album_year_shared_by_song_across_scrobbles_and_filters(tmp_path):
    db = Database(tmp_path / "multi-album.sqlite3")
    db.apply_window(0, 5000, [
        play(100, "Blackbird", "White Album", "The Beatles"),
        play(101, "Blackbird", "1994 Collection", "The Beatles"),
        play(102, "Blackbird", "1994 Collection", "The Beatles"),
        play(103, "Unrelated", "1994 Collection", "The Beatles"),
    ])
    with db.connect() as conn:
        albums = dict(conn.execute(
            "SELECT DISTINCT s.album,av.group_id FROM scrobbles s "
            "JOIN resolved_variants av ON av.id=s.album_id"
        ).fetchall())
        conn.executemany("INSERT INTO release_years VALUES (?,?,?,?,?)", [
            ("album", albums["White Album"], 1968, "MusicBrainz", 9999999999),
            ("album", albums["1994 Collection"], 1994, "MusicBrainz", 9999999999),
        ])
    all_years = release_year_analysis(db, {"period": "all"}, "UTC")
    assert all_years["years"] == [
        {"year": 1968, "plays": 3}, {"year": 1994, "plays": 1},
    ]
    db.record_source("Tester", {
        "username": "Tester", "source": "vinyl",
        "artist": "The Beatles", "title": "Blackbird", "timestamp": 102,
    })
    vinyl = release_year_analysis(db, {"period": "all", "source": "vinyl"}, "UTC")
    assert vinyl["years"] == [{"year": 1968, "plays": 1}]


def test_song_lookup_runs_for_albums_with_existing_release_dates(tmp_path):
    import threading
    db = Database(tmp_path / "queue.sqlite3")
    db.apply_window(0, 5000, [
        play(100, "Blackbird", "Later Collection", "The Beatles"),
    ])
    with db.connect() as conn:
        album_id = conn.execute(
            "SELECT av.group_id FROM scrobbles s "
            "JOIN resolved_variants av ON av.id=s.album_id LIMIT 1"
        ).fetchone()[0]
        conn.execute("INSERT INTO release_years VALUES (?,?,?,?,?)",
                     ("album", album_id, 1994, "MusicBrainz", 9999999999))
    worker = ReleaseYearWorker()
    # Prevent the queue's background consumer starting during this unit test.
    worker.thread = threading.Thread(target=lambda: None)
    worker.scan(db, force=True)
    jobs = []
    while not worker.jobs.empty():
        jobs.append(worker.jobs.get_nowait())
    # A matching-engine upgrade also rechecks already cached album dates;
    # the song is still queued even though an album date was already present.
    assert [(row[1], row[3], row[4]) for row in jobs] == [
        ("album", "The Beatles", "Later Collection"),
        ("song", "The Beatles", "Blackbird"),
    ]


def test_musicbrainz_recording_finds_original_blackbird_1968():
    worker = ReleaseYearWorker(enabled=False)
    def fixture(entity, **kwargs):
        assert entity == "recording"
        assert kwargs["limit"] == 100
        return {
            "recordings": [
            {"title": "Blackbird", "artist-credit": [{"name": "The Beatles"}],
             "first-release-date": "1994-01-01"},
            {"title": "Blackbird", "artist-credit": [{"name": "The Beatles"}],
             "first-release-date": "1968-11-22"},
            {"title": "Blackbird", "artist-credit": [{"name": "Different Band"}],
             "first-release-date": "1950-01-01"},
        ]
        }
    worker.mb = fixture
    assert worker.song_year("The Beatles", "Blackbird") == 1968



def test_album_top_twenty_is_based_on_album_year_not_songs_year(tmp_path):
    db = Database(tmp_path / "album-year.sqlite3")
    db.apply_window(0, 5000, [
        play(100, "Blackbird", "White Album", "The Beatles"),
        play(101, "Blackbird", "Later Collection", "The Beatles"),
        play(102, "Blackbird", "Later Collection", "The Beatles"),
        play(103, "Another Song", "Later Collection", "The Beatles"),
    ])
    with db.connect() as conn:
        albums = dict(conn.execute(
            "SELECT DISTINCT s.album,av.group_id FROM scrobbles s "
            "JOIN resolved_variants av ON av.id=s.album_id"
        ).fetchall())
        blackbird = conn.execute(
            "SELECT sv.group_id FROM scrobbles s "
            "JOIN resolved_variants sv ON sv.id=s.song_id "
            "WHERE s.title='Blackbird' LIMIT 1"
        ).fetchone()[0]
        conn.executemany("INSERT INTO release_years VALUES (?,?,?,?,?)", [
            ("song", blackbird, 1968, "MusicBrainz", 9999999999),
            ("album", albums["White Album"], 1968, "MusicBrainz", 9999999999),
            ("album", albums["Later Collection"], 1994, "MusicBrainz", 9999999999),
        ])
    early = release_year_analysis(db, {"period": "all", "release_year": "1968"}, "UTC")
    assert early["top_songs"][0]["name"] == "Blackbird"
    assert early["top_songs"][0]["plays"] == 3
    assert [(a["name"], a["plays"]) for a in early["top_albums"]] == [
        ("White Album", 1),
    ]
    late = release_year_analysis(db, {"period": "all", "release_year": "1994"}, "UTC")
    assert [(a["name"], a["plays"]) for a in late["top_albums"]] == [
        ("Later Collection", 3),
    ]
    db.record_source("Tester", {
        "username": "Tester", "source": "vinyl", "artist": "The Beatles",
        "title": "Blackbird", "timestamp": 101,
    })
    filtered = release_year_analysis(
        db, {"period": "all", "source": "vinyl", "release_year": "1994"}, "UTC"
    )
    assert [(a["name"], a["plays"]) for a in filtered["top_albums"]] == [
        ("Later Collection", 1),
    ]


def test_release_metadata_progress_counts_all_songs_not_just_queued(tmp_path):
    db = Database(tmp_path / "progress.sqlite3")
    db.apply_window(0, 5000, [
        play(100, "A"), play(101, "A"), play(102, "B"),
        play(103, "C", "Second"),
    ])
    worker = ReleaseYearWorker(enabled=True)
    baseline = worker.status(db)
    assert baseline["songs"]["total"] == 3
    assert baseline["albums"]["total"] == 2
    assert baseline["songs"]["pending"] == 3
    assert not baseline["songs"]["complete"]
    with db.connect() as conn:
        ids = list(conn.execute(
            "SELECT DISTINCT sv.group_id FROM scrobbles s "
            "JOIN resolved_variants sv ON sv.id=s.song_id ORDER BY sv.group_id"
        ))
        albums = list(conn.execute(
            "SELECT DISTINCT av.group_id FROM scrobbles s "
            "JOIN resolved_variants av ON av.id=s.album_id ORDER BY av.group_id"
        ))
        conn.executemany("INSERT INTO release_years VALUES (?,?,?,?,?)", [
            ("song", ids[0][0], 1973, "MusicBrainz", 9999999999),
            ("song", ids[1][0], None, None, 9999999999),
            ("song", ids[2][0], 1968, "MusicBrainz", 1),  # expired
            ("album", albums[0][0], 1973, "MusicBrainz", 9999999999),
        ])
    halfway = worker.status(db)
    assert halfway["songs"]["checked"] == 2
    assert halfway["songs"]["matched"] == 1
    assert halfway["songs"]["unresolved"] == 1
    assert halfway["songs"]["pending"] == 1
    assert not halfway["songs"]["complete"]
    assert halfway["albums"]["checked"] == 1
    with db.connect() as conn:
        conn.execute(
            "UPDATE release_years SET expires=9999999999 WHERE kind='song' AND group_id=?",
            (ids[2][0],),
        )
    finished = worker.status(db)
    assert finished["songs"]["complete"] is True
    assert finished["songs"]["checked"] == 3
    assert finished["songs"]["matched"] == 2
    assert finished["songs"]["unresolved"] == 1
    assert finished["albums"]["complete"] is False
    assert finished["albums"]["pending"] == 1
    # A new song makes the previous "complete" status false again.
    db.apply_window(0, 5000, [play(104, "D", "Second")])
    assert not worker.status(db)["songs"]["complete"]



def test_upgrade_rechecks_legacy_song_dates_before_marking_complete(tmp_path):
    import threading
    db = Database(tmp_path / "legacy.sqlite3")
    db.apply_window(0, 5000, [play(100, "Blackbird", "Later Collection", "The Beatles")])
    with db.connect() as conn:
        song_id = conn.execute(
            "SELECT sv.group_id FROM scrobbles s "
            "JOIN resolved_variants sv ON sv.id=s.song_id LIMIT 1"
        ).fetchone()[0]
        album_id = conn.execute(
            "SELECT av.group_id FROM scrobbles s "
            "JOIN resolved_variants av ON av.id=s.album_id LIMIT 1"
        ).fetchone()[0]
        conn.executemany("INSERT INTO release_years VALUES (?,?,?,?,?)", [
            ("song", song_id, 1994, "MusicBrainz", 9999999999),
            ("album", album_id, 1994, "MusicBrainz", 9999999999),
        ])
        # Isolate the song migration: this album already uses current lookup.
        db.put(conn, "album_lookup_version", 2)
    worker = ReleaseYearWorker()
    assert worker.status(db)["songs"]["complete"]
    worker.thread = threading.Thread(target=lambda: None)
    worker.scan(db, force=True)
    with db.connect() as conn:
        old = conn.execute(
            "SELECT expires FROM release_years WHERE kind='song'"
        ).fetchone()[0]
        album_expiry = conn.execute(
            "SELECT expires FROM release_years WHERE kind='album'"
        ).fetchone()[0]
        assert old == 0
        assert album_expiry == 9999999999
        assert db.get(conn, "recording_lookup_version") == 2
    progress = worker.status(db)
    assert not progress["songs"]["complete"]
    assert progress["songs"]["pending"] == 1
    assert progress["albums"]["complete"]
    # Expired song metadata is not considered a reliable recording match
    # while the new lookup runs, but an existing album date may still be shown.
    assert release_year_analysis(db, {"period": "all"}, "UTC")["years"] == [
        {"year": 1994, "plays": 1}
    ]



def test_album_year_prefers_original_release_group_beyond_first_twelve():
    worker = ReleaseYearWorker(enabled=False)
    # The original release group can be low in MusicBrainz's relevance-ranked
    # search results, behind more recent same-title editions.
    seen = []
    def fixture(entity, **kwargs):
        seen.append((entity, kwargs["limit"]))
        newer = [
            {"title": "The Beatles", "artist-credit": [{"name": "The Beatles"}],
             "primary-type": "Album", "secondary-types": [],
             "first-release-date": "1994-01-01"}
            for _ in range(14)
        ]
        older = {
            "title": "The Beatles", "disambiguation": "The White Album",
            "artist-credit": [{"name": "The Beatles"}],
            "primary-type": "Album", "secondary-types": [],
            "first-release-date": "1968-11-22",
        }
        return {"release-groups": newer + [older]}
    worker.mb = fixture
    assert worker.album_year("The Beatles", "The Beatles") == 1968
    assert worker.album_year("The Beatles", "The Beatles (White Album)") == 1968
    assert all(kind == "release-group" and limit == 100 for kind, limit in seen)
    group = {"title": "The Beatles", "disambiguation": "The White Album"}
    assert worker.same_album("The Beatles (White Album)", group)
    assert worker.same_album("White Album", group)
    assert not worker.same_album("Different Record (White Album)", group)
    assert not worker.same_album("White Album", {"title": "The Beatles"})


def test_album_lookup_upgrade_rechecks_incorrectly_cached_1994(tmp_path):
    import threading
    from analytics.release_years import ALBUM_LOOKUP_VERSION
    db = Database(tmp_path / "old-album-year.sqlite3")
    db.apply_window(0, 5000, [
        play(100, "Blackbird", "The Beatles", "The Beatles"),
    ])
    with db.connect() as conn:
        album_id = conn.execute(
            "SELECT av.group_id FROM scrobbles s "
            "JOIN resolved_variants av ON av.id=s.album_id LIMIT 1"
        ).fetchone()[0]
        conn.execute("INSERT INTO release_years VALUES (?,?,?,?,?)",
                     ("album", album_id, 1994, "MusicBrainz", 9999999999))
        db.put(conn, "recording_lookup_version", 2)

    worker = ReleaseYearWorker()
    worker.thread = threading.Thread(target=lambda: None)
    worker.scan(db, force=True)
    status = worker.status(db)
    assert not status["albums"]["complete"]
    assert status["albums"]["pending"] == 1
    with db.connect() as conn:
        old_expiry = conn.execute(
            "SELECT expires FROM release_years WHERE kind='album'"
        ).fetchone()[0]
        assert old_expiry == 0
        assert db.get(conn, "album_lookup_version") == ALBUM_LOOKUP_VERSION
    # Simulate the corrected MusicBrainz search completing.
    with db.connect() as conn:
        conn.execute(
            "UPDATE release_years SET year=1968, expires=9999999999 "
            "WHERE kind='album' AND group_id=?", (album_id,))
    result = release_year_analysis(db, {"period": "all", "release_year": "1968"}, "UTC")
    assert result["top_albums"][0]["name"] == "The Beatles"
    assert result["top_albums"][0]["plays"] == 1
    # The test disables the background worker; simulate its normally executed
    # finally block removing the completed album from the pending queue.
    with worker.lock:
        worker.pending.discard((str(db.path), "album", album_id))
    assert worker.status(db)["albums"]["complete"]
