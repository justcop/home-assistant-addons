"""Home Assistant and YouTube adapters using Python stdlib."""
import json
import logging
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

LOG = logging.getLogger(__name__)

def json_request(url, token="", method="GET", data=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        headers["Content-Type"] = "application/json"
    payload = json.dumps(data).encode("utf8") if data is not None else None
    req = Request(url, data=payload, headers=headers, method=method)
    try:
        with urlopen(req, timeout=10) as response:
            body = response.read(2000000)
            return json.loads(body) if body else {}
    except HTTPError as exc:
        detail = exc.read(200).decode("utf8", "replace")
        raise RuntimeError(f"Request failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise RuntimeError("Connection failed: " + str(exc.reason)) from exc

class HomeAssistant:
    def __init__(self, token=None, endpoint=None):
        self.token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        self.endpoint = (endpoint or os.environ.get("HA_API_URL", "http://supervisor/core/api")).rstrip("/")
        if not self.token:
            LOG.warning("SUPERVISOR_TOKEN absent: HA API unavailable")

    def states(self):
        return json_request(self.endpoint + "/states", self.token)

    def state(self, entity):
        return json_request(self.endpoint + "/states/" + quote(entity), self.token) if entity else None

    def service(self, action, data):
        return json_request(self.endpoint + "/services/media_player/" + action,
                            self.token, method="POST", data=data)

def spotify_track(payload):
    """Normalize metadata from an HA Spotify entity, not Spotify's Web API."""
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("state") or "idle")
    a = payload.get("attributes") or {}
    artist = str(a.get("media_artist") or "").strip()
    title = str(a.get("media_title") or "").strip()
    if status not in ("playing", "paused") or not (artist and title):
        return {"artist": "", "title": "", "state": "idle", "position": 0.0}
    try:
        position = max(0.0, float(a.get("media_position") or 0))
    except (ValueError, TypeError):
        position = 0.0
    updated = a.get("media_position_updated_at")
    if updated and status == "playing":
        try:
            instant = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
            if instant.tzinfo:
                seconds = (datetime.now(timezone.utc) - instant.astimezone(timezone.utc)).total_seconds()
                position += max(0, min(seconds, 120))
        except ValueError:
            pass
    return {"artist": artist, "title": title, "state": status, "position": round(position, 1)}

def youtube_search(api_key, artist, title, channel_id=None):
    if not api_key:
        raise ValueError("Add youtube_api_key in the add-on Configuration first")
    params = {"part": "snippet", "type": "video", "maxResults": "12",
              "q": f"{artist} {title} official music video", "key": api_key}
    if channel_id:
        params["channelId"] = channel_id
    result = json_request("https://www.googleapis.com/youtube/v3/search?" + urlencode(params))
    candidates = []
    for item in result.get("items", []):
        snippet = item.get("snippet") or {}
        vid = (item.get("id") or {}).get("videoId", "")
        if len(vid) != 11:
            continue
        candidates.append({
            "video_id": vid, "title": snippet.get("title", ""),
            "channel_id": snippet.get("channelId", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "thumbnail": (snippet.get("thumbnails") or {}).get("medium", {}).get("url", "")
        })
    return candidates

def cast_video(ha, entity, video_id):
    if not entity:
        raise ValueError("Choose a Chromecast media_player first")
    return ha.service("play_media", {
        "entity_id": entity, "media_content_type": "cast",
        "media_content_id": json.dumps({"app_name": "youtube", "media_id": video_id})
    })
