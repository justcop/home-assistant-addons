"""Persistent, non-blocking Last.fm delivery queue for Vinyl Guardian."""

import hashlib
import json
import os
import tempfile
import threading
import time
from copy import deepcopy


RETRY_DELAYS = (5.0, 30.0, 120.0, 600.0, 900.0)
SENT_RETENTION_SECONDS = 7 * 24 * 3600
EVENT_TIMESTAMP_BUCKET_SECONDS = 5


def scrobble_event_id(track):
    if not isinstance(track, dict):
        return None
    artist = str(track.get("artist") or "").strip()
    title = str(track.get("title") or "").strip()
    timestamp = int(float(track.get("start_timestamp") or 0))
    if not artist or not title or timestamp <= 0:
        return None
    # Shazam offset estimates can move a reconstructed start by a second or two
    # after an add-on restart. A five-second identity bucket prevents a duplicate
    # for the same physical play while remaining far shorter than any scrobble-
    # eligible track, so a genuine replay still receives a new event id.
    identity_timestamp = (
        timestamp // EVENT_TIMESTAMP_BUCKET_SECONDS
    ) * EVENT_TIMESTAMP_BUCKET_SECONDS
    raw = f"{artist}\0{title}\0{identity_timestamp}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


class ScrobbleDispatcher:
    """Deliver scrobbles in the background and persist retries across restarts."""

    def __init__(
        self,
        path,
        send,
        *,
        on_success=None,
        on_retry=None,
        logger=None,
        enabled=True,
        clock=time.time,
        autostart=True,
    ):
        self.path = os.fspath(path)
        self.backup_path = self.path + ".backup"
        self.send = send
        self.on_success = on_success
        self.on_retry = on_retry
        self.logger = logger or (lambda _message: None)
        self.enabled = bool(enabled)
        self.clock = clock
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._pending = []
        self._sent = {}
        self._load()
        self._thread = None
        if self.enabled and autostart:
            self._thread = threading.Thread(
                target=self._worker,
                name="vinyl-lastfm",
                daemon=True,
            )
            self._thread.start()

    def _valid_state(self, value):
        if not isinstance(value, dict):
            return None
        pending = value.get("pending")
        sent = value.get("sent")
        if not isinstance(pending, list) or not isinstance(sent, dict):
            return None
        clean = []
        for row in pending:
            if not isinstance(row, dict):
                continue
            if not row.get("event_id") or not row.get("artist") or not row.get("title"):
                continue
            clean.append(dict(row))
        return clean, {str(key): float(ts) for key, ts in sent.items()}

    def _load(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        candidates = []
        for candidate in (self.path, self.backup_path):
            try:
                with open(candidate, "r", encoding="utf-8") as handle:
                    parsed = self._valid_state(json.load(handle))
                if parsed is not None:
                    candidates.append((os.path.getmtime(candidate), parsed))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        if candidates:
            _mtime, (self._pending, self._sent) = max(candidates, key=lambda item: item[0])
        self._prune_sent(self.clock())

    def _prune_sent(self, now):
        cutoff = float(now) - SENT_RETENTION_SECONDS
        self._sent = {
            event_id: when
            for event_id, when in self._sent.items()
            if float(when) >= cutoff
        }

    def _atomic_write_json(self, path, state):
        directory = os.path.dirname(path) or "."
        fd, temp_path = tempfile.mkstemp(prefix=".scrobble_", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except OSError:
                pass

    def _write_state(self):
        state = {"format_version": 1, "pending": self._pending, "sent": self._sent}
        self._atomic_write_json(self.path, state)
        try:
            # Keep the backup at the same logical generation as the primary.
            # Copying the *previous* primary here could resurrect a scrobble
            # that had already been delivered if the new primary was damaged.
            self._atomic_write_json(self.backup_path, state)
        except OSError:
            pass

    @property
    def pending_count(self):
        with self._lock:
            return len(self._pending)

    def is_pending(self, event_id):
        with self._lock:
            return any(row.get("event_id") == event_id for row in self._pending)

    def is_sent(self, event_id):
        with self._lock:
            return event_id in self._sent

    def submit(self, track):
        """Queue one physical play. Same song with a new timestamp is a new event."""
        if not self.enabled:
            return None
        event_id = scrobble_event_id(track)
        if not event_id:
            return None
        with self._lock:
            if event_id in self._sent:
                return event_id
            if any(row.get("event_id") == event_id for row in self._pending):
                return event_id
            row = {
                "event_id": event_id,
                "artist": str(track.get("artist") or ""),
                "title": str(track.get("title") or ""),
                "album": str(track.get("album") or ""),
                "timestamp": int(float(track.get("start_timestamp") or 0)),
                "attempts": 0,
                "next_attempt": float(self.clock()),
                "track": deepcopy(track),
            }
            self._pending.append(row)
            self._write_state()
        self._wake.set()
        return event_id

    def process_due(self, now=None):
        """Process at most one due item; public so tests can run synchronously."""
        if not self.enabled:
            return False
        now = float(self.clock() if now is None else now)
        with self._lock:
            due = next(
                (dict(row) for row in self._pending if float(row.get("next_attempt", 0)) <= now),
                None,
            )
        if due is None:
            return False

        success = False
        try:
            success = bool(self.send(
                due["artist"],
                due["title"],
                due["timestamp"],
                due.get("album") or None,
            ))
        except Exception as exc:
            self.logger(f"🚨 Last.fm delivery worker error: {exc}")

        callback = None
        callback_payload = None
        with self._lock:
            current = next(
                (row for row in self._pending if row.get("event_id") == due["event_id"]),
                None,
            )
            if current is None:
                return True
            if success:
                self._pending.remove(current)
                self._sent[due["event_id"]] = now
                self._prune_sent(now)
                callback = self.on_success
                callback_payload = dict(current)
            else:
                current["attempts"] = int(current.get("attempts", 0)) + 1
                delay = RETRY_DELAYS[min(current["attempts"] - 1, len(RETRY_DELAYS) - 1)]
                current["next_attempt"] = now + delay
                callback = self.on_retry
                callback_payload = dict(current)
            self._write_state()

        if callback is not None:
            try:
                callback(callback_payload)
            except Exception as exc:
                self.logger(f"⚠️ Last.fm queue callback failed: {exc}")
        return True

    def _next_wait(self):
        with self._lock:
            if not self._pending:
                return 60.0
            next_attempt = min(float(row.get("next_attempt", 0)) for row in self._pending)
        return max(0.25, min(60.0, next_attempt - float(self.clock())))

    def _worker(self):
        while not self._stop.is_set():
            while self.process_due():
                if self._stop.is_set():
                    break
            self._wake.wait(self._next_wait())
            self._wake.clear()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._lock:
            self._write_state()
