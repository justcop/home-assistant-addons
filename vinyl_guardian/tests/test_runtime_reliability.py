import ast
import json
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_presentation import current_track_presentation


ROOT = Path(__file__).resolve().parents[1]


def load_function(path, name, env):
    tree = ast.parse(path.read_text())
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), env)
    return env[name]


class LastFmAttemptTests(unittest.TestCase):
    def test_success_and_failure_are_reported_to_retry_queue(self):
        path = ROOT / "integrations.py"
        messages = []

        class Success:
            def scrobble(self, **kwargs):
                self.kwargs = kwargs

        def submit(network, artist, title, timestamp, album):
            network.scrobble(
                artist=artist,
                title=title,
                timestamp=int(float(timestamp)),
                album=album,
            )
            return {
                "source": "vinyl",
                "artist": artist,
                "title": title,
                "timestamp": int(float(timestamp)),
            }

        success = Success()
        env = {
            "lastfm_network": success,
            "source_reporter": None,
            "submit_scrobble": submit,
            "log": messages.append,
        }
        attempt = load_function(path, "scrobble_to_lastfm", env)
        self.assertTrue(attempt("Artist", "Title", 1234.9, "Album"))
        self.assertEqual(success.kwargs["timestamp"], 1234)

        class Failure:
            def scrobble(self, **kwargs):
                raise RuntimeError("offline")

        env = {
            "lastfm_network": Failure(),
            "source_reporter": None,
            "submit_scrobble": submit,
            "log": messages.append,
        }
        attempt = load_function(path, "scrobble_to_lastfm", env)
        self.assertFalse(attempt("Artist", "Title", 1234, "Album"))
        self.assertTrue(any("scrobble failed" in message.lower() for message in messages))

    def test_unconfigured_lastfm_is_not_reported_as_success(self):
        path = ROOT / "integrations.py"
        env = {"lastfm_network": None, "log": lambda _message: None}
        attempt = load_function(path, "scrobble_to_lastfm", env)
        self.assertFalse(attempt("Artist", "Title", 1234, "Album"))


class RuntimeStatusTests(unittest.TestCase):
    def test_internal_status_advances_while_mqtt_is_offline(self):
        path = ROOT / "vinyl_guardian.py"

        class MQTT:
            def is_connected(self):
                return False
            def publish(self, *args, **kwargs):
                raise AssertionError("offline state must not publish")

        env = {
            "current_display_status": "Powered Off",
            "current_engine_status": "Off",
            "CALIBRATION_MODE": False,
            "mqtt_client": MQTT(),
        }
        change = load_function(path, "change_3_tier_status", env)
        previous, changed = change("Playing", "Tracking")
        self.assertEqual(previous, "Powered Off")
        self.assertTrue(changed)
        self.assertEqual(env["current_display_status"], "Playing")
        self.assertEqual(env["current_engine_status"], "Tracking")


class RunoutSuppressionIntegrationTests(unittest.TestCase):
    def test_verified_publish_is_the_only_path_that_clears_post_runout_latch(self):
        path = ROOT / "vinyl_guardian.py"

        class MQTT:
            def __init__(self):
                self.messages = []
            def is_connected(self):
                return True
            def publish(self, topic, value, **kwargs):
                self.messages.append((topic, value))

        mqtt = MQTT()
        env = {
            "state_lock": threading.Lock(),
            "track_display_suppressed": True,
            "current_display_status": "Playing",
            "app_state": "SLEEPING",
            "mqtt_client": mqtt,
            "current_track_presentation": current_track_presentation,
            "json": json,
        }
        publish_track = load_function(path, "_publish_track", env)
        track = {"title": "A", "artist": "Artist"}

        publish_track(track)
        self.assertTrue(env["track_display_suppressed"])
        self.assertEqual(mqtt.messages[-2][1], "Not Playing")

        publish_track(track, verified=True)
        self.assertFalse(env["track_display_suppressed"])
        self.assertEqual(mqtt.messages[-2][1], "A - Artist")


if __name__ == "__main__":
    unittest.main()
