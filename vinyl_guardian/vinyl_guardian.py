import calibration_control
from calibration_web import start_server
from audio_scan_once import StartupScanGate, reset_scan_options
import sys
import os
import json
import time
import threading
import wave
import subprocess
import signal
from collections import deque
import numpy as np
import alsaaudio
import paho.mqtt.client as mqtt

# Import local modules
from config import *
from integrations import (
    recognize_shazam,
    get_track_duration,
    get_expected_next_track,
    lastfm_enabled,
    scrobble_to_lastfm,
    log,
)
from calibration import run_calibration
from detector import GuardianDetector
from stylus_usage import StylusUsage
from recognition_session import RecognitionSession, recognize_fragment
from track_reasoning import (
    TrackMonitor,
    PendingScrobbleQueue,
    AlbumIdentityGuard,
    identity_key,
    expected_end,
    scrobble_identity_confident,
    scrobble_is_eligible,
    UNKNOWN_DURATION_SCROBBLE_SECONDS,
)
from telemetry import DatasetCollector
from experiment import ExperimentHarness, TRUSTED_LABELS
from diagnostic_monitor import MODE_NAMES
from profile_manager import ProfileManager
from replay_lab import replay_latest_dataset
from audio_source import AudioSourceManager, SYSTEM_DEFAULT_OPTION
from mqtt_runtime import (
    AVAILABILITY_TOPIC,
    add_availability,
    configure_client,
    subscribe_commands,
)
from scrobble_dispatcher import ScrobbleDispatcher, scrobble_event_id
from runtime_presentation import current_track_presentation

VERSION = os.environ.get("ADDON_VERSION", "Unknown")
FORMAT = alsaaudio.PCM_FORMAT_S16_LE

# Global State & Thread Safety
state_lock = threading.Lock()
recognition_session = RecognitionSession()
track_monitor = TrackMonitor()
pending_scrobbles = PendingScrobbleQueue()
scrobble_dispatcher = None
album_identity_guard = AlbumIdentityGuard()
app_state = "IDLE"
current_attempt = 1
wake_up_time = 0
consecutive_failures = 0
current_track = None
track_display_suppressed = False
scrobble_fired = False
last_scrobbled_track = None
paused_track_memory = None
inp = None
dataset_collector = None
experiment_harness = None
stylus_usage = None
profile_manager = None
manual_label_requested = None
selected_ground_truth_label = "playing"
replay_latest_requested = False
rollback_profile_requested = False
replay_status = "Idle"
profile_status_text = "Unknown"
audio_source_manager = None
audio_scan_requested = False
audio_scan_running = False
audio_source_change_requested = False
requested_audio_source_option = None
audio_scan_status = "Idle"

# Debug Dumper State
debug_countdown = 0
debug_metrics_buffer = {'rms': [], 'hfer': [], 'crest': []}
capture_false_positive_requested = False
capture_missed_music_requested = False
requested_diagnostic_mode = None
requested_diagnostic_action = None

# 3-Tier State Tracking Variables
current_display_status = "Powered Off"
current_engine_status = "Off"

def signal_handler(sig, frame):
    log("🛑 Shutting down gracefully...")
    try:
        if stylus_usage is not None:
            stylus_usage.flush()
    except (OSError, ValueError) as error:
        log(f"🚨 Could not save stylus use: {error}")
    try:
        global inp, dataset_collector
        if inp is not None: inp.close()
        if dataset_collector is not None:
            dataset_collector.close()
        if experiment_harness is not None:
            experiment_harness.audio.flush()
        if scrobble_dispatcher is not None:
            scrobble_dispatcher.close()

        if mqtt_client.is_connected():
            mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)
            mqtt_client.publish("vinyl_guardian/status", "Offline", retain=True)
            mqtt_client.publish("vinyl_guardian/engine_state", "Shut Down", retain=True)
            mqtt_client.publish("vinyl_guardian/track", "Offline", retain=True)
            mqtt_client.publish("vinyl_guardian/scrobble_status", "Offline", retain=True)
            mqtt_client.publish("vinyl_guardian/progress", "Offline", retain=True)
            mqtt_client.publish("vinyl_guardian/raw_volume", "0.0", retain=True)
            mqtt_client.publish("vinyl_guardian/raw_pitch", "0.0", retain=True)
            mqtt_client.publish("vinyl_guardian/raw_texture", "0.0", retain=True)
            mqtt_client.publish("vinyl_guardian/power_score", "0", retain=True)
            mqtt_client.publish("vinyl_guardian/runout_rpm", "None", retain=True)
            mqtt_client.publish("vinyl_guardian/runout_confidence", "0.0", retain=True)
            mqtt_client.publish("vinyl_guardian/music_energy", "0.0", retain=True)
            mqtt_client.publish("vinyl_guardian/pop_texture", "0.0", retain=True)
            mqtt_client.publish("vinyl_guardian/pop_volume", "0.0", retain=True)
            offline = mqtt_client.publish(
                AVAILABILITY_TOPIC,
                "offline",
                qos=1,
                retain=True,
            )
            try:
                offline.wait_for_publish(timeout=2.0)
            except Exception:
                pass

        mqtt_client.disconnect()
        mqtt_client.loop_stop()
    except Exception as e:
        log(f"⚠️ Error during shutdown: {e}")
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# --- MQTT SETUP & CALLBACKS ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

def on_message(client, userdata, msg):
    global debug_countdown, debug_metrics_buffer
    global requested_diagnostic_mode, requested_diagnostic_action
    global capture_false_positive_requested, capture_missed_music_requested
    global manual_label_requested, selected_ground_truth_label
    global replay_latest_requested, rollback_profile_requested
    global audio_scan_requested, requested_audio_source_option

    if msg.topic == 'vinyl_guardian/diagnostics/mode/set':
        if not msg.retain:
            selected = msg.payload.decode('utf-8', errors='ignore').strip()
            requested_diagnostic_mode = next((mode for mode,name in MODE_NAMES.items() if name == selected), None)
        return
    if msg.topic in ('vinyl_guardian/diagnostics/intentional', 'vinyl_guardian/diagnostics/finish'):
        if not msg.retain:
            requested_diagnostic_action = 'intentional_action' if msg.topic.endswith('/intentional') else 'finish_session'
        return
    if msg.topic == "vinyl_guardian/calibration/continue":
        if not msg.retain:
            calibration_control.confirm()
        return
    if msg.topic == "vinyl_guardian/debug/trigger":
        target_chunks = int(RATE / CHUNK * 10.0)
        log(f"🐞 Live Debug Triggered! Capturing 10 seconds ({target_chunks} chunks) of motor profile...")
        debug_metrics_buffer = {'rms': [], 'hfer': [], 'crest': []}
        debug_countdown = target_chunks
    elif msg.topic == "vinyl_guardian/debug/false_positive":
        capture_false_positive_requested = True
        log("👻 False-positive marker received. Saving the recent audio context...")
    elif msg.topic == "vinyl_guardian/debug/missed_music":
        capture_missed_music_requested = True
        log("🎵 Missed-music marker received. Saving the recent audio context...")
    elif msg.topic == "vinyl_guardian/label/set":
        label = msg.payload.decode("utf-8", errors="ignore").strip().lower()
        if label in TRUSTED_LABELS:
            selected_ground_truth_label = label
            client.publish("vinyl_guardian/label/current", label, retain=True)
            log(f"🏷️ Ground-truth selector set to: {label}")
    elif msg.topic == "vinyl_guardian/label/mark":
        manual_label_requested = selected_ground_truth_label
        log(f"🏷️ Ground-truth mark requested: {selected_ground_truth_label}")
    elif msg.topic == "vinyl_guardian/experiment/replay_latest":
        replay_latest_requested = True
        log("⏩ Replay-latest request received.")
    elif msg.topic == "vinyl_guardian/profile/rollback":
        rollback_profile_requested = True
        log("↩️ Profile rollback request received.")
    elif msg.topic == "vinyl_guardian/audio/scan":
        audio_scan_requested = True
        log("🎚️ Audio-input scan requested. Keep music playing during the scan.")
    elif msg.topic == "vinyl_guardian/audio/source/set":
        requested_audio_source_option = msg.payload.decode(
            "utf-8", errors="ignore"
        ).strip()
        log(f"🎚️ Audio source selection requested: {requested_audio_source_option}")

def _mqtt_failed(reason_code):
    return bool(getattr(reason_code, "is_failure", False)) or (
        isinstance(reason_code, int) and reason_code != 0
    )


def on_connect(client, userdata, flags, reason_code, properties):
    if _mqtt_failed(reason_code):
        log(f"🚨 MQTT connection rejected: {reason_code}")
        return
    log("✅ MQTT connected. Restoring subscriptions and discovery.")
    client.publish(AVAILABILITY_TOPIC, "online", retain=True)
    subscribe_commands(client)
    publish_discovery()
    publish_runtime_snapshot()
    refresh_audio_source_select()
    publish_audio_source_state()


def on_connect_fail(client, userdata):
    log("⚠️ MQTT broker unavailable. Guardian will keep retrying in the background.")


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    if _mqtt_failed(reason_code):
        log(f"⚠️ MQTT disconnected ({reason_code}); automatic reconnect active.")


