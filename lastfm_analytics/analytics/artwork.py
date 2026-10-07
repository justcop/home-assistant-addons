"""Small remote cover URLs, with paced background metadata lookups only."""

import json
import queue
import sqlite3
import threading
import time
import urllib.parse
import urllib.request

from .grouping import normalise


def image_url(images):
    if not isinstance(images, list):
        return None
    for size in ("large", "medium", "small"):
        for image in images:
            if not isinstance(image, dict) or image.get("size") != size:
                continue
            url = image.get("#text", "")
            if not isinstance(url, str) or len(url) > 2048:
                continue
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme == "https" and parsed.netloc in (
                "lastfm.freetls.fastly.net", "lastfm-img2.akamaized.net"
            ) and "2a96cbd8b46e442fc41c2b86b821562f" not in url):
                return url
    return None


def artwork_source(url):
    return "Deezer" if urllib.parse.urlsplit(url).netloc == "e-cdns-images.dzcdn.net" else "Last.fm"


def deezer_image(url):
    if not isinstance(url, str) or len(url) > 2048:
        return None
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "https" and parsed.netloc == "e-cdns-images.dzcdn.net":
        return url
    return None


class ArtworkWorker:
    def __init__(self, api_key, enabled=True, opener=None):
        self.api_key = api_key
        self.enabled = enabled and bool(api_key)
        self.opener = opener or urllib.request.urlopen
        self.stop = threading.Event()
        self.jobs = queue.Queue(maxsize=64)
        self.pending = set()
        self.lock = threading.Lock()
        self.thread = None

    def resolve(self, db, albums):
        waiting = False
        for album in albums[:3]:
            artist, name = album["artist"], album["album"]
            key = (normalise(artist), normalise(name))
            with db.connect() as conn:
                cached = conn.execute(
                    "SELECT url,expires FROM artwork_urls WHERE artist_key=? AND album_key=?",
                    key,
                ).fetchone()
            if cached and cached["expires"] > time.time():
                if cached["url"]:
                    return {"artwork": dict(url=cached["url"], artist=artist,
                                            album=name, source=artwork_source(cached["url"])), "artwork_pending": False}
                continue
            if self.enabled:
                job_key = (str(db.path), *key)
                with self.lock:
                    if job_key in self.pending:
                        waiting = True
                    elif not self.jobs.full() and not self.stop.is_set():
                        self.pending.add(job_key)
                        self.jobs.put_nowait((db, artist, name, key, job_key))
                        waiting = True
                        if self.thread is None:
                            self.thread = threading.Thread(target=self.run, daemon=True,
                                                           name="album-artwork")
                            self.thread.start()
        return {"artwork": None, "artwork_pending": waiting}

    def _read_json(self, request):
        with self.opener(request, timeout=5) as response:
            raw = response.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError("Oversized metadata")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Metadata unavailable")
        return payload

    def fetch_lastfm(self, artist, album):
        params = dict(method="album.getInfo", artist=artist, album=album,
                      api_key=self.api_key, format="json", autocorrect=0)
        request = urllib.request.Request(
            "https://ws.audioscrobbler.com/2.0/?" + urllib.parse.urlencode(params),
            headers={"User-Agent": "ListeningAnalytics (Home Assistant companion)"},
        )
        payload = self._read_json(request)
        if payload.get("error") and payload["error"] != 6:
            raise ValueError("Metadata unavailable")
        entry = payload.get("album", {})
        if not isinstance(entry, dict):
            return None
        if (normalise(str(entry.get("artist", ""))) != normalise(artist)
                or normalise(str(entry.get("name", ""))) != normalise(album)):
            return None
        return image_url(entry.get("image"))

    def fetch_deezer(self, artist, album):
        query = urllib.parse.urlencode({
            "q": f'artist:"{artist}" album:"{album}"',
            "limit": 8,
        })
        request = urllib.request.Request(
            "https://api.deezer.com/search/album?" + query,
            headers={"User-Agent": "ListeningAnalytics (Home Assistant companion)"},
        )
        payload = self._read_json(request)
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            return None
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            entry_artist = entry.get("artist", {})
            if not isinstance(entry_artist, dict):
                continue
            if normalise(str(entry_artist.get("name", ""))) != normalise(artist):
                continue
            if normalise(str(entry.get("title", ""))) != normalise(album):
                continue
            for key in ("cover_medium", "cover_big", "cover"):
                url = deezer_image(entry.get(key))
                if url:
                    return url
        return None

    def fetch(self, artist, album):
        # Last.fm remains the first choice because imported history already comes
        # from it. Deezer is a no-key fallback for albums whose Last.fm artwork
        # is missing, which is common for older scrobbles and some catalogue entries.
        try:
            url = self.fetch_lastfm(artist, album)
            if url:
                return url
        except (OSError, ValueError, TypeError):
            pass
        return self.fetch_deezer(artist, album)

    def process(self, job):
        db, artist, album, key, job_key = job
        try:
            try:
                url = self.fetch(artist, album)
                ttl = 30 * 86400 if url else 86400
            except (OSError, ValueError, TypeError):
                url, ttl = None, 300
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO artwork_urls VALUES (?,?,?,?) "
                    "ON CONFLICT(artist_key,album_key) DO UPDATE SET url=excluded.url,expires=excluded.expires",
                    (*key, url, int(time.time()) + ttl),
                )
        finally:
            with self.lock:
                self.pending.discard(job_key)

    def run(self):
        while not self.stop.is_set():
            try:
                job = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.process(job)
            except (OSError, sqlite3.Error):
                pass
            finally:
                self.jobs.task_done()
            self.stop.wait(1)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=6)
