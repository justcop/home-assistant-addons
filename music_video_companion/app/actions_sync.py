"""Launch alignment plus pause/resume. No periodic drift-correction seeks."""
import time
from model import song_key
from sync import align_once, owned_video
from providers import spotify_track

def sync_playback(self):
    with self.lock:
        active = self.active
        if not active:
            return
        track = dict(self.current)
        key = song_key(track.get("artist"), track.get("title"))
        if key != active["song_key"]:
            self.stop()
            return
        try:
            receiver = self.ha.state(active["entity"])
        except Exception as exc:
            active["sync_state"] = "Chromecast status unavailable: " + str(exc)
            return
        now = time.monotonic()
        owned = owned_video(receiver, active["video_id"])
        if owned is False and now - active["launched_at"] >= 12:
            # Never touch a device that has been taken over by another app.
            self.active = None
            self.info = "Chromecast switched away from this video."
            return
        status = track.get("state")
        receiver_state = (receiver or {}).get("state")
        if status == "paused":
            active["last_source_state"] = "paused"
            if owned is True and receiver_state == "playing" and not active["pause_applied"]:
                try:
                    self.ha.service("media_pause", {"entity_id": active["entity"]})
                    active["pause_applied"] = True
                    active["sync_state"] = "Paused with Spotify"
                except Exception as exc:
                    active["sync_state"] = "Chromecast pause failed: " + str(exc)
            elif owned is not True:
                active["sync_state"] = "Waiting to pause YouTube"
            else:
                active["sync_state"] = "Paused with Spotify"
            return
        if status != "playing":
            return
        if active.get("last_source_state") == "paused":
            active["resume_pending"] = True
            active["pause_applied"] = False
        active["last_source_state"] = "playing"
        phase = "resume" if active["resume_pending"] else "initial"
        if not active["initial_pending"] and not active["resume_pending"]:
            # Deliberately do nothing during a continuously playing song.
            return
        # Fetch the source clock at the moment of seeking, not when a user
        # pressed Cast: receiver startup can take several seconds.
        settings = self.library.settings()
        if settings["source"] == "spotify" and settings["spotify_entity"]:
            refreshed = spotify_track(self.ha.state(settings["spotify_entity"]))
            if song_key(refreshed.get("artist"), refreshed.get("title")) != active["song_key"]:
                self.stop()
                return
            track = refreshed
            self.current = dict(refreshed)
        result = align_once(self.ha, active, track, receiver, phase, clock=now)
        if result == "waiting":
            active["sync_state"] = "Waiting for YouTube playback to become seekable"
            return
        if result == "taken_over":
            self.active = None
            self.info = "Another Cast application took over; not seeking."
            return
        if result == "unavailable":
            if phase == "initial" and now - active["launched_at"] < 40:
                # A device may report playback without supporting seeking.
                # Mark unsupported immediately if a seek was explicitly rejected.
                if not active.get("sync_error"):
                    active["sync_state"] = "Waiting for usable playback position"
                    return
            active["sync_state"] = active.get("sync_error") or "No verified seekable YouTube session"
            active["initial_pending"] = False
            active["resume_pending"] = False
            if phase == "resume" and owned is True and receiver_state == "paused":
                try:
                    self.ha.service("media_play", {"entity_id": active["entity"]})
                except Exception as exc:
                    active["sync_state"] += "; resume failed: " + str(exc)
            return
        # Single seek per startup/resume phase. No further automatic seeking.
        active["sync_state"] = (
            "Realigned after Spotify resume" if phase == "resume"
            else "Aligned at launch"
        )
        active["initial_pending"] = False
        active["resume_pending"] = False
        if owned is True and receiver_state == "paused":
            try:
                self.ha.service("media_play", {"entity_id": active["entity"]})
            except Exception as exc:
                active["sync_state"] = "Video aligned, but resume failed: " + str(exc)
