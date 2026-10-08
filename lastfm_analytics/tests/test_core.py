import json
import math
import threading
from datetime import datetime, timezone
from unittest.mock import Mock
from zoneinfo import ZoneInfo
import pytest
from analytics.db import Database
from analytics.grouping import canonical_title
from analytics.insights import overview, period
from analytics.sync import Cancelled, Importer, LastFM, SyncError, decode_page
from analytics.web import create_app


def play(ts, title="Come Together", artist="The Beatles", album="Abbey Road"):
    return {
        "ts": ts,
        "title": title,
        "artist": artist,
        "album": album,
        "raw": {"name": title, "source": "fixture"},
    }


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "history.sqlite3")


class FakeLastFM:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.fail_on = None

    def recent(self, page=1, start=None, end=None, limit=200):
        self.calls.append((page, start, end))
        if self.fail_on and len(self.calls) == self.fail_on:
            raise SyncError("Fixture failure")
        rows = sorted(
            [
                r
                for r in self.rows
                if (start is None or r["ts"] >= max(0, start - 1))
                and (end is None or r["ts"] <= end)
            ],
            key=lambda r: -r["ts"],
        )
        return {
            "rows": rows[(page - 1) * limit : page * limit],
            "playing": None,
            "total": len(rows),
            "pages": math.ceil(len(rows) / limit),
        }


def count(db, active=True):
    with db.connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM scrobbles WHERE active=?", (int(active),)
        ).fetchone()[0]


def test_resumable_paginated_import_preserves_repeats_and_boundaries(db):
    now = 2_000_000_000
    rows = [play(now - 5000 - i) for i in range(520)] + [play(now - 5000)] * 2
    rows += [
        play(now - 31 * 86400),
        play(now - 60 * 86400),
        play(now),
        play(now - 30 * 86400),
    ]
    client = FakeLastFM(rows)
    imp = Importer(db, client, clock=lambda: now)
    client.fail_on = 10
    with pytest.raises(SyncError):
        imp.import_history()
    state = db.meta("import")
    assert not state["complete"] and state["cursor"] < now
    before = count(db)
    client.fail_on = None
    imp.import_history()
    assert (
        db.meta("import")["complete"]
        and count(db) == len(rows) - 1
        and count(db) > before
    )
    imp.update()
    assert count(db) == len(rows) - 1
    imp.clock = lambda: now + 10
    imp.update()
    assert count(db) == len(rows)
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM scrobbles WHERE ts=?", (now - 5000,)
            ).fetchone()[0]
            == 3
        )


def test_reconciliation_is_atomic_and_archives_removed_rows(db):
    now = 2_000_000_000
    rows = [play(now - 1000), play(now - 500)]
    db.apply_window(0, now, rows)
    db.set_meta("import", {"complete": True})
    db.set_meta("watermark", now - 100)
    client = FakeLastFM([rows[0]])
    imp = Importer(db, client, clock=lambda: now)
    client.fail_on = 2
    with pytest.raises(SyncError):
        imp.update()
    assert count(db) == 2 and db.meta("watermark") == now - 100
    client.fail_on = None
    imp.update()
    assert count(db) == 1 and count(db, False) == 1
    client.rows = rows
    imp.update()
    assert count(db) == 2 and count(db, False) == 0


def test_changing_same_size_pages_cannot_erase_history(db):
    now = 2_000_000_000
    original = play(now - 100)
    db.apply_window(0, now, [original])
    client = FakeLastFM([original])
    real = client.recent

    def changing(**kwargs):
        if len(client.calls) == 1:
            client.rows = [play(now - 100, "Something")]
        return real(**kwargs)

    client.recent = changing
    with pytest.raises(SyncError):
        Importer(db, client).stable_window(now - 500, now)
    assert count(db) == 1


def test_empty_history_and_long_offline_catchup(db):
    now = 2_000_000_000
    client = FakeLastFM([])
    imp = Importer(db, client, clock=lambda: now)
    imp.update()
    assert count(db) == 0 and db.meta("import")["complete"]
    client.rows = [play(now + 100), play(now + 80 * 86400)]
    imp.clock = lambda: now + 90 * 86400
    imp.update()
    assert count(db) == 2