def publish_discovery():
    log("Publishing MQTT Auto-Discovery payloads...")
    device_info = {"identifiers": ["vinyl_guardian_01"], "name": "Vinyl Guardian", "manufacturer": "Custom Add-on"}
    mqtt_client.publish(
        "homeassistant/button/vinyl_guardian/calibration_continue/config",
        json.dumps(add_availability({
            "name": "Continue Calibration",
            "unique_id": "vinyl_guardian_calibration_continue",
            "command_topic": "vinyl_guardian/calibration/continue",
            "device": device_info,
            "icon": "mdi:play",
        })),
        retain=True,
    )
    mqtt_client.publish(
        "homeassistant/sensor/vinyl_guardian/calibration_step/config",
        json.dumps(add_availability({
            "name": "Calibration Instructions",
            "unique_id": "vinyl_guardian_calibration_step",
            "state_topic": "vinyl_guardian/calibration/step",
            "json_attributes_topic": "vinyl_guardian/calibration/details",
            "device": device_info,
            "icon": "mdi:clipboard-list",
        })),
        retain=True,
    )
    mqtt_client.publish(
        "homeassistant/sensor/vinyl_guardian/stylus_usage/config",
        json.dumps(add_availability({
            "name": "Stylus Use",
            "unique_id": "vinyl_guardian_stylus_usage",
            "device": device_info,
            "state_topic": "vinyl_guardian/stylus_usage",
            "json_attributes_topic": "vinyl_guardian/stylus_usage/attributes",
            "device_class": "duration",
            "unit_of_measurement": "h",
            "state_class": "total_increasing",
            "suggested_display_precision": 2,
            "icon": "mdi:timer-outline",
        })),
        retain=True,
    )
    if stylus_usage is not None:
        mqtt_client.publish('vinyl_guardian/stylus_usage', f'{stylus_usage.saved_hours:.6f}', retain=True)
    deprecated_sensors = ["music_rms", "rumble_rms", "scrobble", "scrobble_countdown", "scrobble_state"]
    for old_sensor in deprecated_sensors:
        mqtt_client.publish(f"homeassistant/sensor/vinyl_guardian/{old_sensor}/config", "", retain=True)
    
    configs = {
        "power": {"name": "Turntable Power", "topic": "power", "icon": "mdi:power", "domain": "binary_sensor"},
        "status": {"name": "Vinyl Status", "topic": "status", "icon": "mdi:record-player", "domain": "sensor"},
        "engine": {"name": "Guardian Engine State", "topic": "engine_state", "icon": "mdi:cpu-64-bit", "domain": "sensor"},
        "track": {"name": "Vinyl Current Track", "topic": "track", "icon": "mdi:music-circle", "attr": True, "domain": "sensor"},
        "scrobble_status": {"name": "Scrobble Status", "topic": "scrobble_status", "icon": "mdi:lastpass", "domain": "sensor"},
        "progress": {"name": "Vinyl Track Progress", "topic": "progress", "icon": "mdi:clock-outline", "domain": "sensor"},
        "raw_volume": {"name": "Guardian Vol (Target 0-100)", "topic": "raw_volume", "icon": "mdi:volume-high", "domain": "sensor", "state_class": "measurement"},
        "raw_pitch": {"name": "Guardian Pitch (Target 0-100)", "topic": "raw_pitch", "icon": "mdi:sine-wave", "domain": "sensor", "state_class": "measurement"},
        "raw_texture": {"name": "Guardian Texture (Target 0-100)", "topic": "raw_texture", "icon": "mdi:chart-timeline-variant", "domain": "sensor", "state_class": "measurement"},
        "power_score": {"name": "Guardian Motor Confidence", "topic": "power_score", "icon": "mdi:gauge", "domain": "sensor", "state_class": "measurement"},
        "runout_rpm": {"name": "Runout Speed", "topic": "runout_rpm", "icon": "mdi:rotate-right", "domain": "sensor"},
        "runout_confidence": {"name": "Runout Rhythm Confidence", "topic": "runout_confidence", "icon": "mdi:pulse", "domain": "sensor", "state_class": "measurement"},
        "runout_estimated_rpm": {"name": "Runout Estimated RPM", "topic": "runout_estimated_rpm", "icon": "mdi:speedometer", "domain": "sensor", "state_class": "measurement"},
        "runout_jitter": {"name": "Runout Phase Jitter", "topic": "runout_jitter", "icon": "mdi:chart-timeline-variant-shimmer", "domain": "sensor", "state_class": "measurement"},
        "hardware_mode": {"name": "Guardian Input Mode", "topic": "hardware_mode", "icon": "mdi:audio-input-stereo-minijack", "attr": True, "attr_topic": "hardware_health", "domain": "sensor"},
        "stereo_correlation": {"name": "Guardian L/R Correlation", "topic": "stereo_correlation", "icon": "mdi:compare-horizontal", "domain": "sensor", "state_class": "measurement"},
        "side_session": {"name": "Vinyl Side Session", "topic": "side_session", "icon": "mdi:album", "attr": True, "attr_topic": "side_session_attributes", "domain": "sensor"},
        "experiment_status": {"name": "Guardian Experiment Harness", "topic": "experiment_status", "icon": "mdi:flask-outline", "attr": True, "attr_topic": "experiment_attributes", "domain": "sensor"},
        "shadow_disagreement": {"name": "Guardian Shadow Disagreement", "topic": "shadow_disagreement", "icon": "mdi:source-branch", "domain": "sensor"},
        "runout_support": {"name": "Runout Aligned Clicks", "topic": "runout_support", "icon": "mdi:counter", "domain": "sensor", "state_class": "measurement"},
        "replay_status": {"name": "Guardian Replay Lab", "topic": "replay_status", "icon": "mdi:fast-forward", "domain": "sensor"},
        "active_profile": {"name": "Guardian Active Profile", "topic": "active_profile", "icon": "mdi:restore", "domain": "sensor"},
        "audio_input": {"name": "Guardian Audio Input", "topic": "audio/source/current", "icon": "mdi:audio-input-stereo-minijack", "attr": True, "attr_topic": "audio/source/attributes", "domain": "sensor"},
        "audio_scan_status": {"name": "Guardian Audio Scan", "topic": "audio/scan_status", "icon": "mdi:waveform", "domain": "sensor"},
        "music_energy": {"name": "Guardian Music Energy (Target 100+)", "topic": "music_energy", "icon": "mdi:music-note", "domain": "sensor", "state_class": "measurement"},
        "pop_texture": {"name": "Guardian Pop Texture (Target 100+)", "topic": "pop_texture", "icon": "mdi:waveform", "domain": "sensor", "state_class": "measurement"},
        "pop_volume": {"name": "Guardian Pop Volume (Target 100+)", "topic": "pop_volume", "icon": "mdi:volume-source", "domain": "sensor", "state_class": "measurement"}
    }
    
    for key, c in configs.items():
        payload = {"name": c["name"], "state_topic": f"vinyl_guardian/{c['topic']}", "unique_id": f"vinyl_guardian_{key}", "device": device_info, "icon": c["icon"]}
        if c.get("attr"):
            payload["json_attributes_topic"] = f"vinyl_guardian/{c.get('attr_topic', 'attributes')}"
        if c.get("state_class"): payload["state_class"] = c["state_class"]
        if c["domain"] == "binary_sensor":
            payload["payload_on"] = "ON"
            payload["payload_off"] = "OFF"
        mqtt_client.publish(
            f"homeassistant/{c['domain']}/vinyl_guardian/{key}/config",
            json.dumps(add_availability(payload)),
            retain=True,
        )
        
    btn_payload = {
        "name": "Live Debug Dump",
        "command_topic": "vinyl_guardian/debug/trigger",
        "unique_id": "vinyl_guardian_debug_btn",
        "device": device_info,
        "icon": "mdi:bug"
    }
    mqtt_client.publish("homeassistant/button/vinyl_guardian/debug/config", json.dumps(add_availability(btn_payload)), retain=True)

    feedback_buttons = {
        "false_positive": {
            "name": "Mark False Positive",
            "topic": "vinyl_guardian/debug/false_positive",
            "icon": "mdi:ghost-off-outline",
        },
        "missed_music": {
            "name": "Mark Missed Music",
            "topic": "vinyl_guardian/debug/missed_music",
            "icon": "mdi:music-note-off",
        },
    }
    for key, button in feedback_buttons.items():
        payload = {
            "name": button["name"],
            "command_topic": button["topic"],
            "unique_id": f"vinyl_guardian_{key}_btn",
            "device": device_info,
            "icon": button["icon"],
        }
        mqtt_client.publish(
            f"homeassistant/button/vinyl_guardian/{key}/config",
            json.dumps(add_availability(payload)),
            retain=True,
        )

    label_select = {
        "name": "Ground Truth Label",
        "command_topic": "vinyl_guardian/label/set",
        "state_topic": "vinyl_guardian/label/current",
        "options": sorted(TRUSTED_LABELS),
        "unique_id": "vinyl_guardian_ground_truth_label",
        "device": device_info,
        "icon": "mdi:tag-check-outline",
    }
    mqtt_client.publish(
        "homeassistant/select/vinyl_guardian/ground_truth/config",
        json.dumps(add_availability(label_select)),
        retain=True,
    )

    if audio_source_manager is not None:
        audio_options = audio_source_manager.selectable_options()
        audio_select = {
            "name": "Guardian Audio Source",
            "command_topic": "vinyl_guardian/audio/source/set",
            "state_topic": "vinyl_guardian/audio/source/selection",
            "options": audio_options,
            "unique_id": "vinyl_guardian_audio_source_select",
            "device": device_info,
            "icon": "mdi:audio-input-stereo-minijack",
        }
        mqtt_client.publish(
            "homeassistant/select/vinyl_guardian/audio_source/config",
            json.dumps(add_availability(audio_select)),
            retain=True,
        )

    mqtt_client.publish(
        "homeassistant/select/vinyl_guardian/diagnostic_mode/config",
        json.dumps(add_availability({
            "name": "Automatic Diagnostic Capture Mode",
            "unique_id": "vinyl_guardian_diagnostic_mode",
            "device": device_info,
            "icon": "mdi:record-rec",
            "command_topic": "vinyl_guardian/diagnostics/mode/set",
            "state_topic": "vinyl_guardian/diagnostics/mode",
            "options": list(MODE_NAMES.values()),
        })),
        retain=True,
    )
    experiment_buttons = {
        'intentional_action': {'name': 'Mark Intentional Flip or Pause', 'topic': 'vinyl_guardian/diagnostics/intentional', 'icon': 'mdi:album'},
        'finish_listening_report': {'name': 'Finish Listening Session Report', 'topic': 'vinyl_guardian/diagnostics/finish', 'icon': 'mdi:check-circle-outline'},
        "find_audio_input": {
            "name": "Find Audio Input — Play Music",
            "topic": "vinyl_guardian/audio/scan",
            "icon": "mdi:audio-input-stereo-minijack",
        },
        "mark_ground_truth": {
            "name": "Mark Ground Truth Now",
            "topic": "vinyl_guardian/label/mark",
            "icon": "mdi:tag-plus-outline",
        },
        "replay_latest": {
            "name": "Replay Latest Dataset",
            "topic": "vinyl_guardian/experiment/replay_latest",
            "icon": "mdi:fast-forward",
        },
        "rollback_profile": {
            "name": "Rollback Detector Profile",
            "topic": "vinyl_guardian/profile/rollback",
            "icon": "mdi:backup-restore",
        },
    }
    for key, button in experiment_buttons.items():
        mqtt_client.publish(
            f"homeassistant/button/vinyl_guardian/{key}/config",
            json.dumps(add_availability({
                "name": button["name"],
                "command_topic": button["topic"],
                "unique_id": f"vinyl_guardian_{key}_btn",
                "device": device_info,
                "icon": button["icon"],
            })),
            retain=True,
        )

    mqtt_client.publish(
        "vinyl_guardian/label/current",
        selected_ground_truth_label,
        retain=True,
    )

def publish_runtime_snapshot():
    """Restore retained state after first connect or broker reconnect."""
    if not mqtt_client.is_connected():
        return
    with state_lock:
        status = current_display_status
        engine = current_engine_status
        runtime_state = app_state
        track_snapshot = dict(current_track) if current_track else None
        suppress_track = bool(track_display_suppressed)

    mqtt_client.publish(AVAILABILITY_TOPIC, "online", retain=True)
    if CALIBRATION_MODE:
        mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)
        mqtt_client.publish("vinyl_guardian/status", "Calibrating", retain=True)
        mqtt_client.publish("vinyl_guardian/engine_state", "Calibration Mode", retain=True)
        mqtt_client.publish("vinyl_guardian/track", "Calibration Mode", retain=True)
        mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
        mqtt_client.publish(
            "vinyl_guardian/scrobble_status",
            "Calibration Mode",
            retain=True,
        )
        mqtt_client.publish(
            "vinyl_guardian/progress",
            "Calibration Mode",
            retain=True,
        )
        return

    power = "OFF" if status in ("Powered Off", "Offline") else "ON"
    mqtt_client.publish("vinyl_guardian/power", power, retain=True)
    mqtt_client.publish("vinyl_guardian/status", status, retain=True)
    mqtt_client.publish("vinyl_guardian/engine_state", engine, retain=True)

    track_state, track_attributes = current_track_presentation(
        status,
        runtime_state,
        track_snapshot,
        suppress_track=suppress_track,
    )
    mqtt_client.publish("vinyl_guardian/track", track_state, retain=True)
    mqtt_client.publish(
        "vinyl_guardian/attributes",
        json.dumps(track_attributes),
        retain=True,
    )


def connect_mqtt():
    try:
        configure_client(
            mqtt_client,
            MQTT_BROKER,
            MQTT_PORT,
            username=MQTT_USER,
            password=MQTT_PASS,
            on_connect=on_connect,
            on_connect_fail=on_connect_fail,
            on_disconnect=on_disconnect,
            on_message=on_message,
        )
        log("📡 MQTT network loop started; first connection will retry automatically.")
    except Exception as e:
        log(f"🚨 MQTT setup failed: {e}")

