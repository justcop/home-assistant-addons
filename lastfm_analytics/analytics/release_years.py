"""Asynchronous original release-year enrichment, independent of scrobble dates.

MusicBrainz release-group first-release-date is preferred over pressing dates.
Albums are looked up once per canonical group. Tracks without an identified
album year can fall back to a MusicBrainz recording's first-release-date.
Unverified years are left unknown, not guessed from composition or play dates.
"""
import json
import queue
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

    def album_year(self, artist, album):
        groups = self.mb("release-group",
                         query="releasegroup:" + quoted(album) + " AND artist:" + quoted(artist),
                         limit=12).get("release-groups", [])
        candidates = []
        for group in groups:
            if album_key(group.get("title", "")) != album_key(album):
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
                             limit=12).get("recordings", [])
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
            now = int(time.time())
            cached = {(r["kind"], r["group_id"]): r["expires"]
                      for r in conn.execute("SELECT kind,group_id,expires FROM release_years")}
            albums = conn.execute(
                """SELECT av.group_id AS id,g.artist,g.name,COUNT(*) plays
                   FROM scrobbles s JOIN resolved_variants av ON av.id=s.album_id
                   JOIN groups g ON g.id=av.group_id
                   WHERE s.active=1 GROUP BY av.group_id
                   ORDER BY plays DESC LIMIT 20000"""
            ).fetchall()
            # Only try recording lookups if there is no album or a completed
            # album lookup found no verified release year. Album metadata is
            # vastly cheaper to resolve than every distinct song.
            songs = conn.execute(
                """SELECT sv.group_id AS id,g.artist,g.name,COUNT(*) plays
                   FROM scrobbles s JOIN resolved_variants sv ON sv.id=s.song_id
                   JOIN groups g ON g.id=sv.group_id
                   LEFT JOIN resolved_variants av ON av.id=s.album_id
                   LEFT JOIN release_years ry ON ry.kind='album'
                     AND ry.group_id=av.group_id
                   WHERE s.active=1 AND (s.album_id IS NULL OR
                     (ry.expires>? AND ry.year IS NULL))
                   GROUP BY sv.group_id ORDER BY plays DESC LIMIT 15000""",
                (now,),
            ).fetchall()
        # One queue per service, shared between requests; no duplicate jobs.
        with self.lock:
            for kind, rows in (("album", albums), ("song", songs)):
                for row in rows:
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
        path = str(db.path)
        with self.lock:
            busy = self.processing is not None and self.processing[0] == path
            outstanding = sum(key[0] == path for key in self.pending)
        return {
            "enabled": self.enabled and not self.stop.is_set(),
            "queued": max(0, outstanding - int(busy)),
            "processing": int(busy),
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
                        db.put(conn, "analysis_revision",
                               db.get(conn, "analysis_revision", 0) + 1)
            except (ValueError, TypeError, sqlite3.Error, OSError):
                pass
            finally:
                with self.lock:
                    self.pending.discard((path, kind, group_id))
                    self.processing = None
                self.jobs.task_done()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
