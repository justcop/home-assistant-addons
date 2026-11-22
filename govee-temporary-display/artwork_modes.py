"""Installed, reusable 52x32 artwork library for the Govee pixel display.

Artwork is versioned in the add-on image (and GitHub), *not* written into
the Govee itself. Transport remains temporary: streaming stops when idle.
Adding a game later only requires registering another asset here.
"""
from gta_vi_fullscreen import HEIGHT, RGB as GTA_VI_RGB, WIDTH

# Keyed by stable mode IDs, so countdown configurations can survive renaming
# an artwork's friendly title.
_ARTWORK = {
    "gta_vi": {
        "name": "GTA VI Sunset",
        "kind": "static",
        "game": "Grand Theft Auto VI",
        "width": WIDTH,
        "height": HEIGHT,
        "rgb": GTA_VI_RGB,
    },
}


def list_artwork_modes():
    """Return safe, JSON-ready metadata, never raw image bytes."""
    return [
        {"id": mode_id, **{k: v for k, v in item.items() if k != "rgb"}}
        for mode_id, item in _ARTWORK.items()
    ]


def get_artwork_rgb(mode_id):
    """Load the original image for a named mode (without timer overlays)."""
    if not isinstance(mode_id, str) or mode_id not in _ARTWORK:
        raise ValueError("unknown artwork mode")
    data = _ARTWORK[mode_id]["rgb"]
    if len(data) != 52 * 32 * 3:
        raise ValueError("artwork mode has invalid frame size")
    return data


def is_artwork_mode(mode_id):
    return isinstance(mode_id, str) and mode_id in _ARTWORK
