"""MQTT lifecycle helpers for Vinyl Guardian."""

AVAILABILITY_TOPIC = "vinyl_guardian/availability"
COMMAND_TOPICS = (
    "vinyl_guardian/calibration/continue",
    "vinyl_guardian/debug/trigger",
    "vinyl_guardian/debug/false_positive",
    "vinyl_guardian/debug/missed_music",
    "vinyl_guardian/label/set",
    "vinyl_guardian/label/mark",
    "vinyl_guardian/experiment/replay_latest",
    "vinyl_guardian/diagnostics/mode/set",
    "vinyl_guardian/diagnostics/intentional",
    "vinyl_guardian/diagnostics/finish",
    "vinyl_guardian/profile/rollback",
    "vinyl_guardian/audio/scan",
    "vinyl_guardian/audio/source/set",
)


def add_availability(payload):
    value = dict(payload)
    value["availability_topic"] = AVAILABILITY_TOPIC
    value["payload_available"] = "online"
    value["payload_not_available"] = "offline"
    return value


def configure_client(
    client,
    broker,
    port,
    *,
    username="",
    password="",
    on_connect=None,
    on_connect_fail=None,
    on_disconnect=None,
    on_message=None,
):
    if username and password:
        client.username_pw_set(username, password)
    client.will_set(AVAILABILITY_TOPIC, "offline", retain=True)
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.on_connect = on_connect
    client.on_connect_fail = on_connect_fail
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.connect_async(broker, int(port), 60)
    client.loop_start()


def subscribe_commands(client):
    for topic in COMMAND_TOPICS:
        client.subscribe(topic)
