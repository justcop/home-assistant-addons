"""Launch an approved YouTube Cast video; never silently pick an untrusted result."""
from model import song_key, strict_candidate
from providers import youtube_search, cast_video

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
        target = self.ha.state(entity) or {}
        old_mute = (target.get("attributes") or {}).get("is_volume_muted")
        self.ha.service("volume_mute", {"entity_id": entity, "is_volume_muted": True})
        try:
            cast_video(self.ha, entity, match["video_id"])
        except Exception:
            if old_mute is False:
                self.ha.service("volume_mute", {"entity_id": entity, "is_volume_muted": False})
            raise
        self.active = {"entity": entity, "video_id": match["video_id"], "old_mute": old_mute, "song_key": key}
        self.searched.add(key)
        self.info = "Official video launched; frame-level sync is not provided."
        return match

def stop(self):
    with self.lock:
        active = self.active
        if not active:
            return
        self.active = None
        entity = active["entity"]
        try:
            target = self.ha.state(entity) or {}
            attrs = target.get("attributes") or {}
            content = str(attrs.get("media_content_id") or "")
            app_name = str(attrs.get("app_name") or "").lower()
            ours = active["video_id"] in content or (not content and "youtube" in app_name)
            if ours:
                self.ha.service("media_stop", {"entity_id": entity})
                if active["old_mute"] is False:
                    self.ha.service("volume_mute", {"entity_id": entity, "is_volume_muted": False})
            self.info = "Video companion stopped."
        except Exception as exc:
            self.error = "Could not stop Cast: " + str(exc)
