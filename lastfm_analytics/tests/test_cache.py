from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
import time

from analytics.cache import ViewCache
from analytics.db import Database


def test_saved_view_survives_restart_and_week_away(tmp_path):
    db = Database(tmp_path / "plays.sqlite3")
    first = ViewCache("Europe/London", {"overview": lambda db, args: {"plays": 10}})
    saved = first.get(db, "overview", {})
    first.close()
    with db.connect() as conn:
        conn.execute("UPDATE view_cache SET generated=?", (time.time() - 7 * 86400,))
    entered, release = threading.Event(), threading.Event()

    def compute(db, args):
        entered.set()
        assert release.wait(5)
        return {"plays": 12}

    second = ViewCache("Europe/London", {"overview": compute})
    try:
        result = second.get(db, "overview", {})
        assert result["plays"] == saved["plays"] == 10
        assert result["_cache"]["stale"] and result["_cache"]["refreshing"]
        assert entered.wait(2)
        with second.lock:
            future = next(iter(second.jobs.values()))
        release.set()
        future.result(timeout=5)
        assert second.get(db, "overview", {})["plays"] == 12
    finally:
        release.set()
        second.close()


def test_concurrent_cold_requests_share_one_calculation(tmp_path):
    db = Database(tmp_path / "plays.sqlite3")
    entered, release = threading.Event(), threading.Event()
    calls = []

    def compute(db, args):
        calls.append(args)
        entered.set()
        assert release.wait(5)
        return {"plays": 1}

    cache = ViewCache("Europe/London", {"overview": compute})
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            requests = [pool.submit(cache.get, db, "overview", {}) for _ in range(4)]
            assert entered.wait(2)
            release.set()
            assert all(f.result(timeout=5)["plays"] == 1 for f in requests)
        assert len(calls) == 1
    finally:
        release.set()
        cache.close()


def test_nightly_refresh_runs_without_a_browser_and_uses_local_time(
    tmp_path, monkeypatch
):
    import analytics.cache as module
    from zoneinfo import ZoneInfo

    db = Database(tmp_path / "plays.sqlite3")
    calls = []
    cache = ViewCache(
        "Europe/London",
        {"overview": lambda db, args: calls.append(args) or {"calls": len(calls)}},
    )
    try:
        cache.get(db, "overview", {})
        cutoff = datetime(2026, 10, 6, 3, tzinfo=ZoneInfo("Europe/London"))
        with db.connect() as conn:
            conn.execute(
                "UPDATE view_cache SET generated=?", (cutoff.timestamp() - 86400,)
            )

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cutoff.replace(hour=2, minute=59)

        monkeypatch.setattr(module, "datetime", Clock)
        before = len(calls)
        cache._nightly()
        assert len(calls) == before
        Clock.now = classmethod(lambda cls, tz=None: cutoff)
        monkeypatch.setattr(module.time, "time", lambda: cutoff.timestamp() + 1)
        cache._nightly()
        with cache.lock:
            futures = list(cache.jobs.values())
        for future in futures:
            future.result(timeout=5)
        assert len(calls) == before + 1
        with db.connect() as conn:
            assert (
                conn.execute("SELECT generated FROM view_cache").fetchone()[0]
                > cutoff.timestamp()
            )
        cache._nightly()
        assert len(calls) == before + 1
    finally:
        cache.close()


def test_filters_accounts_and_cache_size_are_separate(tmp_path):
    db = Database(tmp_path / "one.sqlite3")
    other = Database(tmp_path / "two.sqlite3")
    cache = ViewCache(
        "Europe/London",
        {
            "overview": lambda db, args: {
                "account": db.path.name,
                "source": args["source"],
            }
        },
    )
    try:
        assert cache.get(db, "overview", {"source": "vinyl"})["source"] == "vinyl"
        assert cache.get(db, "overview", {})["source"] == "all"
        assert cache.get(other, "overview", {})["account"] == "two.sqlite3"
        for year in range(1980, 1980 + cache.LIMIT + 2):
            cache.get(db, "overview", {"period": f"year:{year}"})
        with db.connect() as conn:
            assert (
                conn.execute("SELECT COUNT(*) FROM view_cache").fetchone()[0]
                == cache.LIMIT
            )
            assert conn.execute(
                "SELECT 1 FROM view_cache WHERE cache_key=?",
                (cache.key("overview", {}),),
            ).fetchone()
    finally:
        cache.close()


def test_background_failure_keeps_saved_result(tmp_path):
    db = Database(tmp_path / "plays.sqlite3")
    cache = ViewCache("Europe/London", {"overview": lambda db, args: {"plays": 10}})
    try:
        cache.get(db, "overview", {})
        db.set_meta("import", {"complete": True})
        entered, release = threading.Event(), threading.Event()

        def fail(db, args):
            entered.set()
            assert release.wait(5)
            raise RuntimeError("private fixture error")

        cache.compute["overview"] = fail
        saved = cache.get(db, "overview", {})
        assert saved["plays"] == 10
        assert entered.wait(2)
        with cache.lock:
            future = next(iter(cache.jobs.values()))
        release.set()
        try:
            future.result(timeout=5)
        except RuntimeError:
            pass
        saved = cache.get(db, "overview", {})
        assert saved["plays"] == 10 and saved["_cache"]["error"]
        assert "private fixture error" not in str(saved)
    finally:
        cache.close()


def test_grouping_clear_discards_an_inflight_old_result(tmp_path):
    db = Database(tmp_path / "plays.sqlite3")
    cache = ViewCache("Europe/London", {"overview": lambda db, args: {"plays": 1}})
    entered, release = threading.Event(), threading.Event()
    try:
        cache.get(db, "overview", {})
        db.set_meta("import", {"complete": True})
        calls = []

        def compute(db, args):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                assert release.wait(5)
                return {"plays": 2}
            return {"plays": 3}

        cache.compute["overview"] = compute
        assert cache.get(db, "overview", {})["plays"] == 1
        assert entered.wait(2)
        cache.clear(db)
        with ThreadPoolExecutor(max_workers=1) as pool:
            refreshed = pool.submit(cache.get, db, "overview", {})
            release.set()
            assert refreshed.result(timeout=5)["plays"] == 3
        assert cache.get(db, "overview", {})["plays"] == 3
    finally:
        release.set()
        cache.close()