def change_3_tier_status(new_vinyl_status, new_engine_status):
    global current_display_status, current_engine_status
    previous_status = current_display_status
    status_changed = new_vinyl_status != current_display_status
    engine_changed = new_engine_status != current_engine_status
    current_display_status = new_vinyl_status
    current_engine_status = new_engine_status

    if not CALIBRATION_MODE and mqtt_client.is_connected():
        if status_changed:
            mqtt_client.publish("vinyl_guardian/status", new_vinyl_status, retain=True)
        if engine_changed:
            mqtt_client.publish("vinyl_guardian/engine_state", new_engine_status, retain=True)
    return previous_status, status_changed

def _track_id(track):
    if not isinstance(track, dict):
        return ""
    return f"{track.get('title', '')} - {track.get('artist', '')}".strip(" -")


def _track_duration(match):
    duration = float((match or {}).get("duration") or 0.0)
    if duration <= 0 and match:
        duration = float(get_track_duration(
            match.get("title", ""),
            match.get("artist", ""),
            match.get("adamid"),
            album=match.get("album"),
        ) or 0.0)
    return duration


def _make_track(match, session_start, start_timestamp, confidence, support=1,
                conflicts=0, stage_seconds=0, previously_played=0.0):
    duration = _track_duration(match)
    if duration <= 0:
        duration = 0.0
        duration_known = False
        scrobble_delay = UNKNOWN_DURATION_SCROBBLE_SECONDS
    else:
        duration_known = True
        scrobble_delay = min(duration / 2.0, 240.0)

    previously_played = float(previously_played or 0.0)
    if previously_played:
        scrobble_delay = max(2.0, scrobble_delay - previously_played)

    track = {
        "title": match.get("title", "Unknown"),
        "artist": match.get("artist", "Unknown"),
        "album": match.get("album", "Unknown"),
        "duration": duration,
        "start_timestamp": float(start_timestamp),
        "session_start_time": float(session_start),
        "scrobble_trigger_time": float(session_start) + scrobble_delay,
        "duration_known": duration_known,
        "previously_played": previously_played,
        "source": "Shazam",
        "recognition_status": "confirmed",
        "recognition_stage_seconds": int(stage_seconds or 0),
        "recognition_confidence": str(confidence or "low"),
        "recognition_support": int(support or 0),
        "recognition_conflicts": int(conflicts or 0),
        "recognition_verified": str(confidence or "") == "high",
        "identity_key": identity_key(match),
        "image": match.get("image", ""),
        "adamid": match.get("adamid"),
        "album_adamid": match.get("album_adamid"),
        "shazam_key": match.get("shazam_key"),
        "scrobble_fired": False,
    }
    if duration_known:
        track["expected_end_time"] = float(start_timestamp) + duration
    context_conflict = album_identity_guard.conflict(track, float(session_start))
    if context_conflict:
        track["identity_context_conflict"] = context_conflict
        track["scrobble_pending_reason"] = context_conflict["reason"]
        log(f"⚖️ Holding scrobble for {_track_id(track)}: {context_conflict['reason']}.")
    return track


def _enrich_track_catalogue(track_snapshot):
    """Attach expected-next album evidence without blocking recognition."""
    if not isinstance(track_snapshot, dict):
        return
    album_adamid = track_snapshot.get("album_adamid")
    if album_adamid in (None, ""):
        return
    expected = get_expected_next_track(
        album_adamid,
        current_adamid=track_snapshot.get("adamid"),
        title=track_snapshot.get("title", ""),
        artist=track_snapshot.get("artist", ""),
    )
    if not expected:
        return

    updated = None
    with state_lock:
        if (
            current_track is not None
            and identity_key(current_track) == identity_key(track_snapshot)
            and float(current_track.get("session_start_time") or 0.0)
                == float(track_snapshot.get("session_start_time") or 0.0)
        ):
            current_track["expected_next"] = expected
            track_monitor.update_track_metadata(current_track)
            updated = dict(current_track)
    if updated:
        log(
            f"🧭 Album sequence hint: after {_track_id(updated)} expect "
            f"{expected.get('title', 'next track')} - {expected.get('artist', '')}"
        )
        _publish_track(updated)


def _start_track_enrichment(track):
    if isinstance(track, dict) and track.get("album_adamid") not in (None, ""):
        threading.Thread(
            target=_enrich_track_catalogue,
            args=(dict(track),),
            daemon=True,
        ).start()


def _publish_track(track, verified=False):
    global track_display_suppressed
    if not isinstance(track, dict):
        return
    guard = globals().get("album_identity_guard")
    if guard is not None:
        guard.observe(track)
    with state_lock:
        if verified:
            track_display_suppressed = False
        status = current_display_status
        runtime_state = app_state
        suppress_track = bool(track_display_suppressed)
    if not mqtt_client.is_connected():
        return
    track_state, track_attributes = current_track_presentation(
        status,
        runtime_state,
        track,
        suppress_track=suppress_track,
    )
    mqtt_client.publish("vinyl_guardian/track", track_state, retain=True)
    mqtt_client.publish(
        "vinyl_guardian/attributes",
        json.dumps(track_attributes),
        retain=True,
    )


def _on_scrobble_success(row):
    global last_scrobbled_track
    track = dict(row.get("track") or {})
    event_id = row.get("event_id")
    track_id = _track_id(track)
    is_current = False
    with state_lock:
        if (
            current_track is not None
            and current_track.get("scrobble_event_id") == event_id
        ):
            current_track["scrobble_delivered"] = True
            current_track["scrobble_retry_attempts"] = int(row.get("attempts", 0))
            is_current = True
        last_scrobbled_track = track_id or last_scrobbled_track
    if mqtt_client.is_connected() and track_id:
        mqtt_client.publish("vinyl_guardian/scrobble_state", track_id, retain=True)
        mqtt_client.publish("vinyl_guardian/scrobble", json.dumps(track), retain=True)
        if is_current:
            mqtt_client.publish(
                "vinyl_guardian/scrobble_status",
                f"Scrobbled: {track.get('title', 'Track')} ✅",
                retain=True,
            )


def _on_scrobble_retry(row):
    event_id = row.get("event_id")
    attempts = int(row.get("attempts", 0))
    is_current = False
    with state_lock:
        if (
            current_track is not None
            and current_track.get("scrobble_event_id") == event_id
        ):
            current_track["scrobble_retry_attempts"] = attempts
            is_current = True
    if mqtt_client.is_connected() and is_current:
        mqtt_client.publish(
            "vinyl_guardian/scrobble_status",
            f"Last.fm retry queued · attempt {attempts + 1} ⏳",
            retain=True,
        )


def initialise_scrobble_dispatcher():
    global scrobble_dispatcher
    scrobble_dispatcher = ScrobbleDispatcher(
        "/data/pending_scrobbles.json",
        scrobble_to_lastfm,
        on_success=_on_scrobble_success,
        on_retry=_on_scrobble_retry,
        logger=log,
        enabled=lastfm_enabled(),
    )
    if lastfm_enabled():
        log(
            f"🎵 Last.fm delivery queue ready "
            f"({scrobble_dispatcher.pending_count} pending)."
        )
    else:
        log("ℹ️ Last.fm not configured; scrobble delivery queue disabled.")


def _send_scrobble(track, mark_current=False):
    global scrobble_fired, paused_track_memory
    if not isinstance(track, dict) or track.get("scrobble_fired"):
        return False
    # Final gate also covers delayed tracks promoted by boundary evidence.
    # Keep this before dispatcher access so a suspect identity can never leak
    # through a delayed completion path.
    if track.get("identity_context_conflict"):
        return False
    if (
        scrobble_dispatcher is None
        or not scrobble_dispatcher.enabled
    ):
        return False
    event_id = scrobble_event_id(track)
    if not event_id:
        return False
    already_delivered = scrobble_dispatcher.is_sent(event_id)
    # Set delivery metadata before waking the worker so a very fast Last.fm
    # response cannot beat the current-track bookkeeping.
    track["scrobble_fired"] = True
    track["scrobble_queued"] = not already_delivered
    track["scrobble_delivered"] = already_delivered
    track["scrobble_event_id"] = event_id
    track["scrobble_retry_attempts"] = 0
    queued_id = scrobble_dispatcher.submit(track)
    if not queued_id:
        track["scrobble_fired"] = False
        track["scrobble_queued"] = False
        track.pop("scrobble_event_id", None)
        return False
    if already_delivered:
        if mark_current:
            scrobble_fired = True
            paused_track_memory = None
        return True
    if mark_current:
        scrobble_fired = True
        paused_track_memory = None
    if mqtt_client.is_connected():
        mqtt_client.publish(
            "vinyl_guardian/scrobble_status",
            "Queued for Last.fm ⏳",
            retain=True,
        )
    return True


def _extract_audio_window(ring, start_time, end_time):
    chunks = [
        payload
        for stamp, payload in ring
        if float(start_time) <= float(stamp) <= float(end_time) + (CHUNK / RATE)
    ]
    raw = b"".join(chunks)
    requested_seconds = max(0.0, float(end_time) - float(start_time))
    max_bytes = int(requested_seconds * RATE * CHANNELS * 2)
    return raw[:max_bytes] if max_bytes > 0 else b""


