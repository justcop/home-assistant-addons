"""One-time alignment on launch, and resynchronisation only on Spotify resume.

Never chase small drift during uninterrupted playback. Receiver identity and
seek-support checks prevent accidentally operating on unrelated TV content.
"""
import math
import re
import time

LOAD_TIMEOUT = 105.0
SEEK_TOLERANCE = 0.12


def seconds(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def owned_video(state, video_id):
    """True: exact video ID; False: definitely different app/video; None: unknown."""
    if not isinstance(state, dict):
        return None
    attrs = state.get("attributes") or {}
    content = str(attrs.get("media_content_id") or "")
    app = str(attrs.get("app_name") or "").lower()
    if content == video_id or (
        video_id and re.search(r"(?:v=|youtu\.be/)" + re.escape(video_id) + r"(?:$|[&#?])", content)
    ):
        return True
    if app and "youtube" not in app:
        return False
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", content):
        return False
    return None


def cast_position(state):
    """Extrapolate an HA Cast observation using its timestamp where possible."""
    from providers import spotify_track
    observed = spotify_track({
        "state": (state or {}).get("state"),
        "attributes": {
            "media_artist": "_", "media_title": "_",
            "media_position": ((state or {}).get("attributes") or {}).get("media_position"),
            "media_position_updated_at": ((state or {}).get("attributes") or {}).get("media_position_updated_at")
        }
    })
    # Missing position must remain unknown rather than being treated as zero.
    if seconds(((state or {}).get("attributes") or {}).get("media_position")) is None:
        return None
    return observed["position"]


def target_position(track, offset):
    current = seconds((track or {}).get("position"))
    if current is None:
        return None
    try:
        adjusted = float(offset)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(adjusted):
        return None
    return round(max(0.0, current + adjusted), 2)


def align_once(ha, active, track, state, phase, clock=None):
    """Issue at most one seek per phase (initial / resume).

    phase is a short-lived latch owned by the caller, not a repeating drift
    correction. Returns 'aligned', 'waiting', 'unavailable', or 'taken_over'.
    """
    now = time.monotonic() if clock is None else clock
    identity = owned_video(state, active["video_id"])
    elapsed = now - active["launched_at"]
    youtube_receiver = "youtube" in str(((state or {}).get("attributes") or {}).get("app_name") or "").lower()
    if identity is False:
        # YouTube pre-roll advertisements may expose a different 11-character
        # media ID. Never seek the advert. Wait for the approved video.
        if phase == "initial" and active.get("initial_pending") and youtube_receiver:
            return "waiting" if elapsed < LOAD_TIMEOUT else "unavailable"
        if elapsed >= 12:
            return "taken_over"
    if identity is not True or (state or {}).get("state") not in ("playing", "paused"):
        if now - active["launched_at"] >= LOAD_TIMEOUT and phase == "initial":
            return "unavailable"
        return "waiting"
    lead = active.get("sync_lead_seconds", 0.0) if track.get("state") == "playing" else 0.0
    position = target_position(track, active.get("offset", 0) + lead)
    if position is None:
        return "unavailable"
    receiver = cast_position(state)
    # We still seek when the receiver lacks position metadata: initial and
    # resume alignment cannot be inferred from playback state alone.
    if not active.get("force_seek") and receiver is not None and abs(receiver - position) <= SEEK_TOLERANCE:
        return "aligned"
    try:
        started = time.monotonic()
        ha.service("media_seek", {"entity_id": active["entity"], "seek_position": position})
        active["seek_request_ms"] = round((time.monotonic() - started) * 1000)
    except Exception as exc:
        active["sync_error"] = "Chromecast seek unavailable: " + str(exc)
        return "unavailable"
    active["seek_count"] = active.get("seek_count", 0) + 1
    active["last_target"] = position
    return "aligned"