def test_window_transaction_rolls_back_cursor(db):
    with pytest.raises(ValueError):
        db.apply_window(100, 200, [play(150), play(250)], checkpoint={"watermark": 200})
    assert count(db) == 0 and db.meta("watermark") is None


@pytest.mark.parametrize(
    "title,kind,result",
    [
        ("Come Together (2009 Remaster)", "song", "Come Together"),
        ("Come Together - Remastered 2009", "song", "Come Together"),
        ("Come Together (Live) (2009 Remaster)", "song", "Come Together (Live)"),
        (
            "Come Together - Live, 2009 Remaster",
            "song",
            "Come Together - Live, 2009 Remaster",
        ),
        ("Come Together (2019 Mix)", "song", "Come Together (2019 Mix)"),
        ("Love (Acoustic)", "song", "Love (Acoustic)"),
        ("Abbey Road (Deluxe Edition)", "album", "Abbey Road"),
        ("Deluxe", "song", "Deluxe"),
        ("Remastered", "song", "Remastered"),
        ("Help!", "song", "Help!"),
        ("Song (Part 2)", "song", "Song (Part 2)"),
    ],
)
def test_conservative_titles(title, kind, result):
    assert canonical_title(title, kind) == result


def test_grouping_reversible_and_separation_survives_import(db):
    db.apply_window(
        0,
        1000,
        [
            play(100),
            play(200, "Come Together (2009 Remaster)"),
            play(300, "Come Together - Live"),
        ],
    )
    with db.connect() as conn:
        songs = list(
            conn.execute(
                "SELECT * FROM resolved_variants WHERE kind='song' ORDER BY id"
            )
        )
        assert songs[0]["group_id"] == songs[1]["group_id"] != songs[2]["group_id"]
    db.change_groups("separate", [songs[1]["id"]])
    db.apply_window(
        0,
        1000,
        [
            play(200, "Come Together (2009 Remaster)"),
            play(400, "Come Together (2009 Remaster)"),
        ],
    )
    with db.connect() as conn:
        separate = conn.execute(
            "SELECT group_id FROM resolved_variants WHERE id=?", (songs[1]["id"],)
        ).fetchone()[0]
    assert separate != songs[0]["group_id"]
    db.change_groups("merge", [songs[0]["group_id"], songs[2]["group_id"]])
    db.apply_window(0, 1000, [play(500, "Come Together - Live (2010 Remaster)")])
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT group_id FROM resolved_variants WHERE name='Come Together - Live (2010 Remaster)'"
            ).fetchone()[0]
            == songs[0]["group_id"]
        )
    db.undo_grouping()
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT group_id FROM resolved_variants WHERE name='Come Together - Live (2010 Remaster)'"
            ).fetchone()[0]
            == songs[2]["group_id"]
        )
        assert (
            conn.execute(
                "SELECT group_id FROM resolved_variants WHERE id=?", (songs[1]["id"],)
            ).fetchone()[0]
            == separate
        )
    db.undo_grouping()
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT group_id FROM resolved_variants WHERE id=?", (songs[1]["id"],)
            ).fetchone()[0]
            == songs[0]["group_id"]
        )
    assert count(db) == 5


def test_no_cross_artist_or_cross_kind_merges(db):
    db.apply_window(0, 1000, [play(100), play(200, artist="Other artist")])
    with db.connect() as conn:
        groups = list(conn.execute("SELECT id FROM groups"))
    with pytest.raises(ValueError):
        db.change_groups("merge", [groups[0][0], groups[1][0]])
    with pytest.raises(ValueError):
        db.change_groups("merge", [groups[0][0], groups[2][0]])


