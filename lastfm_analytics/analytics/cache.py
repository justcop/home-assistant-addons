"""Bounded persistent analytics results, refreshed by one background worker."""

from concurrent.futures import Future
from datetime import datetime
import json
import queue
import threading
import time
from zoneinfo import ZoneInfo

from concurrent.futures import TimeoutError as FutureTimeout


class ViewCache:
    LIMIT = 24
    RETRY_SECONDS = 60
    # This changes only when cached payload semantics change, not every release.
    CACHE_FORMAT = "analytics-v4"
    COLD_WAIT_SECONDS = 0.6
    WORKER_COUNT = 2

    def __init__(self, timezone, compute):
        self.timezone = ZoneInfo(timezone)
        self.compute = compute
        self.lock = threading.RLock()
        self.jobs = {}
        self.failures = {}
        self.databases = {}
        self.epochs = {}
        self.queue = queue.Queue()
        self.stop = threading.Event()
        self.threads = []

    def key(self, name, args):
        keys = ["period", "source", "mode", "entity", "id"]
        if args.get("period", "all") == "custom":
            keys += ["start", "end"]
        if name == "rankings":
            keys += ["kind", "q", "offset", "album_sort"]
        if name == "release-years":
            keys += ["release_year"]
        values = {k: args[k] for k in keys if args.get(k) not in (None, "")}
        if name == "release-years":
            # Refresh only this view's schema. Preserve users' other cached
            # overview and ranking views across the add-on upgrade.
            values["_release_year_schema"] = 2
        values.setdefault("period", "all")
        values.setdefault("source", "all")
        values.setdefault("mode", "merged")
        return json.dumps([self.CACHE_FORMAT, name, values], sort_keys=True)

    @staticmethod
    def _revision(db, conn, name):
        base = int(db.get(conn, "analysis_revision", 0))
        if name == "release-years":
            # Release metadata advances independently of imports/grouping.
            # Prevent thousands of MusicBrainz lookups from invalidating
            # unrelated, frequently opened overview and ranking caches.
            release = int(db.get(conn, "release_year_revision", 0))
            return base * 1_000_000_000 + release
        return base

    def register(self, db):
        with self.lock:
            if str(db.path) not in self.databases:
                with db.connect() as conn:
                    for row in conn.execute(
                        "SELECT cache_key FROM view_cache"
                    ).fetchall():
                        if json.loads(row[0])[0] != self.CACHE_FORMAT:
                            conn.execute(
                                "DELETE FROM view_cache WHERE cache_key=?", (row[0],)
                            )
            self.databases[str(db.path)] = db
            if not self.threads:
                # Keep small detail requests responsive even when a long
                # all-time calculation is running (SQLite uses WAL).
                self.threads = [
                    threading.Thread(target=self._run, daemon=True,
                                     name=f"analytics-cache-{i + 1}")
                    for i in range(self.WORKER_COUNT)
                ]
                for thread in self.threads:
                    thread.start()

    def _schedule(self, db, key):
        jobkey = (str(db.path), key)
        with self.lock:
            if jobkey in self.jobs and self.jobs[jobkey].cache_epoch == self.epochs.get(
                str(db.path), 0
            ):
                return self.jobs[jobkey]
            failed = self.failures.get(jobkey)
            if failed is not None and time.monotonic() - failed < self.RETRY_SECONDS:
                return None
            if len(self.jobs) >= self.LIMIT * 2:
                return None
            future = Future()
            future.cache_epoch = self.epochs.get(str(db.path), 0)
            self.jobs[jobkey] = future
            self.queue.put((db, key, future, self.epochs.get(str(db.path), 0)))
            return future

    def get(self, db, name, args):
        self.register(db)
        key = self.key(name, args)
        with self.lock:
            with db.connect() as conn:
                revision = self._revision(db, conn, name)
                row = conn.execute(
                    "SELECT * FROM view_cache WHERE cache_key=?", (key,)
                ).fetchone()
                if row and time.time() - row["accessed"] > 900:
                    # Avoid a SQLite write on every cache hit or polling request.
                    conn.execute(
                        "UPDATE view_cache SET accessed=? WHERE cache_key=?",
                        (time.time(), key),
                    )
            today = datetime.now(self.timezone).date()
            stale = (
                row is None
                or row["revision"] != revision
                or datetime.fromtimestamp(row["generated"], self.timezone).date()
                != today
            )
            future = self._schedule(db, key) if stale else None
        if row is None:
            # A first view has no saved result: respond with pending instead
            # of telling the user that a queued calculation has failed.
            if future is None:
                return {"_pending": True, "retry_after": 5}
            try:
                result, generated, revision = future.result(timeout=self.COLD_WAIT_SECONDS)
            except FutureTimeout:
                return {"_pending": True, "retry_after": 2}
            with db.connect() as conn:
                stale = revision != self._revision(db, conn, name)
        else:
            result, generated = json.loads(row["payload"]), row["generated"]
        with self.lock:
            refreshing = (str(db.path), key) in self.jobs
            failed = (str(db.path), key) in self.failures
        return {
            **result,
            "_cache": {
                "stale": stale,
                "refreshing": refreshing,
                "generated": generated,
                "error": failed,
            },
        }

    def warm(self, db):
        self.register(db)
        self._schedule(db, self.key("overview", {}))

    def clear(self, db):
        # Explicit grouping edits must show the user's decision immediately.
        with self.lock:
            self.epochs[str(db.path)] = self.epochs.get(str(db.path), 0) + 1
        with db.connect() as conn:
            conn.execute("DELETE FROM view_cache")

    def _nightly(self):
        now = datetime.now(self.timezone)
        if now.hour < 3:
            return
        cutoff = now.replace(hour=3, minute=0, second=0, microsecond=0).timestamp()
        with self.lock:
            databases = list(self.databases.values())
        for db in databases:
            with db.connect() as conn:
                keys = [
                    r[0]
                    for r in conn.execute(
                        "SELECT cache_key FROM view_cache WHERE generated<? ORDER BY accessed DESC",
                        (cutoff,),
                    )
                ]
            for key in keys:
                self._schedule(db, key)

    def _run(self):
        while not self.stop.is_set():
            try:
                job = self.queue.get(timeout=30)
            except queue.Empty:
                job = None
            if job:
                db, key, future, epoch = job
                jobkey = (str(db.path), key)
                try:
                    version, name, args = json.loads(key)
                    with db.connect() as conn:
                        revision = self._revision(db, conn, name)
                    result = self.compute[name](db, args)
                    generated = time.time()
                    with self.lock:
                        if epoch == self.epochs.get(str(db.path), 0):
                            with db.connect() as conn:
                                conn.execute(
                                    "INSERT INTO view_cache VALUES (?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET revision=excluded.revision,generated=excluded.generated,payload=excluded.payload",
                                    (
                                        key,
                                        revision,
                                        generated,
                                        generated,
                                        json.dumps(result),
                                    ),
                                )
                                conn.execute(
                                    "DELETE FROM view_cache WHERE cache_key<>? AND cache_key NOT IN (SELECT cache_key FROM view_cache WHERE cache_key<>? ORDER BY accessed DESC LIMIT ?)",
                                    (
                                        self.key("overview", {}),
                                        self.key("overview", {}),
                                        self.LIMIT - 1,
                                    ),
                                )
                        self.failures.pop(jobkey, None)
                    future.set_result((result, generated, revision))
                except Exception as exc:
                    with self.lock:
                        self.failures[jobkey] = time.monotonic()
                        if len(self.failures) > self.LIMIT:
                            del self.failures[next(iter(self.failures))]
                    future.set_exception(exc)
                finally:
                    with self.lock:
                        if self.jobs.get(jobkey) is future:
                            self.jobs.pop(jobkey, None)
            try:
                self._nightly()
            except Exception:
                # A transient storage failure must not kill the refresh worker.
                pass

    def close(self):
        self.stop.set()
        for _ in self.threads:
            self.queue.put(None)
        for thread in self.threads:
            thread.join(timeout=2)
