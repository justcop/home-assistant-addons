"""Artist identity and migration regressions."""
import json
from analytics.db import Database
from analytics.grouping import normalise
from analytics.insights import rankings


def play(ts, artist, title, album="Ágætis byrjun"):
    return {"ts": ts, "artist": artist, "title": title, "album": album,
            "raw": {"name": title, "mbid": "track-mbid", "image": [
                {"size": "large", "#text": "https://lastfm.freetls.fastly.net/a"}]}}


def p():
    return {"start": 0, "end": 5000, "previous_start": 0, "compare": False}


def test_artist_merge_combines_exact_song_album_and_undo(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
    first = "Sigur Rós"
    second = "Sigur Ros"
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


def test_manually_separated_song_is_not_auto_merged(tmp_path):
    db = Database(tmp_path / "listening.sqlite3")
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
