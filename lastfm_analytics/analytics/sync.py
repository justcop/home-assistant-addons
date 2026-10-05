"""Read-only Last.fm import. Checkpoints advance only with committed windows."""

import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

from .db import identity


class SyncError(Exception):
    """Safe user-facing message, with no request URL or credentials."""


class Cancelled(Exception):
    pass


def track_text(value):
    return (
        str(value.get("#text", value.get("name", "")))
        if isinstance(value, dict)
        else str(value or "")
    )


def decode_page(payload, expected_page):
    if not isinstance(payload, dict) or not isinstance(
        payload.get("recenttracks"), dict
    ):
        raise SyncError(
            "Last.fm returned an incomplete response. No history was removed."
        )
    recent = payload["recenttracks"]
    try:
        attrs = recent["@attr"]
        total, pages, page = (
            int(attrs["total"]),
            int(attrs["totalPages"]),
            int(attrs["page"]),
        )
        if min(total, pages) < 0 or page != expected_page or (total > 0 and pages < 1):
            raise ValueError
        tracks = recent["track"]
        if isinstance(tracks, dict):
            tracks = [tracks]
        if not isinstance(tracks, list):
            raise ValueError
        rows, playing = [], None
        for item in tracks:
            if not isinstance(item, dict):
                raise ValueError
            if str(item.get("@attr", {}).get("nowplaying", "")).lower() == "true":
                playing = {
                    "artist": track_text(item.get("artist")),
                    "title": track_text(item.get("name")),
                    "album": track_text(item.get("album")),
                }
                continue
            ts = int(item["date"]["uts"])
            artist, title = track_text(item.get("artist")), track_text(item.get("name"))
            if ts < 0 or not artist.strip() or not title.strip():
                raise ValueError
            rows.append(
                {
                    "ts": ts,
                    "artist": artist,
                    "title": title,
                    "album": track_text(item.get("album")),
                    "raw": item,
                }
            )
        return {"rows": rows, "playing": playing, "total": total, "pages": pages}
    except (KeyError, ValueError, TypeError, AttributeError):
        raise SyncError(
            "Last.fm returned invalid history or pagination. No history was removed."
        ) from None


class LastFM:
    def __init__(self, username, api_key, stop=None, interval=1.0, opener=None):
        self.username, self.api_key = username, api_key
        self.stop = stop or threading.Event()
        self.interval = interval
        self.last_request = 0.0
        self.opener = opener or urllib.request.urlopen

    def wait(self, seconds):
        if self.stop.wait(max(0, seconds)):
            raise Cancelled()

    def recent(self, page=1, start=None, end=None, limit=200):
        params = {
            "method": "user.getRecentTracks",
            "user": self.username,
            "api_key": self.api_key,
            "format": "json",
            "limit": limit,
            "page": page,
        }
        # Request a one-second boundary overlap, then filter locally to [start,end).
        if start is not None:
            params["from"] = max(0, start - 1)
        if end is not None:
            params["to"] = end
        url = "https://ws.audioscrobbler.com/2.0/?" + urllib.parse.urlencode(params)
        for attempt in range(4):
            self.wait(self.interval - (time.monotonic() - self.last_request))
            self.last_request = time.monotonic()
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "ListeningAnalytics/0.1 (Home Assistant companion)"
                    },
                )
                with self.opener(request, timeout=20) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                    if len(raw) > 8 * 1024 * 1024:
                        raise SyncError("Last.fm response was too large.")
                    payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise SyncError("Last.fm returned an invalid response.")
                if payload.get("error"):
                    code = int(payload["error"])
                    if code in (8, 11, 16, 29):
                        if attempt < 3:
                            self.wait(max(30 if code == 29 else 2, 2**attempt))
                            continue
                        raise SyncError(
                            "Last.fm is unavailable or rate limiting. Sync will retry automatically."
                        )
                    raise SyncError(
                        f"Last.fm rejected the request (code {code}). Check username and API key in add-on configuration."
                    )
                return decode_page(payload, page)
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504):
                    raise SyncError(
                        f"Last.fm returned HTTP {exc.code}. Check configuration."
                    ) from None
                if attempt < 3:
                    try:
                        delay = min(
                            120,
                            max(2**attempt, int(exc.headers.get("Retry-After", "30"))),
                        )
                    except (ValueError, TypeError):
                        delay = 30
                    self.wait(delay)
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
                if attempt < 3:
                    self.wait(2 ** (attempt + 1))
        raise SyncError(
            "Unable to read Last.fm. Saved history is safe; sync will retry automatically."
        )


