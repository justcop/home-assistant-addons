"""Paced, persistent canonical tracklist lookups for estimated album listens.

Only complete, identified tracklists produce estimates. Missing metadata is NOT
inferred from tracks that happened to be scrobbled, which would inflate results.
"""
import json
import queue
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

from .grouping import artist_suggestion_key, canonical_title, normalise

MIN_TRACKS = 6
SEARCH_LIMIT = 8
USER_AGENT = "ListeningAnalytics/0.2.18 (https://github.com/justcop/home-assistant-addons)"
BAD_EDITION = re.compile(r"\b(deluxe|expanded|anniversary|special|bonus|limited|super deluxe|tour edition|remix)\b", re.I)
VALID_UUID = re.compile(r"^[0-9a-f-]{36}$", re.I)


def track_key(title):
    """Match typography without treating live, remixes or demos as originals."""
    text = unicodedata.normalize("NFKD", normalise(canonical_title(title, "song")))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^\w]+", "", text)


def album_key(title):
    return normalise(canonical_title(title, "album"))


def estimate(tracklist, track_plays):
    """Third least played canonical track, including zero-play tracks."""
    if not isinstance(tracklist, list) or len(tracklist) < MIN_TRACKS:
        return None
    counts = Counter({track_key(t): int(n) for t, n in track_plays.items()})
    ordered = sorted(max(0, counts.get(track_key(t), 0)) for t in tracklist)
    return ordered[2]


def track_breakdown(tracklist, track_plays):
    if not isinstance(tracklist, list):
        return []
    return [{"title": title, "plays": track_plays.get(track_key(title), 0)}
            for title in tracklist]


