"""One-time alignment on launch, and resynchronisation only on Spotify resume.

Never chase small drift during uninterrupted playback. Receiver identity and
seek-support checks prevent accidentally operating on unrelated TV content.
"""
import math
import re
import time

LOAD_TIMEOUT = 40.0
SEEK_TOLERANCE = 0.8


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
    if identity is False and now - active["launched_at"] >= 12:
        return "taken_over"
    if identity is not True or (state or {}).get("state") not in ("playing", "paused"):
        if now - active["launched_at"] >= LOAD_TIMEOUT and phase == "initial":
            return "unavailable"
        return "waiting"
    position = target_position(track, active.get("offset", 0))
    if position is None:
        return "unavailable"
    receiver = cast_position(state)
    # We still seek when the receiver lacks position metadata: initial and
    # resume alignment cannot be inferred from playback state alone.
    if receiver is not None and abs(receiver - position) <= SEEK_TOLERANCE:
        return "aligned"
    try:
        ha.service("media_seek", {"entity_id": active["entity"], "seek_position": position})
    except Exception as exc:
        active["sync_error"] = "Chromecast seek unavailable: " + str(exc)
        return "unavailable"
    active["seek_count"] = active.get("seek_count", 0) + 1
    active["last_target"] = position
    return "aligned"
