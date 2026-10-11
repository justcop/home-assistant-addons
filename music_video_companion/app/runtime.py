"""Track-source loop, independent of AudioShelf and Vinyl Guardian."""
import json
import logging
import os
import threading
import time
from pathlib import Path
from model import Library, song_key
from providers import HomeAssistant, spotify_track
from video_actions import search, approve, play_current, stop

LOG = logging.getLogger(__name__)

class Companion:
    search = search
    approve = approve
    play_current = play_current
    stop = stop

    def __init__(self, path=None, ha=None, options=None):
        self.options = options if options is not None else self.load_options()
        self.library = Library(path or os.environ.get("VIDEO_LIBRARY", "/data/library.json"))
        self.ha = ha or HomeAssistant()
        self.lock = threading.RLock()
        self.current = {"artist": "", "title": "", "state": "idle", "position": 0}
        self.last_key = ""
        self.active = None
        self.info = "Choose your Home Assistant media players."
        self.error = ""
        self.candidates = []
        self.candidate_song = ""
        self.searched = set()
        self.external_track = None
        self.external_received = 0
        self.running = True

    @staticmethod
    def load_options():
        try:
            return json.loads(Path("/data/options.json").read_text())
        except (OSError, ValueError):
            return {}

    def status(self):
        with self.lock:
            track = dict(self.current)
            match = self.library.match(track["artist"], track["title"]) if track["artist"] else None
            same_song = self.candidate_song == song_key(track["artist"], track["title"])
            return {"settings": self.library.settings(), "current": track,
                    "approved": match, "candidates": list(self.candidates) if same_song else [],
                    "active": dict(self.active) if self.active else None,
                    "has_youtube_key": bool(self.options.get("youtube_api_key")),
                    "external_enabled": bool(self.options.get("source_api_token")),
                    "message": self.info, "error": self.error,
                    "trusted_channels": self.library.trusted(track["artist"]) if track["artist"] else []}

    def entities(self):
        states = self.ha.states()
        return [{"entity_id": e.get("entity_id"),
                 "name": (e.get("attributes") or {}).get("friendly_name", e.get("entity_id")),
                 "state": e.get("state")}
                for e in states if str(e.get("entity_id", "")).startswith("media_player.")]

    def poll(self):
        settings = self.library.settings()
        if settings["source"] == "external":
            with self.lock:
                track = self.external_track if time.monotonic() - self.external_received < 30 else None
                track = dict(track) if track else {"artist": "", "title": "", "state": "idle", "position": 0}
        else:
            if not settings["spotify_entity"]:
                return
            track = spotify_track(self.ha.state(settings["spotify_entity"]))
        if track is None:
            return
        with self.lock:
            prev = dict(self.current)
            self.current = dict(track)
            key = song_key(track["artist"], track["title"]) if track["artist"] else ""
            playing = track["state"] == "playing" and bool(key)
            changed = key != self.last_key
            restarted = key == self.last_key and prev.get("position", 0) > 20 and track.get("position", 0) < 4
            if not playing:
                if self.active:
                    self.stop()
                if track["state"] == "idle":
                    self.last_key = ""
                return
            if changed or restarted:
                self.last_key = key
                if self.active:
                    self.stop()
                self.candidates = []
                self.candidate_song = ""
                if settings["auto"]:
                    self.play_current(allow_lookup=True)
            elif (not self.active and settings["auto"] and
                  self.library.match(track["artist"], track["title"]) and key not in self.searched):
                self.play_current(allow_lookup=False)

    def run(self):
        while self.running:
            try:
                self.poll()
                self.error = ""
            except Exception as exc:
                self.error = str(exc)
                LOG.exception("Polling failed")
            time.sleep(max(2, min(30, int(self.options.get("poll_seconds") or 3))))

    def external_update(self, data):
        if data.get("state") not in ("playing", "paused", "idle"):
            raise ValueError("Unknown track state")
        try:
            position = max(0.0, float(data.get("position") or 0))
        except (ValueError, TypeError):
            raise ValueError("Position must be numeric")
        with self.lock:
            self.external_track = {"artist": str(data.get("artist") or "")[:160],
                                   "title": str(data.get("title") or "")[:160],
                                   "state": data["state"], "position": position}
            self.external_received = time.monotonic()
        return {"accepted": True}