class TracklistWorker:
    """Single rate-limited metadata worker shared across all app requests."""

    def __init__(self, enabled=True, api_key="", opener=None):
        self.enabled = enabled
        self.api_key = api_key
        self.opener = opener or urllib.request.urlopen
        self.stop = threading.Event()
        self.thread = None
        self.jobs = queue.Queue(maxsize=15000)
        self.pending = set()
        self.lock = threading.Lock()
        self.last_request = 0.0
        self.last_scan = {}
        self.databases = {}

    def request_json(self, url):
        if self.stop.wait(max(0, 1.15 - (time.monotonic() - self.last_request))):
            raise OSError("Tracklist worker stopped")
        self.last_request = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                    "Accept": "application/json"})
        with self.opener(req, timeout=18) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Music metadata too large")
        return json.loads(raw)

    def mb(self, entity, **params):
        params["fmt"] = "json"
        return self.request_json(
            "https://musicbrainz.org/ws/2/" + entity + "?" + urllib.parse.urlencode(params))

    @staticmethod
    def same_artist(left, right):
        return artist_suggestion_key(left) == artist_suggestion_key(right)

    def musicbrainz(self, artist, album):
        """Choose a normal, official GB/US audio edition, not bonus material."""
        def quoted(value):
            return '"' + re.sub(r'([+\-!(){}\[\]^"~*?:\\/|&])', r'\\\1', value) + '"'
        groups = self.mb("release-group", query="releasegroup:" + quoted(album) +
                         " AND artist:" + quoted(artist) + " AND primarytype:album",
                         limit=SEARCH_LIMIT).get("release-groups", [])
        candidates = []
        for group in groups:
            if album_key(group.get("title", "")) != album_key(album):
                continue
            if group.get("primary-type") != "Album":
                continue
            credits = group.get("artist-credit", [])
            names = [c.get("name") or (c.get("artist") or {}).get("name", "")
                     for c in credits if isinstance(c, dict)]
            if not any(self.same_artist(artist, n) for n in names):
                continue
            if any(t in ("Live", "Compilation", "Remix", "DJ-mix")
                   for t in group.get("secondary-types", [])):
                continue
            candidates.append(group)
        if not candidates:
            return None
        group = candidates[0]
        releases = self.mb("release", **{"release-group": group["id"], "status": "official",
                                         "inc": "media", "limit": 100}).get("releases", [])
        original_year = str(group.get("first-release-date", ""))[:4]
        def rank(release):
            formats = [str(m.get("format", "")).lower() for m in release.get("media", [])]
            audio = any(f in ("cd", "digital media", "12\" vinyl", "vinyl",
                              "12\" vinyl", "lp", "10\" vinyl") for f in formats)
            country = release.get("country")
            year = str(release.get("date", ""))[:4]
            gap = (abs(int(year) - int(original_year))
                   if year.isdigit() and original_year.isdigit() else 999)
            return (not audio,
                    bool(BAD_EDITION.search(release.get("title", "") + " " +
                                            release.get("disambiguation", ""))),
                    0 if country == "GB" else 1 if country == "US" else 2,
                    gap, len(release.get("media", [])), release.get("date", ""))
        for release in sorted(releases, key=rank)[:6]:
            if not VALID_UUID.fullmatch(release.get("id", "")):
                continue
            detail = self.mb("release/" + release["id"], inc="recordings")
            tracks = []
            for medium in sorted(detail.get("media", []), key=lambda m: m.get("position", 1)):
                if str(medium.get("format", "")).lower() in ("dvd", "blu-ray", "video"):
                    continue
                for track in sorted(medium.get("tracks", []), key=lambda t: t.get("position", 1)):
                    if (track.get("recording") or {}).get("video"):
                        continue
                    title = track.get("title") or (track.get("recording") or {}).get("title")
                    if title:
                        tracks.append(title)
            if len(tracks) >= MIN_TRACKS:
                return tracks, "MusicBrainz"
        return None

    def lastfm(self, artist, album):
        """Fallback where MusicBrainz has no verified, complete release."""
        if not self.api_key:
            return None
        params = dict(method="album.getInfo", artist=artist, album=album,
                      api_key=self.api_key, autocorrect=0, format="json")
        result = self.request_json("https://ws.audioscrobbler.com/2.0/?" +
                                   urllib.parse.urlencode(params))
        entry = result.get("album", {})
        if (not isinstance(entry, dict) or
                album_key(entry.get("name", "")) != album_key(album) or
                not self.same_artist(artist, entry.get("artist", ""))):
            return None
        entries = (entry.get("tracks") or {}).get("track", [])
        if isinstance(entries, dict):
            entries = [entries]
        tracks = [str(t["name"]) for t in entries
                  if isinstance(t, dict) and isinstance(t.get("name"), str)]
        return (tracks, "Last.fm") if len(tracks) >= MIN_TRACKS else None

    def fetch(self, artist, album):
        failed = False
        for provider in (self.musicbrainz, self.lastfm):
            try:
                result = provider(artist, album)
                if result:
                    return result, False
            except (ValueError, TypeError, KeyError, OSError, urllib.error.URLError):
                failed = True
        return None, failed

    def scan(self, db, force=False):
        if not self.enabled or self.stop.is_set():
            return
        path = str(db.path)
        with self.lock:
            if not force and time.monotonic() - self.last_scan.get(path, -1e9) < 90:
                return
            self.last_scan[path] = time.monotonic()
            self.databases[path] = db
        with db.connect() as conn:
            rows = conn.execute(
                """SELECT av.group_id id, g.artist, g.name,
                          COUNT(*) AS plays
                   FROM scrobbles s JOIN resolved_variants av ON av.id=s.album_id
                   JOIN groups g ON g.id=av.group_id
                   WHERE s.active=1 AND s.album_id IS NOT NULL
                   GROUP BY av.group_id ORDER BY plays DESC LIMIT 14000"""
            ).fetchall()
            for row in rows:
                cache = conn.execute(
                    "SELECT expires FROM album_tracklists WHERE album_id=?", (row["id"],)
                ).fetchone()
                if cache and cache["expires"] > time.time():
                    continue
                job_key = (path, row["id"])
                with self.lock:
                    if job_key in self.pending or self.jobs.full():
                        continue
                    self.pending.add(job_key)
                    self.jobs.put_nowait((db, row["id"], row["artist"], row["name"]))
                    if self.thread is None:
                        self.thread = threading.Thread(target=self.run, name="album-tracklists",
                                                       daemon=True)
                        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                db, album_id, artist, title = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                result, failed = self.fetch(artist, title)
                data, source = result if result else (None, None)
                with db.connect() as conn:
                    old = conn.execute(
                        "SELECT tracks_json FROM album_tracklists WHERE album_id=?",
                        (album_id,)
                    ).fetchone()
                    conn.execute(
                        """INSERT INTO album_tracklists(album_id,tracks_json,source,expires)
                           VALUES (?,?,?,?)
                           ON CONFLICT(album_id) DO UPDATE SET
                           tracks_json=excluded.tracks_json,
                           source=excluded.source, expires=excluded.expires""",
                        (album_id, json.dumps(data, ensure_ascii=False) if data else None,
                         source, int(time.time() + (3600 if failed else 30 * 86400))),
                    )
                    if data and (not old or old[0] != json.dumps(data, ensure_ascii=False)):
                        db.put(conn, "analysis_revision",
                               db.get(conn, "analysis_revision", 0) + 1)
            except (ValueError, TypeError, OSError):
                pass
            finally:
                with self.lock:
                    self.pending.discard((str(db.path), album_id))
                self.jobs.task_done()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
