import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mqtt_runtime import (
    AVAILABILITY_TOPIC,
    COMMAND_TOPICS,
    add_availability,
    configure_client,
    subscribe_commands,
)


class FakeClient:
    def __init__(self):
        self.calls = []
        self.subscriptions = []

    def username_pw_set(self, username, password):
        self.calls.append(("auth", username, password))

    def will_set(self, topic, payload, retain=False):
        self.calls.append(("will", topic, payload, retain))

    def reconnect_delay_set(self, min_delay, max_delay):
        self.calls.append(("delay", min_delay, max_delay))

    def connect_async(self, broker, port, keepalive):
        self.calls.append(("connect_async", broker, port, keepalive))

    def loop_start(self):
        self.calls.append(("loop_start",))

    def subscribe(self, topic):
        self.subscriptions.append(topic)


class MqttRuntimeTests(unittest.TestCase):
    def test_configure_uses_async_reconnect_and_last_will(self):
        client = FakeClient()
        callbacks = [object() for _ in range(4)]
        configure_client(
            client,
            "broker",
            1883,
            username="user",
            password="pass",
            on_connect=callbacks[0],
            on_connect_fail=callbacks[1],
            on_disconnect=callbacks[2],
            on_message=callbacks[3],
        )
        self.assertIn(("auth", "user", "pass"), client.calls)
        self.assertIn(("will", AVAILABILITY_TOPIC, "offline", True), client.calls)
        self.assertIn(("delay", 1, 60), client.calls)
        self.assertIn(("connect_async", "broker", 1883, 60), client.calls)
        self.assertIn(("loop_start",), client.calls)
        self.assertIs(client.on_connect, callbacks[0])
        self.assertIs(client.on_connect_fail, callbacks[1])
        self.assertIs(client.on_disconnect, callbacks[2])
        self.assertIs(client.on_message, callbacks[3])

    def test_reconnect_subscriptions_are_centralised(self):
        client = FakeClient()
        subscribe_commands(client)
        self.assertEqual(tuple(client.subscriptions), COMMAND_TOPICS)
        self.assertIn("vinyl_guardian/calibration/continue", client.subscriptions)
        self.assertIn("vinyl_guardian/audio/source/set", client.subscriptions)

    def test_discovery_payload_has_availability(self):
        payload = add_availability({"name": "Track"})
        self.assertEqual(payload["availability_topic"], AVAILABILITY_TOPIC)
        self.assertEqual(payload["payload_available"], "online")
        self.assertEqual(payload["payload_not_available"], "offline")


if __name__ == "__main__":
    unittest.main()