def test_actual_stats_discovery_dates_and_raw_vs_merged(db):
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    ts = int(now.timestamp())
    db.apply_window(
        0,
        ts,
        [
            play(ts - 60 * 86400),
            play(ts - 2 * 86400),
            play(ts - 86400, "Come Together (2009 Remaster)"),
            play(ts - 50, artist="New Artist", album=""),
        ],
    )
    data = overview(db, {"period": "30d"}, "Europe/London", now)
    assert (
        data["current"] == {"plays": 3, "artists": 2, "songs": 2, "albums": 1}
        and data["discovery"]["plays"] is None
    )
    db.set_meta("import", {"complete": True})
    data = overview(db, {"period": "30d"}, "Europe/London", now)
    assert data["discovery"]["plays"] == 1 and data["discovery"]["artists"] == 1
    assert data["grouping"]["raw_songs"] == 3 and data["grouping"]["songs"] == 2
    assert (
        sum(b["plays"] for b in data["timeline"]) == 3
        and sum(sum(h) for h in data["hours"]) == 3
    )
    assert (
        overview(db, {"mode": "raw", "period": "30d"}, "Europe/London", now)["current"][
            "songs"
        ]
        == 3
    )
    p = data["period"]
    assert p["end"] - p["start"] == p["start"] - p["previous_start"]


def test_dst_both_folds_share_local_day_and_hour(db):
    dates = [
        datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc),
        datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc),
    ]
    db.apply_window(0, 2_000_000_000, [play(int(d.timestamp())) for d in dates])
    data = overview(
        db,
        {"period": "custom", "start": "2026-10-25", "end": "2026-10-25"},
        "Europe/London",
        datetime(2026, 10, 26, tzinfo=timezone.utc),
    )
    assert (
        len(data["timeline"]) == 1
        and data["timeline"][0]["plays"] == 2
        and data["hours"][6][1] == 2
    )
    assert data["period"]["end"] - data["period"]["start"] == 25 * 3600


def test_invalid_dates_and_future_range():
    for args in [
        {"period": "custom", "start": "bad", "end": "bad"},
        {"period": "custom", "start": "2099-01-01", "end": "2099-01-02"},
    ]:
        with pytest.raises(ValueError):
            period(args, ZoneInfo("Europe/London"))


def payload(tracks, total=1):
    return {
        "recenttracks": {
            "track": tracks,
            "@attr": {"total": str(total), "totalPages": "1", "page": "1"},
        }
    }


def test_now_playing_and_invalid_records():
    playing = {
        "name": "Nude",
        "artist": {"#text": "Radiohead"},
        "@attr": {"nowplaying": "true"},
    }
    data = decode_page(payload([playing], 0), 1)
    assert data["rows"] == [] and data["playing"]["title"] == "Nude"
    with pytest.raises(SyncError):
        decode_page(payload([{"name": "Bad", "artist": "Artist"}]), 1)
    with pytest.raises(SyncError):
        decode_page({"recenttracks": {}}, 1)


def test_api_retries_pacing_and_safe_errors():
    import urllib.error

    stop = Mock()
    stop.wait.return_value = False
    opener = Mock(side_effect=urllib.error.URLError("url with APIKEY_SECRET"))
    client = LastFM("listener", "APIKEY_SECRET", stop=stop, opener=opener, interval=0)
    with pytest.raises(SyncError) as caught:
        client.recent()
    assert (
        "APIKEY_SECRET" not in str(caught.value)
        and opener.call_count == 4
        and stop.wait.call_count >= 7
    )


def test_rate_limit_then_success_and_shutdown_cancels():
    class Response:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, *args):
            return json.dumps(self.data).encode()

    stop = Mock()
    stop.wait.return_value = False
    opener = Mock(
        side_effect=[
            Response({"error": 29, "message": "rate limit"}),
            Response(payload([], 0)),
        ]
    )
    assert LastFM("u", "k", stop=stop, interval=0, opener=opener).recent()["total"] == 0
    assert any(c.args[0] >= 30 for c in stop.wait.call_args_list)
    real_stop = threading.Event()
    real_stop.set()
    with pytest.raises(Cancelled):
        LastFM("u", "k", stop=real_stop).recent()


