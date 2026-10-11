"""Launch an approved YouTube Cast video; never silently pick an untrusted result."""
from model import song_key, strict_candidate, vevo_candidate
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
        started = time.monotonic()
        match = self.library.match(track["artist"], track["title"])
        if not match and allow_lookup and key not in self.searched:
            # Search at most once per new song per session. A YouTube search is
            # expensive in quota, and channel titles are not identity proof.
            self.searched.add(key)
            api_key = self.options.get("youtube_api_key")
            trusted = self.library.trusted(track["artist"])
            if api_key:
                found = []
                if trusted:
                    for channel in trusted[:3]:
                        found.extend(youtube_search(api_key, track["artist"], track["title"], channel))
                else:
                    found = youtube_search(api_key, track["artist"], track["title"])
                approved = [item for item in found if
                            strict_candidate(item, track["artist"], track["title"], trusted)
                            or vevo_candidate(item, track["artist"], track["title"])]
                unique = {item["video_id"]: item for item in approved}
                if len(unique) == 1:
                    match = self.library.approve(track["artist"], track["title"],
                                                 next(iter(unique.values())), automatic=True)
        lookup_ms = round((time.monotonic() - started) * 1000)
        if not match:
            self.last_cast_timing = {
                "artist": track["artist"], "title": track["title"],
                "lookup_ms": lookup_ms, "result": "no verified match",
            }
            self.info = "No verified official video for this track; skipped."
            return None
        if self.active and self.active["video_id"] == match["video_id"]:
            return match
        if self.active:
            self.stop()
        entity = settings["cast_entity"]
        manual_audio = settings.get("audio_control", "manual") == "manual"
        audio = None
        if not manual_audio:
            audio_entity = settings.get("mute_entity") or entity
            if audio_entity == settings.get("spotify_entity"):
                raise ValueError("Refusing to silence the Spotify audio device. Choose your TV or soundbar instead.")
            audio = silence(self.ha, audio_entity)
        command_started = time.monotonic()
        try:
            cast_video(self.ha, entity, match["video_id"])
            if audio:
                try:
                    reassert(self.ha, audio)
                except Exception as exc:
                    try:
                        self.ha.service("media_stop", {"entity_id": entity})
                    finally:
                        raise ValueError("YouTube launched, but audio muting failed: " + str(exc))
        except Exception:
            if audio:
                try:
                    restore(self.ha, audio)
                except Exception:
                    pass
            raise
        self.active = {
            "entity": entity, "video_id": match["video_id"],
            "audio_restore": audio, "manual_audio": manual_audio, "song_key": key,
            "offset": match.get("offset_seconds", 0),
            "launched_at": time.monotonic(),
            "initial_pending": True, "resume_pending": False,
            "pause_applied": False, "last_source_state": "playing",
            "sync_state": "Waiting for YouTube receiver", "seek_count": 0,
        }
        self.searched.add(key)
        self.last_cast_timing = {
            "artist": track["artist"], "title": track["title"],
            "lookup_ms": lookup_ms,
            "cast_request_ms": round((time.monotonic() - command_started) * 1000),
            "observed_to_request_ms": (
                round((time.monotonic() - self.last_detection_monotonic) * 1000)
                if self.last_detection_monotonic is not None
                and self.last_detection.get("artist") == track["artist"]
                and self.last_detection.get("title") == track["title"]
                else None
            ),
            "receiver_alignment_ms": None,
            "result": "cast requested",
        }
        self.info = ("Official video launched. Mute TV sound manually; initial alignment pending." if manual_audio
                     else "Official video launched. Initial alignment pending.")
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
