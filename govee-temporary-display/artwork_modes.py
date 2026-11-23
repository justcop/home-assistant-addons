"""Named Govee artwork modes and automatic PS5 title matching.

Built-in and uploaded artwork use the same external-disk gallery and
the same 52×32 pixel renderer. Existing mode IDs remain stable.
"""
from artwork_store import gallery, get_rgb, exists, resolve

UNKNOWN_GAME={"", "none", "unknown", "unavailable", "null", "off", "idle"}


def list_artwork_modes():
    result=[]
    for item in gallery()["items"]:
        game_names=[m["value"] for m in item.get("mappings",[])
                    if m.get("kind")=="game"]
        result.append({
            "id":item["id"],"name":item["name"],"kind":"static",
            "game":game_names[0] if game_names else None,
            "game_names":game_names,"mappings":item.get("mappings",[]),
            "width":52,"height":32,"file":item["file"],"source":item["source"],
        })
    return result


def get_artwork_rgb(mode_id):
    return get_rgb(mode_id)


def is_artwork_mode(mode_id):
    return exists(mode_id)


def resolve_game_artwork(game_title, activity="playing"):
    """Only show a game's art while the local PS5 activity is playing."""
    if activity!="playing":
        return "ps5_default"
    if not isinstance(game_title,str) or game_title.strip().casefold() in UNKNOWN_GAME:
        return "ps5_default"
    return resolve("game",game_title) or "ps5_default"