def test_web_ingress_csrf_setup_account_isolation_and_demo(tmp_path):
    app = create_app(
        tmp_path,
        config={"username": "user", "api_key": "MY_SECRET"},
        start_worker=False,
    )
    client = app.test_client()
    assert (
        client.get("/api/status").status_code == 403
        and client.get("/health").status_code == 200
    )
    env = {"REMOTE_ADDR": "172.30.32.2"}
    home = client.get(
        "/",
        environ_overrides=env,
        headers={"X-Ingress-Path": "/api/hassio_ingress/fixture"},
    )
    assert b'<base href="/api/hassio_ingress/fixture/"' in home.data
    manifest = client.get(
        "/manifest.webmanifest",
        environ_overrides=env,
        headers={"X-Ingress-Path": "/api/hassio_ingress/fixture"},
    )
    assert manifest.json["start_url"] == "/api/hassio_ingress/fixture/"
    assert manifest.json["scope"] == manifest.json["start_url"]
    assert manifest.mimetype == "application/manifest+json"
    assert client.get("/manifest.webmanifest").status_code == 403
    assert (
        client.get(
            "/manifest.webmanifest",
            headers={"X-Ingress-Path": "//evil.example"},
            environ_overrides=env,
        ).status_code
        == 400
    )
    for icon in manifest.json["icons"]:
        path = icon["src"].removeprefix("/api/hassio_ingress/fixture")
        assert client.get(path, environ_overrides=env).status_code == 200
    import re

    csrf = re.search(r'name="csrf-token" content="([^"]+)"', home.text).group(1)
    assert client.post("/api/sync", json={}, environ_overrides=env).status_code == 403
    assert (
        client.post(
            "/api/sync", json={}, headers={"X-CSRF-Token": csrf}, environ_overrides=env
        ).status_code
        == 200
    )
    status = client.get("/api/status", environ_overrides=env)
    assert "MY_SECRET" not in status.text and status.json["configured"]
    assert (
        client.get(
            "/", headers={"X-Ingress-Path": "//evil.example"}, environ_overrides=env
        ).status_code
        == 400
    )
    demo = client.get("/api/status?demo=1", environ_overrides=env).json
    assert (
        demo["demo"]
        and demo["counts"]["plays"] > 1000
        and count(app.extensions["database"]) == 0
    )
    other = create_app(tmp_path, config={"username": "different"}, start_worker=False)
    assert other.extensions["database"].path != app.extensions["database"].path
    for url in [
        "/api/overview?demo=1",
        "/api/rankings?demo=1&kind=song",
        "/api/history?demo=1",
        "/api/rankings?demo=1&kind=album",
    ]:
        r = client.get(url, environ_overrides=env)
        assert r.status_code == 200, r.text
    song = client.get("/api/rankings?demo=1&kind=song", environ_overrides=env).json[
        "rows"
    ][0]
    assert (
        client.get(
            f'/api/detail?demo=1&entity=song&id={song["id"]}', environ_overrides=env
        ).status_code
        == 200
    )


def test_incomplete_page_and_invalid_group_request_are_rejected(db):
    now = 2_000_000_000
    client = FakeLastFM([play(now - 100 - i) for i in range(250)])
    real = client.recent

    def truncated(**kwargs):
        data = real(**kwargs)
        if kwargs["page"] == 1:
            data["rows"] = data["rows"][:-1]
        return data

    client.recent = truncated
    with pytest.raises(SyncError):
        Importer(db, client).read_window(now - 1000, now)
    with pytest.raises(ValueError):
        db.change_groups("separate", [None])
    assert count(db) == 0


def test_import_pending_hides_comparisons_and_original_json_is_retained(db):
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    ts = int(now.timestamp())
    original = play(ts - 100)
    db.apply_window(0, ts, [original])
    changed = {**original, "raw": {"later": "metadata"}}
    db.apply_window(0, ts, [changed])
    data = overview(db, {}, "Europe/London", now)
    assert data["previous"] is None and not data["period"]["compare"]
    with db.connect() as conn:
        assert (
            json.loads(conn.execute("SELECT raw_json FROM scrobbles").fetchone()[0])
            == {"image": original["raw"]["image"]} if "image" in original["raw"] else {}
        )


