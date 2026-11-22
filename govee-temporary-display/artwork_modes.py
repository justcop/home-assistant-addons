"""Persistent game-artwork registry for the 52x32 Govee Pixel display.

Artwork is stored with the Home Assistant app, not on the display.
Game names can later be mapped to further graphics without changing the
temporary transport or the PS5 session-control automations.
"""
import re
from artwork_loader import HEIGHT, WIDTH, load_rgb_image

PIXELS = WIDTH * HEIGHT * 3
UNKNOWN_GAME = {"", "none", "unknown", "unavailable", "null", "off", "idle"}



# Stable IDs: future entries can attach their own game aliases and assets.
_ARTWORK = {
    "gta_vi": {
        "name": "GTA VI Sunset",
        "kind": "static",
        "game": "Grand Theft Auto VI",
        "game_names": ["Grand Theft Auto VI", "Grand Theft Auto 6", "GTA VI", "GTA 6"],
        "width": WIDTH,
        "height": HEIGHT,
        "file": "gta_vi.png",
    },
    "ffvii_revelation": {
        "name": "Final Fantasy VII Revelation",
        "kind": "static",
        "game": "Final Fantasy VII Revelation",
        "game_names": [
            "Final Fantasy VII Revelation",
            "Final Fantasy VII: Revelation",
            "Final Fantasy VII - Revelation",
            "Final Fantasy 7 Revelation",
            "FFVII Revelation",
        ],
        "width": WIDTH,
        "height": HEIGHT,
        "file": "ffvii_revelation.png",
    },
    "ps5_default": {
        "name": "PS5 Default",
        "kind": "static",
        "game": None,
        "game_names": [],
        "width": WIDTH,
        "height": HEIGHT,
        "file": "ps5_default.png",
    },
}


def list_artwork_modes():
    """Return JSON metadata for selectable built-in modes."""
    return [
        {"id": mode_id, **{k: v for k, v in item.items() if k != "rgb"}}
        for mode_id, item in _ARTWORK.items()
    ]


def get_artwork_rgb(mode_id):
    if not isinstance(mode_id, str) or mode_id not in _ARTWORK:
        raise ValueError("unknown artwork mode")
    data = load_rgb_image(_ARTWORK[mode_id]["file"])
    if len(data) != PIXELS:
        raise ValueError("artwork mode has invalid frame size")
    return data


def is_artwork_mode(mode_id):
    return isinstance(mode_id, str) and mode_id in _ARTWORK


def _normalise(title):
    if not isinstance(title, str):
        return ""
    return re.sub(r"\s+", " ", title.replace("™", "").replace("®", "").strip()).casefold()


def resolve_game_artwork(game_title, activity="playing"):
    """Recognise only confirmed titles while the console is playing.

    An unavailable/stale PSN title, unknown game, or idle PS5 uses the
    neutral background. Never label an unrelated game as GTA.
    """
    if activity != "playing":
        return "ps5_default"
    title = _normalise(game_title)
    if title in UNKNOWN_GAME:
        return "ps5_default"
    for mode_id, item in _ARTWORK.items():
        if title in (_normalise(x) for x in item["game_names"]):
            return mode_id
    return "ps5_default"
