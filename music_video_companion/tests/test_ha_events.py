"""Event stream and fast-track diagnostics regression tests, no network."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from ha_events import meaningful_change, receive_event, connect_and_listen
from runtime import Companion


def state(title="Something", state="playing"):
    return {
        "state": state,
        "attributes": {
            "media_artist": "The Beatles",
            "media_title": title,
            "media_position": 2,
        },
    }


def change(old, new, entity_id="media_player.spotify"):
    return {
        "type": "event",
        "event": {
            "event_type": "state_changed",
            "data": {
                "entity_id": entity_id,
                "old_state": old,
                "new_state": new,
            },
        },
    }


class EventsTests(unittest.TestCase):
    def test_title_change_is_meaningful(self):
        packet = change(state(), state("Penny Lane"))
        self.assertEqual(receive_event(packet, "media_player.spotify")["attributes"]["media_title"], "Penny Lane")

    def test_pause_is_meaningful(self):
        self.assertEqual(receive_event(change(state(), state(state="paused")),
                                       "media_player.spotify")["state"], "paused")

    def test_same_song_position_change_ignored(self):
        previous = state()
        current = state()
        current["attributes"]["media_position"] = 25
        self.assertIsNone(receive_event(change(previous, current), "media_player.spotify"))

    def test_wrong_entity_and_other_events_are_ignored(self):
        self.assertIsNone(receive_event(change(state(), state("Help!"), "media_player.television"),
                                        "media_player.spotify"))
        self.assertIsNone(receive_event({"type": "result", "id": 1}, "media_player.spotify"))

    def test_auth_and_subscription_deliver_event(self):
        class Application:
            options = {"ha_access_token": "fake-private-token"}
            running = True
            def __init__(self):
                self.status = []
                self.events = []
                self.library = type("Library", (), {
                    "settings": lambda self: {"source": "spotify", "spotify_entity": "media_player.spotify"}
                })()
            def set_event_feed(self, state, error):
                self.status.append((state, error))
            def ingest_ha_event(self, data):
                self.events.append(data)
                self.running = False

        class WebSocket:
            def __init__(self):
                self.sent = []
                self.closed = False
                self.inbound = iter([
                    {"type": "auth_required"},
                    {"type": "auth_ok"},
                    {"id": 1, "type": "result", "success": True},
                    change(state(), state("Penny Lane")),
                ])
            def recv(self):
                return json.dumps(next(self.inbound))
            def send(self, payload):
                self.sent.append(json.loads(payload))
            def settimeout(self, timeout):
                self.timeout = timeout
            def close(self):
                self.closed = True

        app = Application()
        ws = WebSocket()
        connect_and_listen(app, lambda *_args, **_kw: ws)
        self.assertTrue(ws.closed)
        self.assertEqual(ws.sent[0], {"type": "auth", "access_token": "fake-private-token"})
        self.assertEqual(ws.sent[1]["event_type"], "state_changed")
        self.assertEqual(app.events[0]["attributes"]["media_title"], "Penny Lane")
        self.assertIn(("connected", ""), app.status)

    def test_push_event_wakes_monitor_and_updates_source(self):
        class HA:
            def state(self, entity):
                raise AssertionError("Event processing should not reread the cached Spotify entity")
        with tempfile.TemporaryDirectory() as folder:
            app = Companion(path=Path(folder) / "test.json", ha=HA(),
                            options={"spotify_refresh_seconds": 0})
            app.library.configure({"source": "spotify", "spotify_entity": "media_player.spotify"})
            app.ingest_ha_event(state("Penny Lane"))
            self.assertTrue(app._wake.is_set())
            app.poll()
            self.assertEqual(app.current["title"], "Penny Lane")
            self.assertEqual(app.last_detection["method"], "Home Assistant push event")
            self.assertGreaterEqual(app.event_feed_events, 1)

    def test_push_events_coalesce_to_latest_track(self):
        class HA:
            def state(self, entity):
                raise AssertionError("Push update should avoid redundant REST GET")
        with tempfile.TemporaryDirectory() as folder:
            app = Companion(path=Path(folder) / "test.json", ha=HA(),
                            options={"spotify_refresh_seconds": 0})
            app.library.configure({"spotify_entity": "media_player.spotify"})
            app.ingest_ha_event(state("Something"))
            app.ingest_ha_event(state("Help!"))
            app.poll()
            self.assertEqual(app.current["title"], "Help!")
            self.assertEqual(app.status()["event_feed"]["meaningful_events"], 2)

    def test_external_adapter_wakes_monitor(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Companion(path=Path(folder) / "test.json", options={})
            app.external_update({"artist": "Test", "title": "Song", "state": "playing"})
            self.assertTrue(app._wake.is_set())


if __name__ == "__main__":
    unittest.main()