def test_history_filtering_and_exact_counts(db):
    from analytics.insights import history, rankings, details

    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    ts = int(now.timestamp())
    db.apply_window(
        0,
        ts,
        [
            play(ts - 100),
            play(ts - 200, "Come Together (2009 Remaster)"),
            play(ts - 300, "Unrelated"),
            play(ts - 400, artist="Different artist"),
        ],
    )
    p = period({"period": "all"}, ZoneInfo("Europe/London"), now, ts - 500)
    with db.connect() as conn:
        songs = rankings(conn, p, "song", search="Come Together")
        assert len(songs) == 2
        group = next(r for r in songs if r["artist"] == "The Beatles")
        assert group["plays"] == 2 and group["versions"] == 2
        events = history(
            conn,
            p,
            {"entity": "song", "id": str(group["id"])},
            ZoneInfo("Europe/London"),
        )
        assert events["total"] == 2
        assert len(details(conn, "song", group["id"], False)["versions"]) == 2


def test_direct_password_login_logout_and_password_change(tmp_path):
    import re

    config = {"web_password": "long-enough-password"}
    app = create_app(tmp_path, config=config, start_worker=False)
    client = app.test_client()
    env = {"REMOTE_ADDR": "192.0.2.1", "wsgi.url_scheme": "https"}

    def token(response):
        return re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)

    assert client.get("/", environ_overrides=env).status_code == 302
    assert client.get("/api/status", environ_overrides=env).status_code == 401
    assert (
        client.get(
            "/", environ_overrides=env, headers={"X-Ingress-Path": "/fake"}
        ).location
        == "/login"
    )
    login = client.get("/login", environ_overrides=env)
    cookie = login.headers["Set-Cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert (
        client.post(
            "/login", data={"password": config["web_password"]}, environ_overrides=env
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/login",
            data={"csrf": token(login), "password": "wrong"},
            environ_overrides=env,
        ).status_code
        == 401
    )
    signed_in = client.post(
        "/login",
        data={"csrf": token(login), "password": config["web_password"]},
        environ_overrides=env,
    )
    assert signed_in.status_code == 302
    status = client.get("/api/status", environ_overrides=env)
    assert status.status_code == 200
    assert "Set-Cookie" not in status.headers
    home = client.get("/", environ_overrides=env, headers={"X-Ingress-Path": "/fake"})
    assert '<base href="/"' in home.text and 'id="logout"' in home.text
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', home.text).group(1)
    assert client.post("/api/logout", json={}, environ_overrides=env).status_code == 403
    old_cookie = client.get_cookie("listening_session").value
    assert (
        client.post(
            "/api/logout",
            json={},
            environ_overrides=env,
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 200
    )
    assert client.get("/api/status", environ_overrides=env).status_code == 401
    changed = create_app(
        tmp_path, config={"web_password": "new-long-password"}, start_worker=False
    ).test_client()
    changed.set_cookie("listening_session", old_cookie)
    assert changed.get("/api/status", environ_overrides=env).status_code == 401
    ingress = {"REMOTE_ADDR": "172.30.32.2"}
    assert client.get("/api/status", environ_overrides=ingress).status_code == 200


def test_direct_password_rate_limit_and_disabled_access(tmp_path):
    import re

    client = create_app(
        tmp_path, config={"web_password": "long-enough-password"}, start_worker=False
    ).test_client()
    env = {"REMOTE_ADDR": "192.0.2.2", "wsgi.url_scheme": "https"}
    login = client.get("/login", environ_overrides=env)
    csrf = re.search(r'name="csrf" value="([^"]+)"', login.text).group(1)
    for _ in range(10):
        assert (
            client.post(
                "/login",
                data={"csrf": csrf, "password": "wrong"},
                environ_overrides=env,
            ).status_code
            == 401
        )
    limited = client.post(
        "/login",
        data={"csrf": csrf, "password": "long-enough-password"},
        environ_overrides=env,
    )
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "600"
    disabled = create_app(tmp_path, config={}, start_worker=False).test_client()
    assert disabled.get("/login", environ_overrides=env).status_code == 403
    assert disabled.get("/api/status", environ_overrides=env).status_code == 403
    with pytest.raises(ValueError):
        create_app(tmp_path, config={"web_password": "short"}, start_worker=False)


def test_calendar_year_period():
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    p = period({"period": "year:2024"}, ZoneInfo("Europe/London"), now=now)
    assert p["start_label"] == "01 Jan 2024" and p["end_label"] == "31 Dec 2024"
    assert p["end"] - p["start"] == 366 * 86400
    assert p["previous_start"] == int(
        datetime(2023, 1, 1, tzinfo=ZoneInfo("Europe/London")).timestamp()
    )
    current = period({"period": "year:2026"}, ZoneInfo("Europe/London"), now=now)
    assert current["end"] == int(now.timestamp()) + 1
    assert not current["compare"]
    with pytest.raises(ValueError):
        period({"period": "year:2027"}, ZoneInfo("Europe/London"), now=now)


def test_candidate_review_learning_and_undo(tmp_path):
    from analytics.review import review

    db = Database(tmp_path / "review.sqlite3")
    rows = [
        play(100, title="Alpha"),
        play(200, title="Alpha (Anniversary Edition)"),
        play(300, title="Alpha (Live)"),
        play(400, title="Other"),
        play(500, title="Other (2009 Remaster)"),
    ]
    db.apply_window(0, 1000, rows)
    candidates = review(db)["rows"]
    assert len(candidates) == 1 and candidates[0]["learnable"]
    assert len(review(db, tab="skipped")["rows"]) >= 1
    assert len(review(db, tab="merged")["rows"]) == 1
    db.change_groups("merge_learn", candidates[0]["ids"])
    assert db.meta("learned_rules")[0]["artist"] == "The Beatles"
    # The suffixed entry arrives before its base. Learned matches still resolve.
    db.apply_window(
        1000,
        2000,
        [
            play(1100, title="Beta (Anniversary Edition)"),
            play(1200, title="Beta"),
            play(1300, title="Gamma (Anniversary Edition)", artist="Other Artist"),
            play(1400, title="Gamma", artist="Other Artist"),
        ],
    )
    assert len(review(db, tab="merged")["rows"]) == 3
    db.undo_grouping()
    assert db.meta("learned_rules") == []
    assert len(review(db, tab="merged")["rows"]) == 1
    protected = review(db, tab="skipped")["rows"][0]
    with pytest.raises(ValueError):
        db.change_groups("merge_learn", protected["ids"])


def test_cached_analysis_refreshes_in_background_after_import(tmp_path, monkeypatch):
    app = create_app(tmp_path, config={}, start_worker=False, development=True)
    client = app.test_client()
    db = app.extensions["database"]
    now = int(datetime.now(timezone.utc).timestamp())
    db.apply_window(now - 10, now + 1, [play(now - 5)])
    assert client.get("/api/overview?period=all").json["current"]["plays"] == 1
    db.apply_window(now - 10, now + 1, [play(now - 4, title="Something")])
    cache = app.extensions["view_cache"]
    entered, release = threading.Event(), threading.Event()
    original = cache.compute["overview"]

    def delayed(db, args):
        entered.set()
        assert release.wait(5)
        return original(db, args)

    monkeypatch.setitem(cache.compute, "overview", delayed)
    saved = client.get("/api/overview?period=all").json
    assert saved["current"]["plays"] == 1 and saved["_cache"]["stale"]
    assert entered.wait(2)
    with cache.lock:
        future = next(iter(cache.jobs.values()))
    release.set()
    future.result(timeout=5)
    assert client.get("/api/overview?period=all").json["current"]["plays"] == 2
    cache.close()


def test_rejected_candidate_persists_and_can_be_undone(tmp_path):
    import re

    app = create_app(tmp_path, config={}, development=True, start_worker=False)
    db = app.extensions["database"]
    db.apply_window(
        0,
        1000,
        [play(100, title="Alpha"), play(200, title="Alpha (Anniversary Edition)")],
    )
    client = app.test_client()
    csrf = re.search(
        r'name="csrf-token" content="([^"]+)"', client.get("/").text
    ).group(1)
    candidate = client.get("/api/grouping-review").json["rows"][0]
    response = client.post(
        "/api/grouping",
        json={"action": "dismiss", "key": candidate["key"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert client.get("/api/grouping-review").json["rows"] == []
    assert client.get("/api/grouping-review?tab=skipped").json["rows"][0]["dismissed"]
    restarted = create_app(
        tmp_path, config={}, development=True, start_worker=False
    ).test_client()
    assert restarted.get("/api/grouping-review?tab=skipped").json["rows"][0][
        "dismissed"
    ]
    db.undo_grouping()
    assert len(client.get("/api/grouping-review").json["rows"]) == 1


def test_startup_identifies_invalid_password_without_logging_it(tmp_path, monkeypatch):
    import sys
    from analytics.__main__ import main

    secret = "short-secret"
    (tmp_path / "options.json").write_text(
        json.dumps({"web_password": secret[:-1], "api_key": "never-log-my-api-key"})
    )
    monkeypatch.setattr(sys, "argv", ["analytics", "--data-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as failure:
        main()
    message = str(failure.value)
    assert "web_password" in message and "12 to 256" in message
    assert secret[:-1] not in message and "never-log-my-api-key" not in message


def test_null_optional_options_are_treated_as_blank(tmp_path):
    from analytics.web import load_config

    (tmp_path / "options.json").write_text(
        json.dumps(
            {
                "web_password": None,
                "source_api_token": None,
                "api_key": None,
                "username": None,
            }
        )
    )
    config = load_config(tmp_path)
    assert all(
        config[key] == ""
        for key in ("web_password", "source_api_token", "api_key", "username")
    )
    app = create_app(tmp_path, start_worker=False)
    assert app.test_client().get("/api/status").status_code == 403


def test_invalid_config_values_have_credential_free_errors(tmp_path):
    from analytics.web import ConfigurationError, load_config

    for options, field in [
        ({"sync_interval_seconds": "secret-value"}, "sync_interval_seconds"),
        ({"timezone": "secret-value"}, "timezone"),
        ({"web_password": 12345}, "web_password"),
    ]:
        (tmp_path / "options.json").write_text(json.dumps(options))
        with pytest.raises(ConfigurationError) as failure:
            load_config(tmp_path)
        assert field in str(failure.value) and "secret-value" not in str(failure.value)


def test_remote_cover_art_for_each_detail_type(db):
    from analytics.insights import details

    thumbnail = "https://lastfm.freetls.fastly.net/i/u/174s/cover.jpg"
    row = play(100)
    row["raw"]["image"] = [
        {"size": "extralarge", "#text": "https://lastfm.freetls.fastly.net/full.jpg"},
        {"size": "large", "#text": thumbnail},
    ]
    db.apply_window(0, 200, [row])
    with db.connect() as conn:
        artist = conn.execute("SELECT artist_key FROM scrobbles").fetchone()[0]
        for kind, value in [("artist", artist), ("song", 1), ("album", 2)]:
            for raw in (True, False):
                result = details(conn, kind, value, raw)
                assert result["artwork"]["url"] == thumbnail
                assert result["artwork"]["album"] == "Abbey Road"
        assert (
            details(conn, "artist", artist, False, {"source": "vinyl"})["artwork"]
            is None
        )


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://evil.example/cover.jpg",
        "http://lastfm.freetls.fastly.net/a.jpg",
        "https://lastfm.freetls.fastly.net/i/2a96cbd8b46e442fc41c2b86b821562f.png",
    ],
)
def test_artwork_ignores_untrusted_urls_and_placeholder(db, url):
    from analytics.insights import details

    row = play(100)
    row["raw"]["image"] = [{"size": "large", "#text": url}]
    db.apply_window(0, 200, [row])
    with db.connect() as conn:
        assert details(conn, "song", 1, False)["artwork"] is None


def test_default_period_is_all_time(db):
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    ts = int(now.timestamp())
    db.apply_window(0, ts, [play(ts - 400 * 86400), play(ts - 60)])
    data = overview(db, {}, "Europe/London", now)
    assert data["period"]["name"] == "all"
    assert data["current"]["plays"] == 2
    assert data["previous"] is None
