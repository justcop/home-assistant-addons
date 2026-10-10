"""Artist identity and migration regressions."""
import json
from unittest.mock import patch
from analytics.db import Database
from analytics.grouping import normalise
from analytics.insights import rankings, overview


def play(ts, artist, title, album="Ágætis byrjun"):
    return {"ts": ts, "artist": artist, "title": title, "album": album,
            "raw": {"name": title, "mbid": "track-mbid", "image": [
                {"size": "large", "#text": "https://lastfm.freetls.fastly.net/a"}]}}


def p():
    return {"start": 0, "end": 5000, "previous_start": 0, "compare": False}


def test_artist_name_variants_merge_when_they_share_recordings(tmp_path):
    from analytics.grouping import artist_suggestion_key
    from analytics.web import create_app
    assert artist_suggestion_key("Courteeners") == artist_suggestion_key("The Courteeners")
    assert artist_suggestion_key("Sigur Rós") == artist_suggestion_key("Sigur Ros")
    assert artist_suggestion_key("The National") != artist_suggestion_key("National Orchestra")
    app = create_app(tmp_path, config={"username":"user","api_key":"fixture"},
                     development=True, start_worker=False)
    db = app.extensions["database"]
    try:
        db.apply_window(0, 1000, [
            play(100, "Courteeners", "Not Nineteen Forever", "St Jude"),
            play(200, "The Courteeners", "Not Nineteen Forever", "St Jude"),
        ])
        with db.connect() as conn:
            result = rankings(conn, p(), "artist")
            assert len(result) == 1 and result[0]["plays"] == 2
            assert conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0] == 2
            assert len(set(r[0] for r in conn.execute(
                "SELECT group_id FROM resolved_variants WHERE kind='song'"))) == 1
            assert len(set(r[0] for r in conn.execute(
                "SELECT group_id FROM resolved_variants WHERE kind='album'"))) == 1
        review = app.test_client().get("/api/artists-review").json
        assert not any(set(item["names"]) == {"Courteeners", "The Courteeners"}
                       for item in review["suggestions"])
    finally:
        app.extensions["artwork_worker"].close()
        app.extensions["view_cache"].close()