# --- BACKGROUND WORKER (SHAZAM) ---
def process_audio_background(audio_data_bytes, song_start_timestamp, token, stage_seconds):
    global app_state, current_attempt, wake_up_time, consecutive_failures, current_track
    global scrobble_fired, paused_track_memory, experiment_harness

    stage_seconds = int(stage_seconds)
    with state_lock:
        if not recognition_session.valid(token, app_state):
            return
        local_attempt = current_attempt

    seconds = len(audio_data_bytes) / (RATE * CHANNELS * 2)
    log(
        f"🔬 Analyzing {seconds:.1f}s initial recognition "
        f"({stage_seconds}s, Attempt {local_attempt}/{MAX_ATTEMPTS})..."
    )
    try:
        match, trimmed_seconds = recognize_fragment(
            audio_data_bytes,
            RECORDING_DIR,
            RATE,
            CHANNELS,
            AUDIO_ONSET_THRESHOLD,
            min(float(MIN_AUDIO_SECONDS), float(stage_seconds)),
            recognize_shazam,
        )
    except Exception as exc:
        log(f"⚠️ Recognition failed at {stage_seconds}s: {exc}")
        match, trimmed_seconds = None, 0.0

    with state_lock:
        outcome = recognition_session.record_result(
            token,
            app_state,
            stage_seconds,
            match,
            trimmed_seconds,
        )
        if not outcome["accepted"]:
            return

        if outcome.get("display") and match:
            display_track = dict(
                match,
                source="Shazam",
                recognition_status="provisional",
                recognition_stage_seconds=stage_seconds,
                recognition_confidence=outcome.get("confidence", "low"),
                recognition_support=outcome.get("support", 1),
                recognition_conflicts=outcome.get("conflicts", 0),
            )
            log(
                f"🎶 {stage_seconds}s MATCH: "
                f"{match['title']} - {match['artist']} "
                f"({outcome.get('confidence', 'low')} confidence)"
            )
            if mqtt_client.is_connected():
                if current_display_status == "Runout Groove" or track_display_suppressed:
                    mqtt_client.publish(
                        "vinyl_guardian/track",
                        "Not Playing",
                        retain=True,
                    )
                    mqtt_client.publish(
                        "vinyl_guardian/attributes",
                        "{}",
                        retain=True,
                    )
                else:
                    mqtt_client.publish(
                        "vinyl_guardian/track",
                        f"{match['title']} - {match['artist']}",
                        retain=True,
                    )
                    mqtt_client.publish(
                        "vinyl_guardian/attributes",
                        json.dumps(display_track),
                        retain=True,
                    )

        if not outcome.get("finalize"):
            return

        best_match = outcome.get("best_match")
        best_stage = int(outcome.get("best_stage") or 0)
        best_trimmed = float(outcome.get("best_trimmed_seconds") or 0.0)
        confidence = str(outcome.get("confidence") or "low")
        support = int(outcome.get("support") or 0)
        conflicts = int(outcome.get("conflicts") or 0)

        paused = dict(paused_track_memory) if paused_track_memory else None

    if best_match:
        track_id = f"{best_match.get('title', '')} - {best_match.get('artist', '')}".strip(" -")
        previously_played = 0.0
        if paused and paused.get("id") == track_id:
            previously_played = float(paused.get("accumulated_playtime") or 0.0)
            log(f"▶️ Resuming track! Recovered {int(previously_played)}s playtime.")

        raw_offset = float(best_match.get("offset_seconds") or 0.0)
        start_ts = float(song_start_timestamp) + best_trimmed - raw_offset
        if start_ts < 0:
            start_ts = float(song_start_timestamp)

        new_track = _make_track(
            best_match,
            song_start_timestamp,
            start_ts,
            confidence,
            support=support,
            conflicts=conflicts,
            stage_seconds=best_stage,
            previously_played=previously_played,
        )

        with state_lock:
            if not recognition_session.valid(token, app_state):
                return
            current_attempt = 1
            consecutive_failures = 0
            current_track = new_track
            scrobble_fired = False
            if not paused or paused.get("id") != track_id:
                paused_track_memory = None
            track_monitor.begin_track(current_track)
            wake_up_time = (
                current_track.get("expected_end_time")
                or (time.time() + float(current_track.get("duration") or 1200))
            )
            app_state = "SLEEPING"

        resolved = pending_scrobbles.resolve_with_successor(
            new_track,
            new_track.get("session_start_time", song_start_timestamp),
            strong_boundary=True,
        )
        for old_track in resolved:
            _send_scrobble(old_track, mark_current=False)

        if experiment_harness is not None:
            try:
                experiment_harness.track_identified(new_track, now=time.time())
            except Exception as exc:
                log(f"⚠️ Experiment track logging failed: {exc}")

        log(
            f"✅ TRACK: {best_match['title']} - {best_match['artist']} "
            f"({confidence}, {support} agreeing / {conflicts} conflicting)"
        )
        _publish_track(new_track, verified=True)
        _start_track_enrichment(new_track)
    else:
        with state_lock:
            if not recognition_session.valid(token, app_state):
                return
            if current_attempt < MAX_ATTEMPTS:
                log(
                    f"❌ No Shazam match across 3/5/10s. "
                    f"Retrying with fresh audio ({current_attempt + 1}/{MAX_ATTEMPTS})..."
                )
                current_attempt += 1
                app_state = "IDLE"
            else:
                consecutive_failures += 1
                log("❌ Max staged attempts reached. Fallback to gap detection.")
                if mqtt_client.is_connected():
                    mqtt_client.publish("vinyl_guardian/track", "Unknown Track", retain=True)
                    mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                current_attempt = 1
                wake_up_time = time.time() + (
                    CONSECUTIVE_FAILURE_TIMEOUT
                    if consecutive_failures >= 10
                    else FALLBACK_SLEEP_SECS
                )
                if consecutive_failures >= 10:
                    consecutive_failures = 0
                app_state = "SLEEPING"

    if TEST_CAPTURE_MODE:
        log("🛑 TEST CAPTURE COMPLETE.")
        os._exit(0)


def process_tracking_audio_background(audio_data_bytes, window_start_timestamp, request_id):
    """Evaluate a <=10s verification/boundary window without blocking audio."""
    global current_track, wake_up_time, scrobble_fired, paused_track_memory

    with state_lock:
        request = dict(track_monitor.requests.get(request_id) or {})
        if not request or app_state != "SLEEPING" or current_track is None:
            return
        generation = request.get("generation")

    seconds = len(audio_data_bytes) / (RATE * CHANNELS * 2)
    if seconds < 2.0:
        with state_lock:
            track_monitor.record_result(request_id, None)
            if request.get("kind") == "periodic" and current_track is not None and generation == track_monitor.generation:
                current_track["duration_recheck_pending"] = False
                current_track["duration_recheck_confirmed_at"] = None
        return

    try:
        match, trimmed_seconds = recognize_fragment(
            audio_data_bytes,
            RECORDING_DIR,
            RATE,
            CHANNELS,
            AUDIO_ONSET_THRESHOLD,
            min(float(MIN_AUDIO_SECONDS), seconds),
            recognize_shazam,
        )
    except Exception as exc:
        log(f"⚠️ Tracking recognition failed ({request.get('name')}): {exc}")
        match, trimmed_seconds = None, 0.0

    with state_lock:
        if (
            app_state != "SLEEPING"
            or current_track is None
            or generation != track_monitor.generation
        ):
            return
        action = track_monitor.record_result(request_id, match)
        if not action.get("accepted"):
            return
        action_name = action.get("action")
        old_snapshot = dict(current_track)

    if action_name == "duration_rechecked":
        with state_lock:
            if current_track is None or generation != track_monitor.generation:
                return
            current_track["duration_recheck_pending"] = False
            current_track["duration_recheck_confirmed_at"] = request["end"] if action["same"] else None
            if action["same"]:
                current_track["recognition_verified"] = True
                current_track["recognition_confidence"] = "high"
                if not current_track.get("identity_context_conflict"):
                    current_track.pop("scrobble_pending_reason", None)
            updated = dict(current_track)
        if action["same"]:
            log(f"✅ Unknown-duration recheck: still {_track_id(updated)}.")
        elif action.get("match"):
            log("🔎 Unknown-duration recheck heard a different song; confirming with fresh audio.")
        else:
            log("🔎 Unknown-duration recheck had no match; waiting for fresh confirmation before scrobbling.")
        _publish_track(updated)
        return

    if action_name == "verified":
        with state_lock:
            if current_track is None or generation != track_monitor.generation:
                return
            current_track["recognition_confidence"] = "high"
            current_track["recognition_verified"] = True
            current_track["recognition_conflicts"] = int(current_track.get("recognition_conflicts", 0))
            updated = dict(current_track)
        log(f"✅ Identity verified from fresh audio: {_track_id(updated)}")
        _publish_track(updated, verified=True)
        return

    if action_name == "hold_ambiguous":
        with state_lock:
            if current_track is not None and generation == track_monitor.generation:
                current_track["recognition_conflicts"] = int(current_track.get("recognition_conflicts", 0)) + 1
                current_track["scrobble_pending_reason"] = "Recognition evidence is contradictory"
                updated = dict(current_track)
            else:
                updated = None
        if updated:
            log(f"⚖️ Conflicting Shazam evidence retained for {_track_id(updated)}; not rewriting track history.")
            _publish_track(updated)
        return

    if action_name == "correct_identity" and action.get("match"):
        replacement = action["match"]
        session_start = float(old_snapshot.get("session_start_time") or window_start_timestamp)
        replacement_track = _make_track(
            replacement,
            session_start,
            float(old_snapshot.get("start_timestamp") or session_start),
            "high",
            support=2,
            conflicts=int(old_snapshot.get("recognition_conflicts", 0)),
            stage_seconds=10,
            previously_played=float(old_snapshot.get("previously_played") or 0.0),
        )
        replacement_track["scrobble_fired"] = bool(old_snapshot.get("scrobble_fired"))
        with state_lock:
            if current_track is None or generation != track_monitor.generation:
                return
            current_track = replacement_track
            scrobble_fired = replacement_track["scrobble_fired"]
            track_monitor.begin_track(current_track)
            wake_up_time = (
                current_track.get("expected_end_time")
                or time.time() + float(current_track.get("duration") or 1200)
            )
        log(
            f"🔁 Corrected weak initial identity to "
            f"{replacement_track['title']} - {replacement_track['artist']} "
            f"after two fresh agreeing windows."
        )
        _publish_track(replacement_track, verified=True)
        _start_track_enrichment(replacement_track)
        return

    if action_name == "continuation":
        with state_lock:
            if current_track is None or generation != track_monitor.generation:
                return
            current_track["recognition_verified"] = True
            current_track["recognition_confidence"] = "high"
            old_end = expected_end(current_track)
            if old_end is not None and time.time() >= old_end:
                current_track["expected_end_time"] = max(old_end + 15.0, time.time() + 10.0)
                wake_up_time = current_track["expected_end_time"]
            updated = dict(current_track)
        if action.get("reason") == "music_recovery":
            log("↪️ Same song after a quiet passage; preserving playback and scrobble state.")
        elif action.get("reason") == "periodic_identity":
            log("↪️ Fresh audio confirms the current song; retaining its playback state.")
        else:
            log("↪️ Shazam still hears the current track after its predicted end; extending boundary watch.")
        _publish_track(updated, verified=True)
        return

    if action_name == "boundary_unresolved":
        with state_lock:
            if current_track is None or generation != track_monitor.generation:
                return
            old_end = expected_end(current_track)
            if old_end is not None and time.time() >= old_end:
                current_track["expected_end_time"] = max(old_end + 15.0, time.time() + 8.0)
                wake_up_time = current_track["expected_end_time"]
            current_track["scrobble_pending_reason"] = "Boundary not yet resolved"
        context = "Quiet-passage check" if action.get("reason") == "music_recovery" else "Expected boundary"
        log(f"⚖️ {context} was inconclusive; keeping the current track and checking again later.")
        return

    if action_name == "successor" and action.get("match"):
        successor_match = action["match"]
        boundary_time = float(action.get("anchor") or window_start_timestamp)
        successor = _make_track(
            successor_match,
            boundary_time,
            boundary_time,
            action.get("confidence") or "medium",
            support=2 if action.get("confidence") == "high" else 1,
            conflicts=0,
            stage_seconds=int(request.get("stage") or 0),
        )

        physical_end = float(action.get("previous_end") or boundary_time)
        pending_scrobbles.hold(
            old_snapshot,
            ended_at=boundary_time,
            physical_now=physical_end,
            reason=f"successor:{action.get('reason')}",
            completed=True,
        )
        resolved = pending_scrobbles.resolve_with_successor(
            successor,
            boundary_time,
            strong_boundary=True,
        )

        with state_lock:
            if current_track is None or generation != track_monitor.generation:
                return
            current_track = successor
            scrobble_fired = False
            paused_track_memory = None
            track_monitor.begin_track(current_track)
            wake_up_time = (
                current_track.get("expected_end_time")
                or time.time() + float(current_track.get("duration") or 1200)
            )

        for old_track in resolved:
            _send_scrobble(old_track, mark_current=False)

        log(
            f"⏭️ Track boundary confirmed ({action.get('reason')}): "
            f"{successor['title']} - {successor['artist']}"
        )
        if experiment_harness is not None:
            try:
                experiment_harness.track_identified(successor, now=time.time())
            except Exception as exc:
                log(f"⚠️ Experiment track logging failed: {exc}")
        _publish_track(successor, verified=True)
        _start_track_enrichment(successor)

def get_crest(audio_data):
    rms = float(np.sqrt(np.mean(np.square(audio_data))))
    if rms <= 0: return 1.0
    return float(np.max(np.abs(audio_data)) / rms)

def normalize_metric(val, t_min, t_max):
    if t_max - t_min == 0: return 0.0
    norm = ((val - t_min) / (t_max - t_min)) * 100.0
    return max(-50.0, min(150.0, norm))


