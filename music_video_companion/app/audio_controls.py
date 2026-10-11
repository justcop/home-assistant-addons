"""Safe audio silencing for a Cast receiver or a separate sound system.

Home Assistant media_player devices do not all implement volume_mute.
Only change a volume that can be restored later; never fall back to audible video.
"""
from math import isfinite
from providers import ApiHTTPError

VOLUME_SET = 4
VOLUME_MUTE = 8

def _features(attrs):
    try:
        return int(attrs["supported_features"])
    except (KeyError, ValueError, TypeError):
        return None

def _level(attrs):
    try:
        value = float(attrs.get("volume_level"))
        if isfinite(value) and 0 <= value <= 1:
            return value
    except (TypeError, ValueError):
        pass
    return None

def silence(ha, entity):
    """Mute or set volume to zero. Returns a restoration record.

    Requires a known previous state so we can put the sound back after casting.
    """
    state = ha.state(entity) or {}
    attrs = state.get("attributes") or {}
    features = _features(attrs)
    old_mute = attrs.get("is_volume_muted")
    old_volume = _level(attrs)
    errors = []
    if isinstance(old_mute, bool) and (features is None or features & VOLUME_MUTE):
        try:
            if not old_mute:
                ha.service("volume_mute", {"entity_id": entity, "is_volume_muted": True})
            return {"entity": entity, "method": "mute", "old_mute": old_mute}
        except (ApiHTTPError, RuntimeError, ValueError) as exc:
            errors.append("mute unavailable: " + str(exc))
    if old_volume is not None and (features is None or features & VOLUME_SET):
        try:
            if old_volume != 0:
                ha.service("volume_set", {"entity_id": entity, "volume_level": 0.0})
            return {"entity": entity, "method": "volume", "old_volume": old_volume}
        except (ApiHTTPError, RuntimeError, ValueError) as exc:
            errors.append("volume control unavailable: " + str(exc))
    detail = "; ".join(errors)
    raise ValueError(
        f"{entity} cannot be safely silenced by Home Assistant. "
        "The device lacks a supported mute action and usable volume_set/volume_level. "
        "Choose another audio-output entity under 'Mute audio using' "
        "(for example your soundbar) and save settings before casting. "
        + detail
    )

def reassert(ha, record):
    """Keep receiver silent after YouTube changes the playing application."""
    entity = record["entity"]
    if record["method"] == "mute":
        ha.service("volume_mute", {"entity_id": entity, "is_volume_muted": True})
    else:
        ha.service("volume_set", {"entity_id": entity, "volume_level": 0.0})

def restore(ha, record):
    """Restore only when the value we set remains in effect.

    Preserve manual user volume changes made during casting.
    """
    if not record:
        return
    entity = record["entity"]
    current = ha.state(entity) or {}
    attrs = current.get("attributes") or {}
    if record["method"] == "mute":
        if record["old_mute"] is False and attrs.get("is_volume_muted") is True:
            ha.service("volume_mute", {"entity_id": entity, "is_volume_muted": False})
    elif record["method"] == "volume":
        if _level(attrs) is not None and _level(attrs) <= 0.001:
            old = record["old_volume"]
            if old > 0:
                ha.service("volume_set", {"entity_id": entity, "volume_level": old})
