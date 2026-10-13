"""Asynchronous original release-year enrichment, independent of scrobble dates.

MusicBrainz release-group first-release-date is preferred over pressing dates.
Albums are looked up once per canonical group. Song-level recording dates
are also looked up, because a song on a newer album or reissue can predate it.
Album dates are provisional until recording metadata is available.
Unverified years are left unknown, not guessed from composition or play dates.
"""
import json
import queue
from collections import deque
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .album_listens import album_key, metadata_request, track_key
from .grouping import artist_suggestion_key

MAX_JOBS = 35000
# Recheck recordings resolved by older, less comprehensive MusicBrainz searches.
# Increment this when the recording-year matching semantics change.
RECORDING_LOOKUP_VERSION = 2
# Re-evaluate older album matches found with only 12 search results.
ALBUM_LOOKUP_VERSION = 2


def release_year(value):
    match = re.match(r"^(\d{4})(?:-|$)", str(value or ""))
    if not match:
        return None
    year = int(match.group(1))
    return year if 1850 <= year <= datetime.now(timezone.utc).year else None


def quoted(value):
    return '"' + re.sub(r'([+\-!(){}\[\]^"~*?:\\/|&])', r'\\\1', value) + '"'


class ReleaseYearWorker:
    """A background MusicBrainz worker with persistent positive/negative results."""

    def __init__(self, enabled=True, opener=None):
        self.enabled = enabled
        self.opener = opener or urllib.request.urlopen
        self.stop = threading.Event()
        self.jobs = queue.Queue(maxsize=MAX_JOBS)
        self.pending = set()
        self.lock = threading.Lock()
        self.last_scan = {}
        self.processing = None
        self.thread = None
        self.scheduler = None
        self.databases = {}
        self.completed_times = deque(maxlen=101)

    def start(self, db):
        """Start autonomous discovery at add-on startup, without a browser visit."""
        if not self.enabled or self.stop.is_set():
            return
        with self.lock:
            self.databases[str(db.path)] = db
            if self.scheduler is not None:
                return
            self.scheduler = threading.Thread(
                target=self.background_scan, name="release-years-scheduler",
                daemon=True,
            )
            self.scheduler.start()

    def background_scan(self):
        # Rescan periodically to discover new scrobbles and resume after restart.
        # The queue itself is held in memory; checked results survive in SQLite.
        while not self.stop.is_set():
            with self.lock:
                databases = list(self.databases.values())
            for db in databases:
                try:
                    self.scan(db)
                except (sqlite3.Error, OSError, ValueError):
                    # Keep the scheduler alive through transient database failures.
                    pass
            if self.stop.wait(120):
                return

    def mb(self, entity, **params):
        params["fmt"] = "json"
        url = "https://musicbrainz.org/ws/2/" + entity + "?" + urllib.parse.urlencode(params)
        return metadata_request(url, self.opener, self.stop)

    @staticmethod
    def credited(artist, credits):
        names = [
            part.get("name") or (part.get("artist") or {}).get("name", "")
            for part in credits or [] if isinstance(part, dict)
        ]
        return any(artist_suggestion_key(artist) == artist_suggestion_key(name)
                   for name in names if name)

    @staticmethod
    def same_album(album, group):
        """Allow title + MusicBrainz disambiguation and common nicknames.

        MusicBrainz's formal release-group title can be shorter than a
        Last.fm album label (for example a parenthesised nickname). Only
        accept a nickname if it appears in the matching release group's
        own disambiguation; never infer release dates from song dates.
        """
        title = group.get("title") or ""
        if album_key(title) == album_key(album):
            return True
        alias = group.get("disambiguation") or ""
        if not alias:
            return False
        normal_alias = artist_suggestion_key(alias)
        named_alias = artist_suggestion_key(album)
        if normal_alias == named_alias:
            return True
        match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", album)
        if match and album_key(match.group(1)) == album_key(title):
            return artist_suggestion_key(match.group(2)) == normal_alias
        return False

    def album_year(self, artist, album):
        # MB search ordering is relevance, not chronology. The first dozen
        # hits can be later editions while the 1960s release group appears
        # farther down the list. Inspect the full first page before choosing.
        search_titles = [album]
        # A nickname in parentheses is often the release-group disambiguation
        # instead of part of its official title. Search the bare title too.
        match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", album)
        if match:
            search_titles.append(match.group(1).strip())
        candidates = []
        for search_title in dict.fromkeys(search_titles):
            groups = self.mb("release-group",
                             query="releasegroup:" + quoted(search_title) +
                                   " AND artist:" + quoted(artist),
                             limit=100).get("release-groups", [])
            for group in groups:
                if not self.same_album(album, group):
                    continue
                if not self.credited(artist, group.get("artist-credit")):
                    continue
                if any(kind in ("Compilation", "Live", "DJ-mix", "Remix")
                       for kind in group.get("secondary-types", [])):
                    continue
                year = release_year(group.get("first-release-date"))
                if year is not None:
                    priority = {"Album": 0, "EP": 1, "Single": 2}.get(
                        group.get("primary-type"), 3
                    )
                    candidates.append((priority, year))
        return min(candidates)[1] if candidates else None

    def song_year(self, artist, title):
        # Only use an exact title and credited-artist match. A generic song
        # title should never silently inherit another artist's recording year.
        recordings = self.mb("recording",
                             query="recording:" + quoted(title) + " AND artist:" + quoted(artist),
                             limit=100).get("recordings", [])
        years = [
            release_year(recording.get("first-release-date"))
            for recording in recordings
            if track_key(recording.get("title", "")) == track_key(title)
            and self.credited(artist, recording.get("artist-credit"))
        ]
        return min((year for year in years if year is not None), default=None)

    def lookup(self, kind, artist, title):
        try:
            year = (self.album_year if kind == "album" else self.song_year)(artist, title)
            return year, False
        except (ValueError, TypeError, KeyError, AttributeError, OSError,
                urllib.error.URLError, json.JSONDecodeError):
            return None, True

    def scan(self, db, force=False):
        if not self.enabled or self.stop.is_set():
            return
        path = str(db.path)
        with self.lock:
            if not force and time.monotonic() - self.last_scan.get(path, -1e9) < 90:
                return
            self.last_scan[path] = time.monotonic()
        with db.connect() as conn:
            if db.get(conn, "recording_lookup_version", 0) < RECORDING_LOOKUP_VERSION:
                # Existing song dates may have come from an earlier 12-result
                # search. Keep historical rows but mark them unchecked until
                # the current 100-result method has completed for each song.
                conn.execute("UPDATE release_years SET expires=0 WHERE kind='song'")
                db.put(conn, "recording_lookup_version", RECORDING_LOOKUP_VERSION)
                db.put(conn, "release_year_revision",
                       db.get(conn, "release_year_revision", 0) + 1)
            if db.get(conn, "album_lookup_version", 0) < ALBUM_LOOKUP_VERSION:
                # The earlier 12-result search could cache a later album
                # release as its supposed original year. Force a fresh check.
                conn.execute("UPDATE release_years SET expires=0 WHERE kind='album'")
                db.put(conn, "album_lookup_version", ALBUM_LOOKUP_VERSION)
                db.put(conn, "release_year_revision",
                       db.get(conn, "release_year_revision", 0) + 1)
            now = int(time.time())
            cached = {(r["kind"], r["group_id"]): r["expires"]
                      for r in conn.execute("SELECT kind,group_id,expires FROM release_years")}
            albums = conn.execute(
                """SELECT av.group_id AS id,g.artist,g.name,COUNT(*) plays
                   FROM scrobbles s JOIN resolved_variants av ON av.id=s.album_id
                   JOIN groups g ON g.id=av.group_id
                   WHERE s.active=1 GROUP BY av.group_id
                   ORDER BY plays DESC"""
            ).fetchall()
            # A recording's first release may be decades earlier than the
            # album listed by Last.fm (especially on reissues/compilations).
            # Enrich every canonical song rather than only missing album years.
            songs = conn.execute(
                """SELECT sv.group_id AS id,g.artist,g.name,COUNT(*) plays
                   FROM scrobbles s JOIN resolved_variants sv ON sv.id=s.song_id
                   JOIN groups g ON g.id=sv.group_id
                   WHERE s.active=1
                   GROUP BY sv.group_id ORDER BY plays DESC"""
            ).fetchall()
        # Interleave popularity-ranked albums and songs, so the queue
        # progressively corrects old release dates as well as filling in
        # unknown ones. Never make songs wait behind thousands of albums.
        with self.lock:
            for index in range(max(len(albums), len(songs))):
                for kind, rows in (("album", albums), ("song", songs)):
                    if index >= len(rows):
                        continue
                    row = rows[index]
                    if cached.get((kind, row["id"]), 0) > now:
                        continue
                    key = (path, kind, row["id"])
                    if key in self.pending or self.jobs.full():
                        continue
                    self.pending.add(key)
                    self.jobs.put_nowait((db, kind, row["id"], row["artist"], row["name"]))
            if self.thread is None and not self.jobs.empty():
                self.thread = threading.Thread(target=self.run, name="release-years", daemon=True)
                self.thread.start()

    def status(self, db):
        """Whole-library counts, including jobs not yet added to the queue.

        'Checked' includes a lookup that found no matching release year.
        It does not imply that every returned MusicBrainz date is correct.
        Expired entries count as pending until rechecked.
        """
        now = int(time.time())
        with db.connect() as conn:
            progress = {}
            for kind, field in (("song", "song_id"), ("album", "album_id")):
                row = conn.execute(
                    f"""WITH active_groups AS (
                          SELECT DISTINCT v.group_id id
                          FROM scrobbles s
                          JOIN resolved_variants v ON v.id=s.{field}
                          WHERE s.active=1 AND s.{field} IS NOT NULL
                       )
                       SELECT COUNT(*) AS total,
                         COUNT(CASE WHEN r.expires>? THEN 1 END) AS checked,
                         COUNT(CASE WHEN r.expires>? AND r.year IS NOT NULL
                           THEN 1 END) AS matched,
                         COUNT(CASE WHEN r.expires>? AND r.year IS NULL
                           THEN 1 END) AS unresolved
                       FROM active_groups a
                       LEFT JOIN release_years r ON r.kind=? AND r.group_id=a.id""",
                    (now, now, now, kind),
                ).fetchone()
                progress[kind + "s"] = dict(row)

        path = str(db.path)
        with self.lock:
            current = self.processing if self.processing and self.processing[0] == path else None
            for kind in ("song", "album"):
                group = progress[kind + "s"]
                processing = int(bool(current and current[1] == kind))
                queued = sum(key[0] == path and key[1] == kind for key in self.pending) - processing
                group["pending"] = max(0, group["total"] - group["checked"])
                group["queued"] = max(0, queued)
                group["processing"] = processing
                group["complete"] = group["pending"] == 0 and group["queued"] == 0 and not processing
        with self.lock:
            samples = list(self.completed_times)
        remaining = progress["songs"]["pending"] + progress["albums"]["pending"]
        # A rolling estimate from actual completed network lookups, not a
        # promise: MusicBrainz delays, the other metadata worker, and restarts
        # can all change the rate.
        eta = None
        if remaining and len(samples) >= 5 and time.monotonic() - samples[-1] < 180:
            seconds_per_lookup = (samples[-1] - samples[0]) / (len(samples) - 1)
            eta = max(0, round(remaining * seconds_per_lookup))
        return {
            "enabled": self.enabled and not self.stop.is_set(),
            "eta_seconds": eta,
            "songs": progress["songs"],
            "albums": progress["albums"],
            "queued": progress["songs"]["queued"] + progress["albums"]["queued"],
            "processing": progress["songs"]["processing"] + progress["albums"]["processing"],
        }

    def run(self):
        while not self.stop.is_set():
            try:
                db, kind, group_id, artist, name = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            path = str(db.path)
            with self.lock:
                self.processing = (path, kind, group_id)
            saved = False
            try:
                year, failed = self.lookup(kind, artist, name)
                now = int(time.time())
                with db.connect() as conn:
                    old = conn.execute(
                        "SELECT year FROM release_years WHERE kind=? AND group_id=?",
                        (kind, group_id),
                    ).fetchone()
                    conn.execute(
                        """INSERT INTO release_years(kind,group_id,year,source,expires)
                           VALUES (?,?,?,?,?)
                           ON CONFLICT(kind,group_id) DO UPDATE SET
                           year=excluded.year,source=excluded.source,expires=excluded.expires""",
                        (kind, group_id, year, "MusicBrainz" if year else None,
                         now + (3600 if failed else 90 * 86400)),
                    )
                    if old is None or old["year"] != year:
                        db.put(conn, "release_year_revision",
                               db.get(conn, "release_year_revision", 0) + 1)
                saved = True
            except (ValueError, TypeError, sqlite3.Error, OSError):
                pass
            finally:
                with self.lock:
                    self.pending.discard((path, kind, group_id))
                    if saved:
                        self.completed_times.append(time.monotonic())
                    self.processing = None
                self.jobs.task_done()
            if saved and self.jobs.empty() and not self.stop.is_set():
                # A very large catalogue can exceed MAX_JOBS. Continue
                # scanning automatically when each queue batch finishes,
                # even if the user has closed the release-year page.
                self.scan(db, force=True)

    def close(self):
        self.stop.set()
        if self.scheduler is not None:
            self.scheduler.join(timeout=2)
        if self.thread is not None:
            self.thread.join(timeout=5)
