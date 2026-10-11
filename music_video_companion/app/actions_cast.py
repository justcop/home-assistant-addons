"""Launch an approved YouTube Cast video; never silently pick an untrusted result."""
from model import song_key, strict_candidate
from providers import youtube_search, cast_video
from audio_controls import silence, reassert, restore
import time

def play_current(self, allow_lookup=False):
    with self.lock:
        track = dict(self.current)
        settings = self.library.settings()
        if track["state"] != "playing" or not track["artist"]:
            raise ValueError("No playing track")
        if not settings["cast_entity"]:
            raise ValueError("Choose the Chromecast media player first")
        key = song_key(track["artist"], track["title"])
        match = self.library.match(track["artist"], track["title"])
        if not match and allow_lookup and key not in self.searched:
            self.searched.add(key)
            trusted = self.library.trusted(track["artist"])
            if trusted and self.options.get("youtube_api_key"):
                found = []
                for cid in trusted[:3]:
                    found.extend(youtube_search(self.options["youtube_api_key"], track["artist"], track["title"], cid))
                exact = [v for v in found if strict_candidate(v, track["artist"], track["title"], trusted)]
                if len(exact) == 1:
                    match = self.library.approve(track["artist"], track["title"], exact[0], automatic=True)
        if not match:
            self.info = "No verified official video for this track; skipped."
            return None
        if self.active and self.active["video_id"] == match["video_id"]:
            return match
        if self.active:
            self.stop()
        entity = settings["cast_entity"]
        audio_entity = settings.get("mute_entity") or entity
        if audio_entity == settings.get("spotify_entity"):
            raise ValueError("Refusing to silence the Spotify audio device. Choose your TV or soundbar instead.")
        audio = silence(self.ha, audio_entity)
        try:
            cast_video(self.ha, entity, match["video_id"])
            try:
                # YouTube may change the receiver's audio controls while loading.
                reassert(self.ha, audio)
            except Exception as exc:
                # Stop the video instead of leaving unmuted YouTube audio playing.
                try:
                    self.ha.service("media_stop", {"entity_id": entity})
                finally:
                    raise ValueError("YouTube launched, but audio muting failed: " + str(exc))
        except Exception:
            try:
                restore(self.ha, audio)
            except Exception:
                pass
            raise
        self.active = {
            "entity": entity, "video_id": match["video_id"],
            "audio_restore": audio, "song_key": key,
            "offset": match.get("offset_seconds", 0),
            "launched_at": time.monotonic(),
            "initial_pending": True, "resume_pending": False,
            "pause_applied": False, "last_source_state": "playing",
            "sync_state": "Waiting for YouTube receiver", "seek_count": 0,
        }
        self.searched.add(key)
        self.info = "Official video launched. Initial position alignment pending."
        return match

def stop(self):
    with self.lock:
        active = self.active
        if not active:
            return
        self.active = None
        entity = active["entity"]
        audio = active.get("audio_restore")
        try:
            target = self.ha.state(entity) or {}
            attrs = target.get("attributes") or {}
            content = str(attrs.get("media_content_id") or "")
            app_name = str(attrs.get("app_name") or "").lower()
            # Don't stop other applications if somebody changed the TV.
            ours = active["video_id"] in content or (not content and "youtube" in app_name)
            idle = target.get("state") in ("idle", "off", "unavailable")
            if ours:
                self.ha.service("media_stop", {"entity_id": entity})
            # Restore volume after our video or after an idle receiver. When an
            # unrelated app has taken over, never change that app's volume.
            if audio and (ours or idle):
                restore(self.ha, audio)
            self.info = "Video companion stopped."
        except Exception as exc:
            self.error = "Could not stop Cast or restore audio: " + str(exc)