class Importer:
    WINDOW = 30 * 86400

    def __init__(self, db, client, stop=None, progress=None, clock=time.time):
        self.db, self.client = db, client
        self.stop = stop or threading.Event()
        self.progress = progress or (lambda **kwargs: None)
        self.clock = clock

    def check_stop(self):
        if self.stop.is_set():
            raise Cancelled()

    def read_window(self, start, end):
        rows, total, pages = [], None, None
        page = 1
        while True:
            self.check_stop()
            data = self.client.recent(page=page, start=start, end=end)
            if total is None:
                total, pages = data["total"], data["pages"]
                if pages != math.ceil(total / 200) and not (total == 0 and pages == 1):
                    raise SyncError(
                        "Last.fm pagination was inconsistent. Retrying on the next sync."
                    )
                if pages > 1000:
                    raise SyncError(
                        "History window is unusually large. No history was changed."
                    )
            elif total != data["total"] or pages != data["pages"]:
                raise SyncError(
                    "Last.fm history changed during download. Retrying this window."
                )
            rows.extend(data["rows"])
            self.progress(
                page=page, pages=max(1, pages), window_start=start, window_end=end
            )
            if page >= pages:
                break
            if len(data["rows"]) != 200:
                raise SyncError(
                    "Last.fm returned a short page. No history was removed."
                )
            page += 1
        if len(rows) != total or any(a["ts"] < b["ts"] for a, b in zip(rows, rows[1:])):
            raise SyncError(
                "Last.fm returned incomplete or unordered history. Retrying this window."
            )
        if any(not max(0, start - 1) <= row["ts"] <= end for row in rows):
            raise SyncError("Last.fm returned entries outside the requested range.")
        return [r for r in rows if start <= r["ts"] < end]

    def stable_window(self, start, end):
        first = self.read_window(start, end)
        second = self.read_window(start, end)
        if Counter(map(identity, first)) != Counter(map(identity, second)):
            raise SyncError(
                "Last.fm history changed between checks. Saved data is unchanged for this window."
            )
        return second

    def import_history(self):
        state = self.db.meta("import")
        if state and state["complete"]:
            return
        if not state:
            upper = int(self.clock())
            first = self.client.recent(end=upper)
            if first["total"]:
                last = self.client.recent(page=first["pages"], end=upper)
                if first["total"] != last["total"] or not last["rows"]:
                    raise SyncError(
                        "History changed while finding its beginning. Please retry."
                    )
                lower = min(row["ts"] for row in last["rows"])
            else:
                lower = upper
            state = {
                "lower": lower,
                "upper": upper,
                "cursor": upper,
                "complete": False,
                "windows": 0,
            }
            self.db.set_meta("import", state)
        while state["cursor"] > state["lower"]:
            self.check_stop()
            end = state["cursor"]
            start = max(state["lower"], end - self.WINDOW)
            self.progress(phase="importing", window_start=start, window_end=end)
            rows = self.stable_window(start, end)
            state = {**state, "cursor": start, "windows": state["windows"] + 1}
            self.db.apply_window(start, end, rows, checkpoint={"import": state})
        state["complete"] = True
        with self.db.connect() as conn:
            self.db.put(conn, "import", state)
            self.db.put(conn, "watermark", state["upper"])

    def update(self, reconcile_days=7):
        self.import_history()
        end = int(self.clock())
        watermark = self.db.meta("watermark", end)
        last_reconcile = self.db.meta("last_reconcile", 0)
        daily = end - last_reconcile >= 86400
        overlap = reconcile_days * 86400 if daily else 2 * 86400
        start = max(0, min(watermark - 2 * 86400, end - overlap))
        while start < end:
            self.check_stop()
            hi = min(end, start + self.WINDOW)
            self.progress(phase="syncing", window_start=start, window_end=hi)
            rows = self.stable_window(start, hi)
            self.db.apply_window(
                start,
                hi,
                rows,
                reconcile=True,
                checkpoint={"watermark": max(watermark, hi)},
            )
            start = hi
        if daily:
            self.db.set_meta("last_reconcile", end)
        self.db.set_meta("last_sync", end)


class SyncWorker:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.state = {"phase": "ready", "error": None}
        self.thread = None
        self.client = LastFM(config["username"], config["api_key"], self.stop)

    def progress(self, **kwargs):
        with self.lock:
            self.state.update(kwargs)

    def status(self):
        with self.lock:
            return dict(self.state)

    def start(self):
        if (
            not self.config["username"]
            or not self.config["api_key"]
            or self.config["demo_mode"]
        ):
            self.progress(phase="demo" if self.config["demo_mode"] else "setup")
            return
        self.thread = threading.Thread(target=self.run, name="lastfm-sync", daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            self.wake.clear()
            started = time.monotonic()
            try:
                self.progress(error=None)
                # This unbounded recent request is the only source for now playing.
                recent = self.client.recent(limit=1)
                self.db.set_meta(
                    "now_playing",
                    {"track": recent["playing"], "checked_at": int(time.time())},
                )
                Importer(self.db, self.client, self.stop, self.progress).update(
                    self.config["reconcile_days"]
                )
                self.progress(phase="idle", error=None)
            except Cancelled:
                break
            except SyncError as exc:
                self.progress(phase="error", error=str(exc))
            except Exception:
                # Never stringify third-party exceptions: URLs may contain API keys.
                self.progress(
                    phase="error",
                    error="Sync could not finish. Saved history is safe. Check storage and configuration.",
                )
            remaining = max(
                0, self.config["sync_interval_seconds"] - (time.monotonic() - started)
            )
            self.wake.wait(remaining)
            # Manual refresh coalesces, and cannot bypass a one-minute minimum.
            if self.stop.wait(max(0, 60 - (time.monotonic() - started))):
                break

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=25)