def test_artist_merge_combines_exact_song_album_and_undo(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    first = "Sigur Rós"
    second = "Sigur Ros"
    # Exercise the still-supported explicit merge path independently of auto-merge.
    with patch.object(db, "auto_merge_artists"):
        db.apply_window(0, 5000, [
            play(100, first, "Hoppípolla"),
            play(200, second, "Hoppípolla"),
            play(300, first, "Glósóli"),
        ])
    with db.connect() as conn:
        assert len(rankings(conn, p(), "artist")) == 2
        assert len({r[0] for r in conn.execute(
            "SELECT group_id FROM resolved_variants WHERE kind='song' AND name='Hoppípolla'"
        )}) == 2
    db.change_artists([normalise(first), normalise(second)], first)
    with db.connect() as conn:
        artists = rankings(conn, p(), "artist")
        assert len(artists) == 1
        assert artists[0]["name"] == first and artists[0]["plays"] == 3
        assert len({r[0] for r in conn.execute(
            "SELECT group_id FROM resolved_variants WHERE kind='song' AND name='Hoppípolla'"
        )}) == 1
        assert len({r[0] for r in conn.execute(
            "SELECT group_id FROM resolved_variants WHERE kind='album'"
        )}) == 1
        assert len({r[0] for r in conn.execute(
            "SELECT artist FROM scrobbles WHERE title='Hoppípolla'"
        )}) == 2
    # Songs introduced after the merge are also grouped together.
    db.apply_window(500, 800, [
        play(600, first, "New song"),
        play(700, second, "New song"),
    ])
    with db.connect() as conn:
        assert len({r[0] for r in conn.execute(
            "SELECT group_id FROM resolved_variants WHERE kind='song' AND name='New song'"
        )}) == 1
    db.undo_grouping()
    with db.connect() as conn:
        assert len(rankings(conn, p(), "artist")) == 2
        assert len({r[0] for r in conn.execute(
            "SELECT group_id FROM resolved_variants WHERE kind='song' AND name='Hoppípolla'"
        )}) == 2
        assert len({r[0] for r in conn.execute(
            "SELECT group_id FROM resolved_variants WHERE kind='song' AND name='New song'"
        )}) == 2


def test_manually_separated_song_is_not_auto_merged(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    with patch.object(db, "auto_merge_artists"):
        db.apply_window(0, 1000, [
            play(100, "Sigur Rós", "Hoppípolla"),
            play(200, "Sigur Ros", "Hoppípolla"),
        ])
    with db.connect() as conn:
        selected = conn.execute(
            "SELECT id FROM variants WHERE artist='Sigur Ros' AND kind='song'"
        ).fetchone()[0]
    db.change_groups("separate", [selected])
    db.change_artists([normalise("Sigur Rós"), normalise("Sigur Ros")])
    with db.connect() as conn:
        assert len({r[0] for r in conn.execute(
            "SELECT group_id FROM resolved_variants WHERE kind='song' AND name='Hoppípolla'"
        )}) == 2


def test_unchanged_sync_and_duplicate_vinyl_do_not_invalidate(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    row = play(100, "Sigur Rós", "Hoppípolla")
    db.apply_window(0, 1000, [row])
    revision = db.meta("analysis_revision")
    db.apply_window(0, 1000, [row], reconcile=True)
    assert db.meta("analysis_revision") == revision
    report = {"username": "Justin", "source": "vinyl",
              "timestamp": 100, "artist": "Sigur Rós", "title": "Hoppípolla"}
    db.record_source("Justin", report)
    revision = db.meta("analysis_revision")
    db.record_source("Justin", report)
    assert db.meta("analysis_revision") == revision


def test_v2_upgrade_makes_backup_and_preserves_data(tmp_path):
    path = tmp_path / "listening.sqlite3"
    db = Database(path)
    row = play(100, "Sigur Rós", "Hoppípolla")
    db.apply_window(0, 1000, [row])
    with db.connect() as conn:
        conn.execute("PRAGMA user_version=2")
        conn.execute("UPDATE scrobbles SET raw_json=?", (
            json.dumps({"unused": "x" * 2000, "mbid": "recording-id",
                        "image": row["raw"]["image"]}),))
    Database(path)
    assert (tmp_path / "listening.before-v3.sqlite3").exists()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        raw = json.loads(conn.execute("SELECT raw_json FROM scrobbles").fetchone()[0])
        assert raw["mbid"] == "recording-id" and "unused" not in raw
        assert conn.execute("SELECT COUNT(*) FROM scrobbles").fetchone()[0] == 1


def test_filtered_overview_keeps_song_album_joins(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    db.apply_window(0, 1000, [play(100, "Sigur Rós", "Hoppípolla")])
    with db.connect() as conn:
        song = conn.execute("SELECT group_id FROM resolved_variants WHERE kind='song'").fetchone()[0]
        album = conn.execute("SELECT group_id FROM resolved_variants WHERE kind='album'").fetchone()[0]
    for entity, gid in (("song", song), ("album", album)):
        report = overview(db, {"period": "all", "entity": entity, "id": str(gid)}, "Europe/London")
        assert report["current"]["plays"] == 1
        assert report["top_artists"][0]["plays"] == 1


def test_manual_song_merge_across_approved_artist_aliases(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    with patch.object(db, "auto_merge_artists"):
        db.apply_window(0, 1000, [
            play(100, "Sigur Rós", "New Song"),
            play(200, "Sigur Ros", "New Song (Radio Edit)"),
        ])
    db.change_artists([normalise("Sigur Rós"), normalise("Sigur Ros")])
    with db.connect() as conn:
        groups = [r[0] for r in conn.execute(
            "SELECT DISTINCT group_id FROM resolved_variants WHERE kind='song'"
        )]
    assert len(groups) == 2
    db.change_groups("merge", groups)
    with db.connect() as conn:
        assert len({r[0] for r in conn.execute(
            "SELECT group_id FROM resolved_variants WHERE kind='song'"
        )}) == 1


def test_similar_names_without_shared_music_stay_separate(tmp_path):
    from analytics.grouping import artist_suggestion_key
    db = Database(tmp_path / "listening.sqlite3")
    assert artist_suggestion_key("AC/DC") == artist_suggestion_key("AC DC")
    db.apply_window(0, 1000, [
        play(100, "AC/DC", "Thunderstruck", "The Razors Edge"),
        play(200, "AC DC", "Different Song", "Different Record"),
    ])
    with db.connect() as conn:
        assert len(rankings(conn, p(), "artist")) == 2
    # A generic "Intro" on "Greatest Hits" cannot establish identity.
    db.apply_window(1000, 2000, [
        play(1100, "AC/DC", "Intro", "Greatest Hits"),
        play(1200, "AC DC", "Intro", "Greatest Hits"),
    ])
    with db.connect() as conn:
        assert len(rankings(conn, p(), "artist")) == 2


def test_artist_merges_after_new_evidence_and_remembers_undo(tmp_path):
    path = tmp_path / "listening.sqlite3"
    db = Database(path)
    db.apply_window(0, 1000, [
        play(100, "Sigur Rós", "Song A", "Album A"),
        play(200, "Sigur Ros", "Song B", "Album B"),
    ])
    with db.connect() as conn:
        assert len(rankings(conn, p(), "artist")) == 2
    # Later data supplies the missing corroboration.
    db.apply_window(1000, 2000, [
        play(1100, "Sigur Ros", "Song A", "Album A"),
    ])
    with db.connect() as conn:
        assert len(rankings(conn, p(), "artist")) == 1
    db.undo_grouping()
    with db.connect() as conn:
        assert len(rankings(conn, p(), "artist")) == 2
    # Neither another import nor a restart should undo the user's decision.
    db.apply_window(2000, 3000, [play(2100, "Sigur Ros", "Song A", "Album A")])
    db = Database(path)
    with db.connect() as conn:
        assert len(rankings(conn, p(), "artist")) == 2


def test_artist_auto_merge_picks_up_old_imports_on_upgrade(tmp_path):
    path = tmp_path / "listening.sqlite3"
    db = Database(path)
    with patch.object(db, "auto_merge_artists"):
        db.apply_window(0, 1000, [
            play(100, "The Courteeners", "Not Nineteen Forever", "St Jude"),
            play(200, "Courteeners", "Not Nineteen Forever", "St Jude"),
        ])
    db.set_meta("artist_auto_merge_rule_version", 0)
    upgraded = Database(path)
    with upgraded.connect() as conn:
        assert len(rankings(conn, p(), "artist")) == 1
    assert upgraded.meta("artist_auto_merge_rule_version") == 1


def test_artist_ids_prevent_false_auto_merges(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    a = play(100, "The Example", "Shared Song", "Shared Album")
    b = play(200, "Example", "Shared Song", "Shared Album")
    a["raw"]["artist"] = {"mbid": "artist-id-one"}
    b["raw"]["artist"] = {"mbid": "artist-id-two"}
    db.apply_window(0, 1000, [a, b])
    with db.connect() as conn:
        assert len(rankings(conn, p(), "artist")) == 2


def test_multiple_shared_track_names_can_merge_without_albums(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    db.apply_window(0, 1000, [
        play(100, "Sigur Rós", "Hoppípolla", ""),
        play(200, "Sigur Rós", "Glósóli", ""),
        play(300, "Sigur Ros", "Hoppípolla", ""),
        play(400, "Sigur Ros", "Glósóli", ""),
    ])
    with db.connect() as conn:
        assert len(rankings(conn, p(), "artist")) == 1
        assert len({r[0] for r in conn.execute(
            "SELECT group_id FROM resolved_variants WHERE kind='song'")}) == 2


def test_article_only_variants_merge_even_if_albums_do_not_overlap(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    db.apply_window(0, 1000, [
        play(100, "The Courteeners", "Not Nineteen Forever", "St Jude"),
        play(200, "Courteeners", "Are You in Love with a Notion?", "Anna"),
    ])
    with db.connect() as conn:
        artists = rankings(conn, p(), "artist")
        assert len(artists) == 1 and artists[0]["plays"] == 2
        # Distinct recordings should NOT be collapsed together.
        assert len({r[0] for r in conn.execute(
            "SELECT group_id FROM resolved_variants WHERE kind='song'")}) == 2


def test_the_the_is_not_the_same_artist_as_the(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    db.apply_window(0, 1000, [
        play(100, "The The", "This Is the Day", "Soul Mining"),
        play(200, "The", "Unrelated Song", "Different Album"),
    ])
    with db.connect() as conn:
        assert len(rankings(conn, p(), "artist")) == 2
