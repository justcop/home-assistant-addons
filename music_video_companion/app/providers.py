"""Home Assistant and YouTube adapters using Python stdlib."""
import json
import logging
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

LOG = logging.getLogger(__name__)

class ApiHTTPError(RuntimeError):
    def __init__(self, status, detail):
        self.status = status
        super().__init__(f"Request failed ({status}): {detail}")

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
        raise ApiHTTPError(exc.code, detail) from exc
    except URLError as exc:
        raise RuntimeError("Connection failed: " + str(exc.reason)) from exc

class HomeAssistant:
    """Prefer Supervisor's injected token; allow explicit direct-Core fallback.

    Never send a user long-lived token to the Supervisor proxy, and never send
    the Supervisor token directly to Home Assistant Core.
    """
    def __init__(self, token=None, endpoint=None, alternate_token="", direct_endpoint=None):
        self.token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        self.endpoint = (endpoint or os.environ.get("HA_API_URL", "http://supervisor/core/api")).rstrip("/")
        self.alternate_token = str(alternate_token or "")
        self.direct_endpoint = (direct_endpoint or "http://homeassistant:8123/api").rstrip("/")
        if not self.token:
            LOG.warning("Supervisor API token is absent; use a Home Assistant access token fallback if needed")

    def _request(self, suffix, method="GET", data=None):
        if self.token:
            try:
                return json_request(self.endpoint + suffix, self.token, method=method, data=data)
            except ApiHTTPError as exc:
                if exc.status not in (401, 403):
                    raise
                if not self.alternate_token:
                    raise RuntimeError(
                        "Home Assistant refused the add-on's internal API credentials "
                        f"({exc.status}). This is not the YouTube key. Check the add-on's "
                        "homeassistant_api permission and restart it after updating. "
                        "Alternatively, add an optional Home Assistant Access Token "
                        "under add-on Configuration."
                    ) from exc
                LOG.warning("Internal Home Assistant API authentication rejected (%s); trying configured Core token", exc.status)
        if not self.alternate_token:
            raise RuntimeError(
                "Home Assistant API authentication unavailable: Supervisor token missing. "
                "Reinstall or restart the updated add-on, or configure the optional Home Assistant Access Token."
            )
        try:
            return json_request(self.direct_endpoint + suffix, self.alternate_token, method=method, data=data)
        except ApiHTTPError as exc:
            if exc.status in (401, 403):
                raise RuntimeError(
                    "Direct Home Assistant API rejected the configured Access Token "
                    f"({exc.status}). Create a new long-lived token under Profile → Security, "
                    "paste it in the add-on Configuration, then restart."
                ) from exc
            raise

    def states(self):
        return self._request("/states")

    def state(self, entity):
        return self._request("/states/" + quote(entity)) if entity else None

    def service(self, action, data):
        return self._request("/services/media_player/" + action, method="POST", data=data)

    def refresh_entity(self, entity):
        """Request an early Spotify entity refresh through Home Assistant Core."""
        return self._request("/services/homeassistant/update_entity",
                             method="POST", data={"entity_id": entity})

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
    return {"artist": artist, "title": title, "state": status, "position": round(position, 3)}

def youtube_search(api_key, artist, title, channel_id=None):
    if not api_key:
        raise ValueError("Add youtube_api_key in the add-on Configuration first")
    params = {"part": "snippet", "type": "video", "maxResults": "25",
              "q": f"{artist} {title} official music video", "key": api_key}
    if channel_id:
        params["channelId"] = channel_id
    try:
        result = json_request("https://www.googleapis.com/youtube/v3/search?" + urlencode(params))
    except ApiHTTPError as exc:
        if exc.status in (400, 401, 403):
            raise RuntimeError(
                f"YouTube Data API rejected the search ({exc.status}). Check the "
                "YouTube API key, API restrictions, and that YouTube Data API v3 is enabled."
            ) from exc
        raise
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
