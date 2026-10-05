"""Pure presentation rules for Home Assistant track entities."""


def track_id(track):
    if not isinstance(track, dict):
        return ""
    return f"{track.get('title', '')} - {track.get('artist', '')}".strip(" -")


def current_track_presentation(
    vinyl_status,
    app_state,
    current_track,
    suppress_track=False,
):
    """Return track state + attributes without mutating recognition state.

    Runout is physically active stylus time, but it is never a playing song.
    """
    if vinyl_status == "Runout Groove" or suppress_track:
        return "Not Playing", {}
    if isinstance(current_track, dict):
        return track_id(current_track) or "Not Playing", dict(current_track)
    if app_state in ("RECORDING", "PROCESSING"):
        return "Searching...", {}
    return "Not Playing", {}
