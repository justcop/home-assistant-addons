"""Read-only collectors. Phone data comes from existing Home Assistant entity updates."""
import json
import logging
import os
import threading
import time

log = logging.getLogger(__name__)


def start_mqtt(audit, options):
    import paho.mqtt.client as mqtt
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="work_audit_addon", transport=options.get("mqtt_transport", "tcp"))
    if options.get("mqtt_username"):
        client.username_pw_set(options["mqtt_username"], options.get("mqtt_password", ""))
    if options.get("mqtt_tls"):
        client.tls_set()
    if options.get("mqtt_transport") == "websockets":
        client.ws_set_options(path=options.get("mqtt_websocket_path", "/mqtt"))
    status_topic = options.get("status_topic", "work/monitor/status")
    idle_topic = options.get("idle_topic", "work/monitor/idle")
    if status_topic == idle_topic:
        raise ValueError("Status and idle MQTT topics must differ")

    def connect(client, userdata, flags, reason, properties):
        if reason.is_failure:
            audit.connection("mqtt", False, "Broker rejected connection")
            return
        # Wait for subscription acknowledgement before reporting a healthy collector.
        client.subscribe([(status_topic, 1), (idle_topic, 1)])

    def subscribed(client, userdata, mid, reasons, properties):
        ok = all(not code.is_failure for code in reasons)
        audit.connection("mqtt", ok, "Connected" if ok else "Subscription rejected")

    def disconnected(client, userdata, flags, reason, properties):
        audit.connection("mqtt", False)

    def message(client, userdata, msg):
        try:
            if len(msg.payload) > 16384:
                raise ValueError("Oversized audit payload")
            audit.mqtt("status" if msg.topic == status_topic else "idle", msg.payload.decode("utf-8"), msg.retain)
        except (ValueError, UnicodeError, TypeError):
            # Payloads can contain private context; do not echo them to logs.
            log.warning("Ignored invalid MQTT audit message")

    client.on_connect, client.on_subscribe = connect, subscribed
    client.on_disconnect, client.on_message = disconnected, message
    client.on_connect_fail = lambda client, userdata: audit.connection("mqtt", False, "Unable to reach broker")
    client.reconnect_delay_set(min_delay=2, max_delay=60)
    client.connect_async(options.get("mqtt_host", "core-mosquitto"), options.get("mqtt_port", 1883), keepalive=30)
    client.loop_start()
    return client


def ha_loop(audit):
    import websocket
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        audit.connection("ha", False, "Supervisor token unavailable")
        return
    while True:
        ws = None
        try:
            ws = websocket.create_connection("ws://supervisor/core/websocket", timeout=30)
            if json.loads(ws.recv()).get("type") != "auth_required":
                raise ValueError("Unexpected authentication handshake")
            ws.send(json.dumps({"type": "auth", "access_token": token}))
            if json.loads(ws.recv()).get("type") != "auth_ok":
                raise ValueError("Home Assistant authentication failed")
            # Subscribe first, then get the snapshot. Buffer changes racing the snapshot.
            ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
            ws.send(json.dumps({"id": 2, "type": "get_states"}))
            snapshot = None
            subscribed = False
            buffered = []
            selected = {audit.options["phone_screen_entity"], audit.options["location_entity"]} - {""}
            while snapshot is None or not subscribed:
                msg = json.loads(ws.recv())
                if msg.get("type") == "result":
                    if not msg.get("success"):
                        raise ValueError("Home Assistant command failed")
                    if msg["id"] == 1:
                        subscribed = True
                    if msg["id"] == 2:
                        snapshot = msg["result"]
                elif msg.get("type") == "event":
                    data = msg["event"]["data"]
                    if data.get("entity_id") in selected:
                        buffered.append(data)
            audit.connection("ha", True)
            with audit.lock:
                audit.candidates = sorted([
                    {"entity_id": s["entity_id"], "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
                     "state": s.get("state", "unknown")}
                    for s in snapshot if s["entity_id"].startswith(("binary_sensor.", "sensor.", "person.", "device_tracker."))
                ], key=lambda x: x["entity_id"])
            for s in snapshot:
                audit.entity(s["entity_id"], s)
            # Only apply buffered events newer than the state snapshot.
            for data in buffered:
                state = data.get("new_state") or {"state": "unavailable"}
                current = audit.entities.get(data["entity_id"], {})
                if not state.get("last_updated") or state["last_updated"] >= (current.get("last_updated") or ""):
                    audit.entity(data["entity_id"], state)
            next_id = 3
            while True:
                try:
                    msg = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    # Detect a silent connection failure, without requesting phone updates.
                    ws.send(json.dumps({"id": next_id, "type": "ping"}))
                    pong_id = next_id
                    next_id += 1
                    while True:
                        msg = json.loads(ws.recv())
                        if msg.get("type") == "pong" and msg.get("id") == pong_id:
                            break
                        if msg.get("type") == "event":
                            data = msg["event"]["data"]
                            audit.entity(data["entity_id"], data.get("new_state") or {"state": "unavailable"})
                    continue
                if not msg:
                    raise ValueError("Home Assistant closed connection")
                if msg.get("type") == "event":
                    data = msg["event"]["data"]
                    audit.entity(data["entity_id"], data.get("new_state") or {"state": "unavailable"})
        except Exception as error:
            log.warning("Home Assistant collector reconnecting (%s)", type(error).__name__)
            audit.connection("ha", False, "Disconnected, reconnecting")
        finally:
            if ws:
                ws.close()
        time.sleep(5)


def tick_loop(audit):
    while True:
        audit.tick()
        time.sleep(5)


def start(audit, options):
    client = start_mqtt(audit, options)
    for target in (ha_loop, tick_loop):
        threading.Thread(target=target, args=(audit,), daemon=True).start()
    return client