def save_feedback_clip(kind, chunks):
    if not chunks:
        return None
    try:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        feedback_path = os.path.join(RECORDING_DIR, f"{kind}_{stamp}.wav")
        with wave.open(feedback_path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(RATE)
            wf.writeframes(b"".join(chunks))
        log(
            f"💾 Saved {len(chunks) * CHUNK / RATE:.1f}s feedback clip: "
            f"{os.path.basename(feedback_path)}"
        )
        return feedback_path
    except Exception as e:
        log(f"⚠️ Could not save detector feedback clip: {e}")
        return None


def refresh_audio_source_select():
    if audio_source_manager is None or not mqtt_client.is_connected():
        return
    try:
        device_info = {
            "identifiers": ["vinyl_guardian_01"],
            "name": "Vinyl Guardian",
            "manufacturer": "Custom Add-on",
        }
        payload = {
            "name": "Guardian Audio Source",
            "command_topic": "vinyl_guardian/audio/source/set",
            "state_topic": "vinyl_guardian/audio/source/selection",
            "options": audio_source_manager.selectable_options(),
            "unique_id": "vinyl_guardian_audio_source_select",
            "device": device_info,
            "icon": "mdi:audio-input-stereo-minijack",
        }
        mqtt_client.publish(
            "homeassistant/select/vinyl_guardian/audio_source/config",
            json.dumps(add_availability(payload)),
            retain=True,
        )
    except Exception as e:
        log(f"⚠️ Could not refresh audio source selector: {e}")


def publish_audio_source_state():
    if audio_source_manager is None or not mqtt_client.is_connected():
        return
    try:
        status = audio_source_manager.status()
        source = status.get("source")
        description = status.get("description") or source or "Unavailable"
        mqtt_client.publish(
            "vinyl_guardian/audio/source/current",
            description,
            retain=True,
        )
        mqtt_client.publish(
            "vinyl_guardian/audio/source/attributes",
            json.dumps(status),
            retain=True,
        )
        selection_state = (
            SYSTEM_DEFAULT_OPTION
            if status.get("follow_system_default")
            else (source or SYSTEM_DEFAULT_OPTION)
        )
        if selection_state not in audio_source_manager.selectable_options():
            selection_state = SYSTEM_DEFAULT_OPTION
        mqtt_client.publish(
            "vinyl_guardian/audio/source/selection",
            selection_state,
            retain=True,
        )
        mqtt_client.publish(
            "vinyl_guardian/audio/scan_status",
            audio_scan_status,
            retain=True,
        )
    except Exception as e:
        log(f"⚠️ Could not publish audio source state: {e}")


def audio_scan_progress(message):
    global audio_scan_status
    audio_scan_status = str(message)
    log(f"🎚️ {audio_scan_status}")
    if mqtt_client.is_connected():
        mqtt_client.publish(
            "vinyl_guardian/audio/scan_status",
            audio_scan_status,
            retain=True,
        )


startup_scan_gate = StartupScanGate(SHARE_DIR)
startup_scan_pending = startup_scan_gate.should_run(AUDIO_SCAN_ON_START)

def complete_audio_scan():
    startup_scan_gate.complete(AUDIO_SOURCE)
    try:
        if reset_scan_options():
            log("✅ Audio input remembered. Startup scan switched OFF; input setting is Auto / remembered.")
        else:
            log("✅ Input remembered; repeat startup scans blocked. To re-arm, restart once with scan OFF, then enable it, or use Find Input.")
    except Exception as exc:
        log(f"⚠️ Could not reset scan option in Home Assistant ({type(exc).__name__}); repeat scans remain blocked. Use Find Input to scan manually.")

def initialise_audio_source():
    global audio_source_manager
    audio_source_manager = AudioSourceManager(
        SHARE_DIR,
        rate=RATE,
        channels=CHANNELS,
        scan_seconds=AUDIO_SCAN_SECONDS,
        logger=log,
    )
    try:
        status = audio_source_manager.apply_startup(startup_scan_gate.effective_source(AUDIO_SOURCE))
        chosen = status.get("description") or status.get("source") or "system default"
        log(f"🎚️ Guardian capture source: {chosen}")
    except Exception as e:
        log(f"⚠️ Audio source selection failed; using system default: {e}")
        try:
            audio_source_manager.use_system_default(persist=False)
        except Exception:
            pass


def run_startup_audio_scan():
    global audio_scan_status
    if audio_source_manager is None:
        return False
    audio_scan_status = "Scanning — keep music playing"
    try:
        result = audio_source_manager.scan(progress=audio_scan_progress)
        if result.get("applied"):
            complete_audio_scan()
            winner = result.get("winner") or {}
            audio_scan_status = (
                f"Selected {winner.get('description') or winner.get('source')} "
                f"({winner.get('confidence', 'unknown')} confidence)"
            )
            publish_audio_source_state()
            return True
        audio_scan_status = "No convincing input found"
    except Exception as e:
        audio_scan_status = f"Scan error: {e}"
        log(f"⚠️ Audio-input scan failed: {e}")
    publish_audio_source_state()
    return False


def open_guardian_capture():
    return alsaaudio.PCM(
        type=alsaaudio.PCM_CAPTURE,
        mode=alsaaudio.PCM_NORMAL,
        device="default",
        channels=CHANNELS,
        rate=RATE,
        format=FORMAT,
        periodsize=CHUNK,
    )


def apply_guardian_source_volume():
    target = (
        audio_source_manager.volume_target()
        if audio_source_manager is not None
        else "@DEFAULT_SOURCE@"
    )
    try:
        subprocess.run(
            ["pactl", "set-source-mute", target, "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        subprocess.run(
            ["pactl", "set-source-volume", target, f"{MIC_VOLUME}%"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except Exception:
        pass


def replay_latest_background():
    global replay_status
    replay_status = "Running"
    if mqtt_client.is_connected():
        mqtt_client.publish("vinyl_guardian/replay_status", replay_status, retain=True)
    try:
        result = replay_latest_dataset(RECORDING_DIR, AUTO_CALIB_FILE)
        summary = result.get("summary", {})
        duration = float(summary.get("duration_sec", 0.0))
        transitions = int(summary.get("transition_count", 0))
        replay_status = f"Complete · {duration/60.0:.1f} min · {transitions} transitions"
        log(
            f"⏩ Replay complete: {duration:.1f}s audio, "
            f"{transitions} transitions. Report: {result.get('json_path')}"
        )
        if experiment_harness is not None:
            experiment_harness.timeline.record(
                "replay_complete",
                source=summary.get("source"),
                duration_sec=duration,
                transition_count=transitions,
                report=result.get("json_path"),
            )
    except Exception as e:
        replay_status = f"Error: {e}"
        log(f"⚠️ Replay lab failed: {e}")
        if experiment_harness is not None:
            experiment_harness.timeline.record("replay_failed", error=str(e))
    if mqtt_client.is_connected():
        mqtt_client.publish("vinyl_guardian/replay_status", replay_status, retain=True)


# --- MAIN LOOP ---
def listen_and_identify():
    global app_state, current_attempt, wake_up_time, scrobble_fired, current_track, last_scrobbled_track, paused_track_memory, inp, dataset_collector
    global experiment_harness, profile_manager, manual_label_requested
    global requested_diagnostic_mode, requested_diagnostic_action
    global replay_latest_requested, rollback_profile_requested, replay_status, profile_status_text
    global audio_scan_requested, audio_scan_running, audio_source_change_requested
    global requested_audio_source_option, audio_scan_status
    global debug_countdown, debug_metrics_buffer
    global capture_false_positive_requested, capture_missed_music_requested
    global track_display_suppressed
    
    if DEBUG:
        log(
            f"🔊 Applying tuned mic volume: {MIC_VOLUME}% to "
            f"{audio_source_manager.volume_target() if audio_source_manager else '@DEFAULT_SOURCE@'}"
        )
    apply_guardian_source_volume()

    try:
        inp = open_guardian_capture()
    except Exception as e:
        log(f"🚨 ALSA Error: {e}")
        sys.exit(1)
        
    log("Guardian Engine Online. Shields Armed.")

    dataset_collector = DatasetCollector(
        RECORDING_DIR,
        rate=RATE,
        channels=CHANNELS,
        chunk=CHUNK,
        enabled=DATA_COLLECTION_ENABLED,
        label=DATA_COLLECTION_LABEL,
        raw_audio=DATA_COLLECTION_RAW_AUDIO,
        feature_interval_sec=DATA_COLLECTION_FEATURE_INTERVAL,
        raw_segment_minutes=DATA_COLLECTION_RAW_SEGMENT_MINUTES,
    )
    if DATA_COLLECTION_ENABLED:
        raw_text = " + raw WAV" if DATA_COLLECTION_RAW_AUDIO else ""
        log(
            f"📊 Dataset collection enabled: label='{DATA_COLLECTION_LABEL}', "
            f"interval={DATA_COLLECTION_FEATURE_INTERVAL:.2f}s{raw_text}. "
            f"Path: {dataset_collector.session_dir}"
        )

    last_pub, last_sleep_log, cooldown_end, chunks, loud_chunks, silence_sleep, song_start = time.time(), 0, 0, 0, 0, 0, 0
    target = int(RATE / CHUNK * recognition_session.final_stage)
    trigger_chunks = 0
    buffer = bytearray()
    tracking_audio = deque(maxlen=max(1, int(RATE / CHUNK * 40.0)))
    music_gap_started = None
    feedback_buffer, feedback_max_chunks = [], int(RATE / CHUNK * 20.0)
    
    turntable_on, has_played_music, rhythm_locked = False, False, False
    power_score = 0
    
    engine_state_map = {"IDLE": "Listening", "RECORDING": "Recording", "PROCESSING": "Processing", "SLEEPING": "Tracking", "COOLDOWN": "Cooldown"}
    last_logged_status, last_logged_rhythm = "Unknown", False

    try:
        with open(AUTO_CALIB_FILE, "r") as f:
            v6_cfg = json.load(f)
    except Exception as e:
        log(f"⚠️ WARNING: Could not parse {AUTO_CALIB_FILE} ({e}). Using incredibly wide fallback limits.")
        v6_cfg = {}

    r_min = v6_cfg.get('rms_min', globals().get('MOTOR_POWER_THRESHOLD', 0.0001))
    r_max = v6_cfg.get('rms_max', globals().get('MOTOR_POWER_CEILING', 999.0))
    h_min = v6_cfg.get('hfer_min', 0.0)
    h_max = v6_cfg.get('hfer_max', globals().get('MOTOR_HFER_THRESHOLD', 1.0))
    c_min = v6_cfg.get('crest_min', 0.0)
    c_max = v6_cfg.get('crest_max', 20.0)
    
    m_thresh = v6_cfg.get('music_threshold', globals().get('MUSIC_THRESHOLD', 0.002))
    m_hold_thresh = v6_cfg.get('music_hold_threshold', m_thresh * 0.6)
    runout_crest_thresh = v6_cfg.get('runout_crest_threshold', globals().get('RUNOUT_CREST_THRESHOLD', 3.5))
    pop_amp = v6_cfg.get('pop_amplitude_threshold', globals().get('POP_AMPLITUDE_THRESHOLD', 0.0))
    needle_lift_sec = v6_cfg.get('needle_lift_sec', globals().get('NEEDLE_LIFT_SECONDS', 15.0))

    # V8: one stateful detector owns feature extraction, power hysteresis,
    # music hysteresis and runout rhythm. Calibration replay uses this same
    # class, so passing calibration now means passing production logic.
    detector = GuardianDetector(v6_cfg, rate=RATE, channels=CHANNELS)
    if v6_cfg.get('motor_combination_model'):
        log("🔬 Active motor model: " + ', '.join(v6_cfg['motor_combination_model']['features']))
    else:
        log("🔬 Existing motor profile active. Run calibration with saved-audio reuse to fit the three-feature model.")

    profile_manager = ProfileManager(SHARE_DIR, AUTO_CALIB_FILE)
    try:
        profile_manager.ensure_active_archived()
        profile_info = profile_manager.status()
        profile_status_text = profile_info.get("active_profile_id") or "Unversioned"
    except Exception as e:
        profile_status_text = f"Profile error: {e}"
        log(f"⚠️ Profile manager initialisation failed: {e}")

    if EXPERIMENT_HARNESS_ENABLED or DIAGNOSTIC_CAPTURE_MODE != "normal":
        try:
            experiment_harness = ExperimentHarness(
                RECORDING_DIR,
                v6_cfg,
                rate=RATE,
                channels=CHANNELS,
                chunk=CHUNK,
                enabled=True,
                auto_capture=AUTO_CAPTURE_INTERESTING_EVENTS,
                session_label=DATA_COLLECTION_LABEL if DATA_COLLECTION_ENABLED else "unlabelled",
                diagnostic_mode=DIAGNOSTIC_CAPTURE_MODE,
                addon_version=VERSION,
                audio_source=str(os.environ.get('PULSE_SOURCE') or AUDIO_SOURCE),
            )
            log(
                "🧪 Experimental harness active: shadow detectors, event "
                "timeline, hardware health, side sessions and trusted labels."
            )
        except Exception as e:
            experiment_harness = None
            log(f"⚠️ Experimental harness disabled after startup error: {e}")

    if mqtt_client.is_connected():
        mqtt_client.publish("vinyl_guardian/active_profile", profile_status_text, retain=True)
        mqtt_client.publish("vinyl_guardian/replay_status", replay_status, retain=True)
        mqtt_client.publish(
            "vinyl_guardian/experiment_status",
            "Active" if experiment_harness is not None else "Disabled",
            retain=True,
        )

    while True:
        if requested_audio_source_option is not None:
            option = requested_audio_source_option
            requested_audio_source_option = None
            try:
                inp.close()
            except Exception:
                pass

            changed = False
            try:
                changed = bool(
                    audio_source_manager
                    and audio_source_manager.select_option(option)
                )
            except Exception as e:
                log(f"⚠️ Could not select audio source {option!r}: {e}")

            if changed:
                apply_guardian_source_volume()
                detector = GuardianDetector(v6_cfg, rate=RATE, channels=CHANNELS)
                feedback_buffer.clear()
                buffer.clear()
                tracking_audio.clear()
                music_gap_started = None
                chunks = loud_chunks = silence_sleep = trigger_chunks = 0
                with state_lock:
                    recognition_session.invalidate()
                    track_monitor.clear()
                    app_state = "IDLE"
                    current_track = None
                    scrobble_fired = False
                    current_attempt = 1
                log(
                    "🎚️ Audio source changed. Detector confidence reset; "
                    "a fresh calibration is recommended if this is new hardware."
                )

            try:
                inp = open_guardian_capture()
            except Exception as e:
                log(f"🚨 ALSA Error after audio-source change: {e}")
                time.sleep(1.0)
                continue
            publish_audio_source_state()

        if audio_scan_requested and not audio_scan_running:
            audio_scan_requested = False
            audio_scan_running = True
            try:
                inp.close()
            except Exception:
                pass

            with state_lock:
                recognition_session.invalidate()
                track_monitor.clear()
                app_state = "IDLE"
                current_track = None
                scrobble_fired = False
                current_attempt = 1
            buffer.clear()
            feedback_buffer.clear()
            tracking_audio.clear()
            music_gap_started = None
            chunks = loud_chunks = silence_sleep = trigger_chunks = 0

            audio_scan_status = "Scanning — keep music playing"
            if mqtt_client.is_connected():
                mqtt_client.publish(
                    "vinyl_guardian/audio/scan_status",
                    audio_scan_status,
                    retain=True,
                )

            scan_applied = False
            try:
                result = audio_source_manager.scan(progress=audio_scan_progress)
                scan_applied = bool(result.get("applied"))
                winner = result.get("winner") or {}
                if scan_applied:
                    complete_audio_scan()
                    audio_scan_status = (
                        f"Selected {winner.get('description') or winner.get('source')} "
                        f"({winner.get('confidence', 'unknown')} confidence)"
                    )
                else:
                    audio_scan_status = "No convincing input found"
            except Exception as e:
                audio_scan_status = f"Scan error: {e}"
                log(f"⚠️ Audio-input scan failed: {e}")
            finally:
                audio_scan_running = False

            apply_guardian_source_volume()
            detector = GuardianDetector(v6_cfg, rate=RATE, channels=CHANNELS)
            try:
                inp = open_guardian_capture()
            except Exception as e:
                log(f"🚨 ALSA Error after audio scan: {e}")
                time.sleep(1.0)
                continue

            refresh_audio_source_select()
            publish_audio_source_state()
            if scan_applied:
                log(
                    "🎚️ New input is active. Detector state was reset; run a "
                    "fresh calibration before judging detection accuracy."
                )

        try:
            length, data = inp.read()
        except Exception as e:
            log(f"⚠️ Audio capture read failed ({e}); reopening source.")
            try:
                inp.close()
            except Exception:
                pass
            time.sleep(0.25)
            try:
                inp = open_guardian_capture()
            except Exception:
                time.sleep(1.0)
            continue

        if length > 0:
            feedback_buffer.append(data)
            if len(feedback_buffer) > feedback_max_chunks:
                feedback_buffer.pop(0)

            feedback_kind = None
            if capture_false_positive_requested:
                feedback_kind = "ghost_trigger"
                capture_false_positive_requested = False
            elif capture_missed_music_requested:
                feedback_kind = "missed_music"
                capture_missed_music_requested = False

            if feedback_kind and feedback_buffer:
                save_feedback_clip(feedback_kind, feedback_buffer)
            
            now = time.time()
            with state_lock:
                current_state = app_state

            previous_power = detector.turntable_on
            # Shazam recognition state must never override the physical-state
            # detector. The staged recognition ladder can run for 30 seconds,
            # while Playing/Motor Idle/Runout remain purely audio-derived.
            frame = detector.update_pcm(data, now)
            tracking_audio.append((now, bytes(data)))

            if stylus_usage is not None:
                # Persistent diagnostic labels are hints only and must never
                # suppress real stylus time if the user forgets to clear them.
                stylus_usage.observe(frame, len(data)//(CHANNELS*2), RATE, known_off=False)

            raw_rms = frame["rms"]
            music_rms = frame["music_rms"]
            hfer = frame["hfer"]
            crest = frame["crest"]
            max_val = frame["peak"]
            is_dust_pop = frame["is_pop_candidate"]
            is_playing = frame["music_active"]
            rhythm_locked = frame["runout_locked"]
            turntable_on = frame["turntable_on"]
            has_played_music = frame["has_played_music"]
            continuous_silence = frame["seconds_since_music"]
            power_score = int(round(frame["motor_confidence"] * 100.0))

            if manual_label_requested is not None:
                if experiment_harness is not None:
                    try:
                        experiment_harness.manual_label(manual_label_requested, now=now)
                        log(f"🏷️ Ground truth marked: {manual_label_requested}")
                    except Exception as e:
                        log(f"⚠️ Ground-truth mark failed: {e}")
                manual_label_requested = None

            if rollback_profile_requested:
                rollback_profile_requested = False
                try:
                    rolled = profile_manager.rollback_previous() if profile_manager else None
                    if rolled:
                        profile_status_text = (
                            f"{rolled.get('profile_id')} · restart required"
                        )
                        log(
                            f"↩️ Rolled calibration back to "
                            f"{rolled.get('profile_id')}. Restart the add-on to load it."
                        )
                        if experiment_harness is not None:
                            experiment_harness.timeline.record(
                                "profile_rollback",
                                profile_id=rolled.get("profile_id"),
                                restart_required=True,
                            )
                    else:
                        profile_status_text = "No earlier profile available"
                        log("↩️ No earlier accepted detector profile is available.")
                except Exception as e:
                    profile_status_text = f"Rollback error: {e}"
                    log(f"⚠️ Profile rollback failed: {e}")
                if mqtt_client.is_connected():
                    mqtt_client.publish(
                        "vinyl_guardian/active_profile",
                        profile_status_text,
                        retain=True,
                    )

            if replay_latest_requested and replay_status != "Running":
                replay_latest_requested = False
                threading.Thread(
                    target=replay_latest_background,
                    daemon=True,
                ).start()

            experiment_snapshot = {}
            if experiment_harness is None and requested_diagnostic_mode not in (None, 'normal'):
                try:
                    experiment_harness = ExperimentHarness(RECORDING_DIR, v6_cfg, RATE, CHANNELS, CHUNK,
                        auto_capture=AUTO_CAPTURE_INTERESTING_EVENTS, addon_version=VERSION,
                        audio_source=str(os.environ.get('PULSE_SOURCE') or AUDIO_SOURCE))
                except Exception as error:
                    log(f"🚨 Diagnostic capture could not start: {error}")
                    requested_diagnostic_mode = None
            if experiment_harness is not None:
                if requested_diagnostic_mode is not None:
                    mode, requested_diagnostic_mode = requested_diagnostic_mode, None
                    if mode != experiment_harness.monitor.mode:
                        experiment_harness.set_diagnostic_mode(mode, now)
                    log('📁 Diagnostic capture mode: ' + MODE_NAMES[mode])
                if audio_source_manager is not None:
                    experiment_harness.monitor.source = audio_source_manager.selected_source
                if requested_diagnostic_action is not None:
                    action, requested_diagnostic_action = requested_diagnostic_action, None
                    # Flush before ending a ground-truth interval so later audio
                    # cannot leak into a known-off regression fixture.
                    if action == 'finish_session':
                        experiment_harness.audio.flush()
                    experiment_harness.monitor.notify(action, now=now)

                try:
                    experiment_snapshot = experiment_harness.observe(
                        data,
                        now,
                        frame,
                        current_state,
                        force_music_active=False,
                    )
                except Exception as e:
                    log(f"⚠️ Experimental harness observation failed: {e}")
                    experiment_snapshot = {}

            if audio_source_manager is not None:
                experiment_snapshot["audio_source"] = audio_source_manager.selected_source
                experiment_snapshot["audio_source_description"] = (
                    audio_source_manager.selected_description
                )
                experiment_snapshot["audio_card"] = audio_source_manager.selected_card
                experiment_snapshot["audio_profile"] = audio_source_manager.selected_profile

            known_off_labels = {"off", "known_off", "turntable_off", "known-off"}
            if (
                DATA_COLLECTION_ENABLED
                and str(DATA_COLLECTION_LABEL).strip().lower() in known_off_labels
                and not previous_power
                and turntable_on
                and feedback_buffer
            ):
                save_feedback_clip("ghost_trigger_auto", feedback_buffer)

            if dataset_collector is not None:
                try:
                    dataset_collector.observe(
                        data,
                        now,
                        frame,
                        current_state,
                        current_track=current_track,
                        experiment_snapshot=experiment_snapshot,
                    )
                except Exception as e:
                    log(f"⚠️ Dataset collector error: {e}")
                    dataset_collector.close()
                    dataset_collector = None

            if debug_countdown > 0:
                debug_metrics_buffer['rms'].append(raw_rms)
                debug_metrics_buffer['hfer'].append(hfer)
                debug_metrics_buffer['crest'].append(crest)
                debug_countdown -= 1
                
                if debug_countdown == 0:
                    avg_r = float(np.median(debug_metrics_buffer['rms']))
                    min_r = float(np.min(debug_metrics_buffer['rms']))
                    max_r = float(np.max(debug_metrics_buffer['rms']))

                    avg_h = float(np.median(debug_metrics_buffer['hfer']))
                    min_h = float(np.min(debug_metrics_buffer['hfer']))
                    max_h = float(np.max(debug_metrics_buffer['hfer']))

                    avg_c = float(np.median(debug_metrics_buffer['crest']))
                    min_c = float(np.min(debug_metrics_buffer['crest']))
                    max_c = float(np.max(debug_metrics_buffer['crest']))
                    
                    rep = [
                        "\n=========================================",
                        "🐞 LIVE MOTOR DIAGNOSTIC REPORT (10s CAPTURE)",
                        "=========================================",
                        "VOLUME (RMS):",
                        f"   ↳ Captured Avg: {avg_r:.6f} (Min: {min_r:.6f}, Max: {max_r:.6f})",
                        f"   ↳ Required Win: {r_min:.6f} to {r_max:.6f}",
                        f"   ↳ Status:       {'✅ PASS' if r_min <= avg_r <= r_max else '❌ TOO QUIET' if avg_r < r_min else '❌ TOO LOUD'}",
                        "-----------------------------------------",
                        "PITCH (HFER):",
                        f"   ↳ Captured Avg: {avg_h:.4f} (Min: {min_h:.4f}, Max: {max_h:.4f})",
                        f"   ↳ Required Win: {h_min:.4f} to {h_max:.4f}",
                        f"   ↳ Status:       {'✅ PASS' if h_min <= avg_h <= h_max else '❌ TOO DEEP' if avg_h < h_min else '❌ TOO SHARP'}",
                        "-----------------------------------------",
                        "TEXTURE (CREST):",
                        f"   ↳ Captured Avg: {avg_c:.2f} (Min: {min_c:.2f}, Max: {max_c:.2f})",
                        f"   ↳ Required Win: {c_min:.2f} to {c_max:.2f}",
                        f"   ↳ Status:       {'✅ PASS' if c_min <= avg_c <= c_max else '❌ TOO FLAT' if avg_c < c_min else '❌ TOO SPIKY'}",
                        "=========================================\n"
                    ]
                    out_text = "\n".join(rep)
                    print(out_text, flush=True)
                    try:
                        with open(os.path.join(SHARE_DIR, "live_debug_dump.txt"), "w") as df:
                            df.write(out_text)
                    except: pass
            
            current_guardian_state = engine_state_map.get(current_state, "Listening")

            # Publish physical power transitions and clean up track state only
            # after the detector has accumulated sustained evidence.
            if turntable_on != previous_power:
                if mqtt_client.is_connected():
                    mqtt_client.publish(
                        "vinyl_guardian/power",
                        "ON" if turntable_on else "OFF",
                        retain=True,
                    )

                if not turntable_on:
                    poweroff_scrobble = None
                    with state_lock:
                        if app_state in ["RECORDING", "PROCESSING", "SLEEPING", "COOLDOWN"]:
                            if app_state == "SLEEPING" and current_track and not scrobble_fired:
                                current_silence_sec = silence_sleep * (CHUNK / RATE)
                                physical_now = now - current_silence_sec
                                time_played = (
                                    physical_now - current_track["session_start_time"]
                                    + current_track.get("previously_played", 0)
                                )
                                if time_played > 5:
                                    track_id = _track_id(current_track)
                                    paused_track_memory = {
                                        "id": track_id,
                                        "accumulated_playtime": time_played,
                                    }
                                if scrobble_is_eligible(current_track, physical_now):
                                    if scrobble_identity_confident(current_track):
                                        poweroff_scrobble = current_track
                                    else:
                                        pending_scrobbles.hold(
                                            current_track,
                                            ended_at=now,
                                            physical_now=physical_now,
                                            reason="power_off",
                                        )
                            recognition_session.invalidate()
                            track_monitor.clear()
                            app_state, current_track, scrobble_fired, current_attempt, consecutive_failures = (
                                "IDLE", None, False, 1, 0
                            )
                            track_display_suppressed = False
                            music_gap_started = None

                    if poweroff_scrobble is not None:
                        _send_scrobble(poweroff_scrobble, mark_current=False)

                    if mqtt_client.is_connected():
                        mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                        mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                        mqtt_client.publish(
                            "vinyl_guardian/progress",
                            "[░░░░░░░░░░] 00:00 / 00:00",
                            retain=True,
                        )
                        mqtt_client.publish("vinyl_guardian/scrobble_status", "Off", retain=True)

            if not turntable_on:
                current_guardian_state = "Off"

            new_vinyl_status = frame["status"]
            # Recognition work should never make the UI flicker away from
            # Playing while the captured track is being identified.
            if turntable_on and current_state in ["RECORDING", "PROCESSING"]:
                new_vinyl_status = "Playing"

            previous_vinyl_status, vinyl_status_changed = change_3_tier_status(
                new_vinyl_status,
                current_guardian_state,
            )
            if vinyl_status_changed and new_vinyl_status == "Runout Groove":
                with state_lock:
                    track_display_suppressed = True
                # Runout is strong physical evidence that the musical segment
                # has completed. This matters especially when catalogue duration
                # is unavailable and the normal timer is deliberately conservative.
                if current_track and not scrobble_fired:
                    physical_end = now - max(0.0, float(continuous_silence or 0.0))
                    if scrobble_is_eligible(
                        current_track,
                        physical_end,
                        completed=True,
                    ):
                        if scrobble_identity_confident(current_track):
                            _send_scrobble(current_track, mark_current=True)
                        else:
                            pending_scrobbles.hold(
                                current_track,
                                ended_at=now,
                                physical_now=physical_end,
                                reason="runout",
                                completed=True,
                            )

                if mqtt_client.is_connected():
                    # The stylus is still down and runout wear continues to count,
                    # but no song is playing once the locked runout begins.
                    track_state, track_attributes = current_track_presentation(
                        new_vinyl_status,
                        app_state,
                        current_track,
                    )
                    mqtt_client.publish("vinyl_guardian/track", track_state, retain=True)
                    mqtt_client.publish(
                        "vinyl_guardian/attributes",
                        json.dumps(track_attributes),
                        retain=True,
                    )
            
            # --- MQTT LOGGING & UI DISPATCH ---
            if now - last_pub >= 1.0:
                if mqtt_client.is_connected():
                    norm_v = normalize_metric(raw_rms, r_min, r_max)
                    norm_h = normalize_metric(hfer, h_min, h_max)
                    norm_c = normalize_metric(crest, c_min, c_max)

                    norm_music_energy = (music_rms / m_thresh) * 100.0 if m_thresh > 0 else 0
                    norm_pop_texture = (crest / runout_crest_thresh) * 100.0 if runout_crest_thresh > 0 else 0
                    norm_pop_volume = (max_val / pop_amp) * 100.0 if pop_amp > 0 else 0

                    mqtt_client.publish("vinyl_guardian/raw_volume", f"{norm_v:.1f}", retain=False)
                    mqtt_client.publish("vinyl_guardian/raw_pitch", f"{norm_h:.1f}", retain=False)
                    mqtt_client.publish("vinyl_guardian/raw_texture", f"{norm_c:.1f}", retain=False)
                    mqtt_client.publish("vinyl_guardian/power_score", str(power_score), retain=False)
                    mqtt_client.publish(
                        "vinyl_guardian/runout_rpm",
                        frame["runout_rpm"] or "None",
                        retain=False,
                    )
                    mqtt_client.publish(
                        "vinyl_guardian/runout_confidence",
                        f"{frame['runout_confidence'] * 100.0:.1f}",
                        retain=False,
                    )
                    mqtt_client.publish(
                        "vinyl_guardian/runout_estimated_rpm",
                        (
                            f"{frame['runout_estimated_rpm']:.3f}"
                            if frame.get("runout_estimated_rpm") is not None
                            else "0.0"
                        ),
                        retain=False,
                    )
                    mqtt_client.publish(
                        "vinyl_guardian/runout_jitter",
                        (
                            f"{frame['runout_phase_jitter_ms']:.1f}"
                            if frame.get("runout_phase_jitter_ms") is not None
                            else "0.0"
                        ),
                        retain=False,
                    )
                    mqtt_client.publish(
                        "vinyl_guardian/runout_support",
                        str(frame.get("runout_support", 0)),
                        retain=False,
                    )

                    if stylus_usage is not None:
                        mqtt_client.publish('vinyl_guardian/stylus_usage', f'{stylus_usage.saved_hours:.6f}', retain=True)
                        mqtt_client.publish('vinyl_guardian/stylus_usage/attributes', json.dumps(stylus_usage.saved_snapshot()), retain=True)
                    diagnostics = experiment_snapshot.get('diagnostics') or {}
                    mqtt_client.publish('vinyl_guardian/diagnostics/mode', MODE_NAMES.get(diagnostics.get('mode'), 'Unavailable'), retain=True)
                    mqtt_client.publish('vinyl_guardian/diagnostics/attributes', json.dumps(diagnostics), retain=True)
                    hardware = experiment_snapshot.get("hardware") or {}
                    side_summary = experiment_snapshot.get("side") or {}
                    shadows = experiment_snapshot.get("shadows") or {}

                    mqtt_client.publish(
                        "vinyl_guardian/hardware_mode",
                        hardware.get("channel_mode", "Unavailable"),
                        retain=False,
                    )
                    mqtt_client.publish(
                        "vinyl_guardian/stereo_correlation",
                        f"{float(hardware.get('left_right_correlation', 0.0) or 0.0):.5f}",
                        retain=False,
                    )
                    mqtt_client.publish(
                        "vinyl_guardian/hardware_health",
                        json.dumps(hardware),
                        retain=False,
                    )

                    mqtt_client.publish(
                        "vinyl_guardian/side_session",
                        side_summary.get("state", "Idle"),
                        retain=False,
                    )
                    mqtt_client.publish(
                        "vinyl_guardian/side_session_attributes",
                        json.dumps(side_summary),
                        retain=False,
                    )

                    prod_signature = (
                        bool(frame.get("turntable_on")),
                        bool(frame.get("music_active")),
                        bool(frame.get("runout_locked")),
                    )
                    disagreeing = []
                    for shadow_name, shadow in shadows.items():
                        shadow_signature = (
                            bool(shadow.get("turntable_on")),
                            bool(shadow.get("music_active")),
                            bool(shadow.get("runout_locked")),
                        )
                        if shadow_signature != prod_signature:
                            disagreeing.append(shadow_name)
                    mqtt_client.publish(
                        "vinyl_guardian/shadow_disagreement",
                        ", ".join(disagreeing) if disagreeing else "None",
                        retain=False,
                    )
                    mqtt_client.publish(
                        "vinyl_guardian/experiment_status",
                        "Active" if experiment_harness is not None else "Disabled",
                        retain=False,
                    )
                    mqtt_client.publish(
                        "vinyl_guardian/experiment_attributes",
                        json.dumps({
                            "trusted_label": experiment_snapshot.get("trusted_label"),
                            "shadows": shadows,
                            "event_log": (
                                experiment_harness.timeline.path
                                if experiment_harness is not None else None
                            ),
                        }),
                        retain=False,
                    )
                    
                    mqtt_client.publish("vinyl_guardian/music_energy", f"{min(250.0, norm_music_energy):.1f}", retain=False)
                    mqtt_client.publish("vinyl_guardian/pop_texture", f"{min(250.0, norm_pop_texture):.1f}", retain=False)
                    mqtt_client.publish("vinyl_guardian/pop_volume", f"{min(250.0, norm_pop_volume):.1f}", retain=False)

                    if not turntable_on:
                        scrob_str = "Off"
                    elif current_state == "SLEEPING" and current_track:
                        if current_track.get("scrobble_delivered"):
                            scrob_str = f"Scrobbled: {current_track.get('title', 'Track')} ✅"
                        elif scrobble_fired:
                            attempts = int(current_track.get("scrobble_retry_attempts", 0))
                            scrob_str = (
                                f"Queued for Last.fm · retry {attempts + 1} ⏳"
                                if attempts
                                else "Queued for Last.fm ⏳"
                            )
                        else:
                            current_silence_sec = silence_sleep * (CHUNK / RATE)
                            physical_now_for_scrobble = now - current_silence_sec
                            time_left = max(0, int(current_track.get('scrobble_trigger_time', 0) - physical_now_for_scrobble))
                            if (
                                current_track.get("duration_known")
                                and float(current_track.get("duration") or 0) <= 30.0
                            ):
                                scrob_str = "Not eligible · track ≤30s"
                            elif time_left > 0:
                                m, sec = divmod(time_left, 60)
                                scrob_str = f"In {m:02d}:{sec:02d} ⏳"
                            elif not scrobble_identity_confident(current_track) or not scrobble_is_eligible(current_track, physical_now_for_scrobble):
                                scrob_str = "Eligible · confirming identity ⚖️"
                            elif not lastfm_enabled():
                                scrob_str = "Last.fm disabled"
                            else:
                                scrob_str = "Queueing scrobble…"
                    else:
                        scrob_str = (
                            f"Scrobbled: {last_scrobbled_track.split(' - ')[0]} ✅"
                            if last_scrobbled_track
                            else "Waiting ⏸️"
                        )
                    mqtt_client.publish("vinyl_guardian/scrobble_status", scrob_str, retain=True)
                    
                    if rhythm_locked:
                        mqtt_client.publish(
                            "vinyl_guardian/progress",
                            "Not Playing · Runout Groove",
                            retain=True,
                        )
                    elif current_state == "SLEEPING" and current_track:
                        pos_sec, dur_sec = max(0, int(now - current_track['start_timestamp'])), int(current_track['duration'])
                        if pos_sec > dur_sec > 0: pos_sec = dur_sec
                        p_m, p_s = divmod(pos_sec, 60); d_m, d_s = divmod(dur_sec, 60)
                        if current_track.get('duration_known', True) and dur_sec > 0:
                            filled = int((pos_sec / dur_sec) * 10)
                            prog_str = f"[{'█' * filled}{'░' * (10 - filled)}] {p_m:02d}:{p_s:02d} / {d_m:02d}:{d_s:02d}"
                        else: prog_str = f"▶️ {p_m:02d}:{p_s:02d} / ??:??"
                        mqtt_client.publish("vinyl_guardian/progress", prog_str)
                    elif current_state in ["RECORDING", "PROCESSING"]:
                        p_m, p_s = divmod(max(0, int(now - song_start)), 60)
                        mqtt_client.publish("vinyl_guardian/progress", f"▶️ {p_m:02d}:{p_s:02d} / ??:??")
                    elif current_state in ["IDLE", "COOLDOWN"]:
                        mqtt_client.publish("vinyl_guardian/progress", "▶️ 00:00 / ??:??" if turntable_on else "[░░░░░░░░░░] 00:00 / 00:00")
                        
                if DEBUG:
                    state_changed = (new_vinyl_status != last_logged_status)
                    rhythm_changed = (rhythm_locked != last_logged_rhythm)
                    
                    if state_changed or rhythm_changed:
                        timestamp = time.strftime('%H:%M:%S')
                        r_icon = "🥁 RHYTHM ACQUIRED" if rhythm_locked else "🛑 RHYTHM LOST"
                        print(f"\n[{timestamp}] 🔄 STATE CHANGE: {last_logged_status} -> {new_vinyl_status}")
                        print(f"   ↳ RMS: {raw_rms:.4f} | Music: {music_rms:.4f} | Crest: {crest:.2f}")
                        if rhythm_changed:
                            rhythm_detail = (
                                f"{frame['runout_rpm']} RPM, "
                                f"{frame['runout_confidence'] * 100.0:.0f}% confidence, "
                                f"{frame['runout_support']} aligned clicks"
                                if rhythm_locked else "unlocked"
                            )
                            print(f"   ↳ {r_icon}: {rhythm_detail}")
                        last_logged_status, last_logged_rhythm = new_vinyl_status, rhythm_locked
                    
                    if current_state == "SLEEPING" and now - last_sleep_log >= 15.0:
                        print(f"[{time.strftime('%H:%M:%S')}] 💤 SLEEP ({max(0, int(wake_up_time - now))}s remaining)")
                        last_sleep_log = now
                    
                last_pub = now

            # --- TIER 3: GUARDIAN RECORDING MACHINE ---
            if current_state == "IDLE":
                if is_playing and turntable_on and not is_dust_pop:
                    trigger_chunks += 1
                    if trigger_chunks >= DYNAMIC_DEBOUNCE_CHUNKS:
                        if mqtt_client.is_connected():
                            mqtt_client.publish("vinyl_guardian/track", "Searching...", retain=True)
                            mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                        song_start, buffer, chunks, loud_chunks, silence_sleep, trigger_chunks = now, bytearray(data), 1, 1, 0, 0
                        with state_lock:
                            track_display_suppressed = False
                            token = recognition_session.begin()
                            app_state = "RECORDING"
                else: trigger_chunks = 0
                    
            elif current_state == "RECORDING":
                if chunks == 0:
                    song_start = now
                buffer.extend(data); chunks += 1
                if music_rms > m_hold_thresh: loud_chunks += 1
                if len(buffer) > MAX_BUFFER_SIZE:
                    buffer.clear(); chunks, loud_chunks = 0, 0
                    if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                    with state_lock: app_state = "IDLE"
                    continue
                elapsed = chunks * CHUNK / RATE
                with state_lock:
                    due_stages = recognition_session.due_stages(token, elapsed)
                for stage_seconds in due_stages:
                    stage_bytes = min(
                        len(buffer),
                        int(stage_seconds * RATE * CHANNELS * 2),
                    )
                    snapshot = bytes(buffer[:stage_bytes])
                    threading.Thread(
                        target=process_audio_background,
                        args=(snapshot, song_start, token, stage_seconds),
                        daemon=True,
                    ).start()

                if chunks >= target:
                    with state_lock:
                        if not recognition_session.valid(token, app_state):
                            continue
                        app_state = "PROCESSING"
                    # The 3/5/10 uploads own immutable snapshots. The live
                    # buffer can now be released while their results finish.
                    buffer, chunks, loud_chunks = bytearray(), 0, 0
                        
            elif current_state == "SLEEPING":
                # Unknown-track fallback still uses its timeout. Identified
                # tracks no longer end merely because metadata duration elapsed:
                # expected end-time starts a boundary search instead.
                if current_track is None:
                    if now >= wake_up_time:
                        cooldown_end = now + 4
                        with state_lock:
                            app_state = "COOLDOWN"
                    continue

                if is_playing:
                    if music_gap_started is not None:
                        gap_seconds = max(0.0, now - music_gap_started)
                        if gap_seconds >= 0.75:
                            end_hint = expected_end(current_track)
                            near_expected = (
                                end_hint is not None
                                and abs(now - end_hint) <= 12.0
                            )
                            with state_lock:
                                started = track_monitor.start_boundary(
                                    now,
                                    "music_recovery",
                                    strength="strong" if near_expected else "medium",
                                    previous_end=music_gap_started,
                                )
                            if started:
                                log(
                                    f"↗️ Music resumed after {gap_seconds:.1f}s; "
                                    "checking whether this is a new track."
                                )
                        music_gap_started = None
                    silence_sleep = 0
                else:
                    silence_sleep += 1
                    if music_gap_started is None:
                        music_gap_started = now

                end_hint = expected_end(current_track)
                if (
                    end_hint is not None
                    and now >= end_hint
                    and not track_monitor.boundary_active()
                ):
                    with state_lock:
                        track_monitor.start_boundary(
                            end_hint,
                            "expected_end",
                            strength="strong",
                            previous_end=end_hint,
                        )

                with state_lock:
                    tracking_requests = (
                        []
                        if rhythm_locked
                        else track_monitor.due_requests(now, current_track)
                    )
                    if any(request["kind"] == "periodic" for request in tracking_requests):
                        current_track["duration_recheck_pending"] = True
                for request in tracking_requests:
                    snapshot = _extract_audio_window(
                        tracking_audio,
                        request["start"],
                        request["end"],
                    )
                    threading.Thread(
                        target=process_tracking_audio_background,
                        args=(snapshot, request["start"], request["id"]),
                        daemon=True,
                    ).start()

                # A long silence well before the metadata end may be an
                # intentional pause inside a song. Give it extra grace rather
                # than immediately treating it as a needle lift.
                silence_seconds = silence_sleep * (CHUNK / RATE)
                # Catalogue timing is only a hint. A rest remains a possible
                # internal pause even when the returned release has ended.
                required_silence_seconds = max(float(needle_lift_sec), 30.0)

                if silence_seconds >= required_silence_seconds and not rhythm_locked:
                    physical_now = now - silence_seconds
                    if current_track and not scrobble_fired:
                        time_played = (
                            physical_now - current_track["session_start_time"]
                            + current_track.get("previously_played", 0)
                        )
                        if time_played > 5:
                            track_id = _track_id(current_track)
                            paused_track_memory = {
                                "id": track_id,
                                "accumulated_playtime": time_played,
                            }
                        pending_scrobbles.hold(
                            current_track,
                            ended_at=now,
                            physical_now=physical_now,
                            reason="silence_or_needle_lift",
                        )
                    if mqtt_client.is_connected():
                        mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                    with state_lock:
                        track_monitor.clear()
                        app_state, current_track, current_attempt, consecutive_failures, has_played_music = (
                            "IDLE", None, 1, 0, False
                        )
                    music_gap_started = None
                    continue

                physical_now = now - silence_seconds
                if (
                    current_track
                    and not scrobble_fired
                    and scrobble_is_eligible(current_track, physical_now)
                ):
                    if scrobble_identity_confident(current_track):
                        if experiment_harness is not None:
                            experiment_harness.monitor.notify(
                                "scrobble_requested",
                                current_track,
                                now,
                            )
                        _send_scrobble(current_track, mark_current=True)
                    else:
                        current_track["scrobble_pending_reason"] = (
                            current_track.get("identity_context_conflict", {}).get("reason")
                            or "Eligible, but contradictory recognition evidence is still being resolved"
                        )

                pending_scrobbles.expire(now)

            elif current_state == "COOLDOWN" and now >= cooldown_end:
                with state_lock: app_state = "IDLE"

def publish_calibration_status(message):
    mqtt_client.publish("vinyl_guardian/calibration/step", message[:250], retain=True)
    mqtt_client.publish("vinyl_guardian/calibration/details", json.dumps({"instruction": message}), retain=True)


if __name__ == "__main__":
    try:
        stylus_usage = StylusUsage()
        log(f"⏱️ Cumulative stylus use: {stylus_usage.hours:.2f} hours, stored at {stylus_usage.path}.")
        if stylus_usage.recovered:
            log("⚠️ Stylus use recovered from its saved backup.")
    except (OSError, ValueError) as error:
        log(f"🚨 Stylus use storage error: {error}")
        sys.exit(1)
    calibration_control.begin(CALIBRATION_MODE)
    start_server()
    initialise_audio_source()
    initialise_scrobble_dispatcher()
    connect_mqtt()
    calibration_control.configure(publish_calibration_status)
    refresh_audio_source_select()
    publish_audio_source_state()

    if startup_scan_pending:
        log("🎚️ Startup audio scan enabled. Keep turntable music playing.")
        run_startup_audio_scan()
        refresh_audio_source_select()
        publish_audio_source_state()

    if CALIBRATION_MODE:
        try:
            run_calibration()
        except Exception as exc:
            calibration_control.append_log(f"Calibration stopped: {exc}")
            calibration_control.set_status(f"Calibration stopped: {exc}", phase="failed")
            log(f"Calibration stopped: {exc}. Correct the input settings and restart to retry.")
            threading.Event().wait()  # Keep the failure and logs visible in the calibration screen.
    else:
        files_to_clean = [
            os.path.join(RECORDING_DIR, "vinyl_debug.wav"),
            os.path.join(RECORDING_DIR, "process.wav"),
        ]
        for f in files_to_clean:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
        listen_and_identify()
