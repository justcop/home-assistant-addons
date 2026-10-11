"""Home Assistant state_changed WebSocket listener.

This is a local event notification channel, NOT a separate Spotify API poll.
Uses the existing configured Home Assistant long-lived access token. Reconnects
with backoff. If it is unavailable the REST state polling still works.
"""
import json
import logging
import time

LOG = logging.getLogger(__name__)
URL = "ws://homeassistant:8123/api/websocket"


def meaningful_change(event_data, entity_id):
    """Return the new HA state only for title/artist/play-state transitions."""
    if not isinstance(event_data, dict) or event_data.get("entity_id") != entity_id:
        return None
    new = event_data.get("new_state")
    if not isinstance(new, dict):
        return None
    old = event_data.get("old_state")
    if not isinstance(old, dict):
        return new
    fields = ("media_artist", "media_title")
    old_attrs = old.get("attributes") or {}
    new_attrs = new.get("attributes") or {}
    if old.get("state") == new.get("state") and all(
        old_attrs.get(field) == new_attrs.get(field) for field in fields
    ):
        return None
    return new


def receive_event(message, entity_id):
    """Parse one subscription frame; return matching new_state or None."""
    if not isinstance(message, dict) or message.get("type") != "event":
        return None
    event = message.get("event") or {}
    if event.get("event_type") != "state_changed":
        return None
    return meaningful_change(event.get("data"), entity_id)


def connect_and_listen(app, connector):
    """Single authenticated connection; called by the reconnect supervisor."""
    token = str(app.options.get("ha_access_token") or "")
    if not token:
        raise RuntimeError("Set ha_access_token to enable push updates")
    try:
        from websocket import WebSocketTimeoutException
    except ImportError:
        WebSocketTimeoutException = TimeoutError
    ws = connector(URL, timeout=15)
    try:
        greeting = json.loads(ws.recv())
        if greeting.get("type") != "auth_required":
            raise RuntimeError("Home Assistant did not request WebSocket authentication")
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        auth = json.loads(ws.recv())
        if auth.get("type") != "auth_ok":
            raise RuntimeError("Home Assistant WebSocket authentication rejected")
        ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
        receipt = json.loads(ws.recv())
        if receipt.get("type") != "result" or receipt.get("id") != 1 or not receipt.get("success"):
            raise RuntimeError("Home Assistant rejected state_changed subscription")
        app.set_event_feed("connected", "")
        # Long timeout is fine: Spotify track events arrive asynchronously.
        ws.settimeout(45)
        while app.running:
            try:
                packet = json.loads(ws.recv())
            except (TimeoutError, WebSocketTimeoutException):
                ws.ping()
                continue
            settings = app.library.settings()
            if settings["source"] != "spotify" or not settings["spotify_entity"]:
                continue
            event = receive_event(packet, settings["spotify_entity"])
            if event:
                app.ingest_ha_event(event)
    finally:
        ws.close()


def run_event_feed(app, connector=None):
    """Maintain a nonblocking feed; keep REST polling available on failure."""
    token = str(app.options.get("ha_access_token") or "")
    if not token:
        app.set_event_feed("disabled", "A Home Assistant access token is required for push events")
        return
    if connector is None:
        from websocket import create_connection
        connector = create_connection
    backoff = 2
    while app.running:
        try:
            app.set_event_feed("connecting", "")
            connect_and_listen(app, connector)
            backoff = 2
        except Exception as exc:
            LOG.warning("Home Assistant event feed disconnected: %s", exc)
            app.set_event_feed("disconnected", str(exc)[:180])
            if not app.running:
                return
            time.sleep(backoff)
            backoff = min(60, backoff * 2)
