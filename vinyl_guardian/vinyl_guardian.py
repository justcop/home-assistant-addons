import calibration_control
from calibration_web import start_server
from audio_scan_once import StartupScanGate, reset_scan_options
import sys
import os
import glob
import json
import time
import threading
import wave
import subprocess
import signal
import numpy as np
import alsaaudio
import paho.mqtt.client as mqtt
from shazamio import Shazam
import pylast

# Import local modules
from config import *
from audio_math import calculate_audio_levels, calculate_deep_metrics
from integrations import recognize_shazam, get_track_duration, scrobble_to_lastfm, log
from calibration import run_calibration
from detector import GuardianDetector
from telemetry import DatasetCollector
from experiment import ExperimentHarness, TRUSTED_LABELS
from profile_manager import ProfileManager
from replay_lab import replay_latest_dataset
from audio_source import AudioSourceManager, AUTO_OPTION, SYSTEM_DEFAULT_OPTION

VERSION = os.environ.get("ADDON_VERSION", "Unknown")
FORMAT = alsaaudio.PCM_FORMAT_S16_LE

# Global State & Thread Safety
state_lock = threading.Lock()
app_state = "IDLE"
current_attempt = 1
wake_up_time = 0
consecutive_failures = 0
current_track = None
scrobble_fired = False
last_scrobbled_track = None
paused_track_memory = None
inp = None
dataset_collector = None
experiment_harness = None
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

# 3-Tier State Tracking Variables
current_display_status = "Powered Off"
current_engine_status = "Off"

def signal_handler(sig, frame):
    log("🛑 Shutting down gracefully...")
    try:
        global inp, dataset_collector
        if inp is not None: inp.close()
        if dataset_collector is not None:
            dataset_collector.close()
        
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
            
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    except Exception as e:
        log(f"⚠️ Error during shutdown: {e}")
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# --- MQTT SETUP & CALLBACKS ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def on_message(client, userdata, msg):
    global debug_countdown, debug_metrics_buffer
    global capture_false_positive_requested, capture_missed_music_requested
    global manual_label_requested, selected_ground_truth_label
    global replay_latest_requested, rollback_profile_requested
    global audio_scan_requested, requested_audio_source_option

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

def publish_discovery():
    log("Publishing MQTT Auto-Discovery payloads...")
    mqtt_client.subscribe("vinyl_guardian/calibration/continue")
    device_info = {"identifiers": ["vinyl_guardian_01"], "name": "Vinyl Guardian", "manufacturer": "Custom Add-on"}
    mqtt_client.publish("homeassistant/button/vinyl_guardian/calibration_continue/config", json.dumps({"name": "Continue Calibration", "unique_id": "vinyl_guardian_calibration_continue", "command_topic": "vinyl_guardian/calibration/continue", "device": device_info, "icon": "mdi:play"}), retain=True)
    mqtt_client.publish("homeassistant/sensor/vinyl_guardian/calibration_step/config", json.dumps({"name": "Calibration Instructions", "unique_id": "vinyl_guardian_calibration_step", "state_topic": "vinyl_guardian/calibration/step", "json_attributes_topic": "vinyl_guardian/calibration/details", "device": device_info, "icon": "mdi:clipboard-list"}), retain=True)
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
        mqtt_client.publish(f"homeassistant/{c['domain']}/vinyl_guardian/{key}/config", json.dumps(payload), retain=True)
        
    btn_payload = {
        "name": "Live Debug Dump",
        "command_topic": "vinyl_guardian/debug/trigger",
        "unique_id": "vinyl_guardian_debug_btn",
        "device": device_info,
        "icon": "mdi:bug"
    }
    mqtt_client.publish("homeassistant/button/vinyl_guardian/debug/config", json.dumps(btn_payload), retain=True)

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
            json.dumps(payload),
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
        json.dumps(label_select),
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
            json.dumps(audio_select),
            retain=True,
        )

    experiment_buttons = {
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
            json.dumps({
                "name": button["name"],
                "command_topic": button["topic"],
                "unique_id": f"vinyl_guardian_{key}_btn",
                "device": device_info,
                "icon": button["icon"],
            }),
            retain=True,
        )

    mqtt_client.publish(
        "vinyl_guardian/label/current",
        selected_ground_truth_label,
        retain=True,
    )

    if CALIBRATION_MODE:
        mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)
        mqtt_client.publish("vinyl_guardian/status", "Calibrating", retain=True)
        mqtt_client.publish("vinyl_guardian/engine_state", "Calibration Mode", retain=True)
        mqtt_client.publish("vinyl_guardian/track", "Calibration Mode", retain=True)
        mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
        mqtt_client.publish("vinyl_guardian/scrobble_status", "Calibration Mode", retain=True)
        mqtt_client.publish("vinyl_guardian/progress", "Calibration Mode", retain=True)
        mqtt_client.publish("vinyl_guardian/raw_volume", "0.0", retain=True)
        mqtt_client.publish("vinyl_guardian/raw_pitch", "0.0", retain=True)
        mqtt_client.publish("vinyl_guardian/raw_texture", "0.0", retain=True)
        mqtt_client.publish("vinyl_guardian/power_score", "0", retain=True)
        mqtt_client.publish("vinyl_guardian/runout_rpm", "None", retain=True)
        mqtt_client.publish("vinyl_guardian/runout_confidence", "0.0", retain=True)
        mqtt_client.publish("vinyl_guardian/music_energy", "0.0", retain=True)
        mqtt_client.publish("vinyl_guardian/pop_texture", "0.0", retain=True)
        mqtt_client.publish("vinyl_guardian/pop_volume", "0.0", retain=True)
    else:
        mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)
        mqtt_client.publish("vinyl_guardian/status", "Powered Off", retain=True)
        mqtt_client.publish("vinyl_guardian/engine_state", "Off", retain=True)
        mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
        mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
        mqtt_client.publish("vinyl_guardian/scrobble_status", "Off", retain=True)
        mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00", retain=True)
        mqtt_client.publish("vinyl_guardian/raw_volume", "0.0", retain=True)
        mqtt_client.publish("vinyl_guardian/raw_pitch", "0.0", retain=True)
        mqtt_client.publish("vinyl_guardian/raw_texture", "0.0", retain=True)
        mqtt_client.publish("vinyl_guardian/power_score", "0", retain=True)
        mqtt_client.publish("vinyl_guardian/runout_rpm", "None", retain=True)
        mqtt_client.publish("vinyl_guardian/runout_confidence", "0.0", retain=True)
        mqtt_client.publish("vinyl_guardian/music_energy", "0.0", retain=True)
        mqtt_client.publish("vinyl_guardian/pop_texture", "0.0", retain=True)
        mqtt_client.publish("vinyl_guardian/pop_volume", "0.0", retain=True)

def connect_mqtt():
    try:
        mqtt_client.on_message = on_message
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.subscribe("vinyl_guardian/debug/trigger")
        mqtt_client.subscribe("vinyl_guardian/debug/false_positive")
        mqtt_client.subscribe("vinyl_guardian/debug/missed_music")
        mqtt_client.subscribe("vinyl_guardian/label/set")
        mqtt_client.subscribe("vinyl_guardian/label/mark")
        mqtt_client.subscribe("vinyl_guardian/experiment/replay_latest")
        mqtt_client.subscribe("vinyl_guardian/profile/rollback")
        mqtt_client.subscribe("vinyl_guardian/audio/scan")
        mqtt_client.subscribe("vinyl_guardian/audio/source/set")
        mqtt_client.loop_start()
        publish_discovery()
    except Exception as e: log(f"🚨 MQTT Failed: {e}")

def change_3_tier_status(new_vinyl_status, new_engine_status):
    global current_display_status, current_engine_status
    if not CALIBRATION_MODE and mqtt_client.is_connected():
        if new_vinyl_status != current_display_status:
            mqtt_client.publish("vinyl_guardian/status", new_vinyl_status, retain=True)
            current_display_status = new_vinyl_status
        if new_engine_status != current_engine_status:
            mqtt_client.publish("vinyl_guardian/engine_state", new_engine_status, retain=True)
            current_engine_status = new_engine_status

# --- BACKGROUND WORKER (SHAZAM) ---
def process_audio_background(audio_data_bytes, song_start_timestamp):
    global app_state, current_attempt, wake_up_time, consecutive_failures, current_track, scrobble_fired, last_scrobbled_track, paused_track_memory
    global experiment_harness
    local_attempt = None
    with state_lock: local_attempt = current_attempt
    log(f"🔬 Analyzing {RECORD_SECONDS}s capture (Attempt {local_attempt}/{MAX_ATTEMPTS})...")
    
    full_data = np.frombuffer(audio_data_bytes, dtype=np.int16)
    usable = len(full_data) - (len(full_data) % max(1, CHANNELS))
    frame_data = full_data[:usable].reshape(-1, max(1, CHANNELS))
    frame_peak = np.max(np.abs(frame_data), axis=1) if len(frame_data) else np.array([])
    trigger = np.where(frame_peak > AUDIO_ONSET_THRESHOLD)[0]
    start_frame = int(trigger[0]) if len(trigger) > 0 else 0
    min_frames = RATE * MIN_AUDIO_SECONDS
    if len(frame_data) - start_frame < min_frames:
        start_frame = max(0, len(frame_data) - min_frames)

    # Trim only on complete PCM frames so stereo channel order is preserved.
    trimmed_bytes = frame_data[start_frame:].reshape(-1).tobytes()
    trimmed_seconds = start_frame / RATE
    wav_temp = os.path.join(RECORDING_DIR, "process.wav")
    try:
        with wave.open(wav_temp, "wb") as wf:
            wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(trimmed_bytes)
    except Exception as e:
        log(f"⚠️ Failed to write temp wav: {e}")
        with state_lock: app_state = "IDLE"
        return
        
    match = recognize_shazam(wav_temp)
    with state_lock:
        if match:
            current_attempt = 1
            consecutive_failures = 0
            total_duration = match.get('duration', 0)
            if total_duration <= 0: total_duration = get_track_duration(match['title'], match['artist'], match.get('adamid'))
            if total_duration <= 0:
                log("⚠️ Duration unknown. Using track gaps fallback.")
                total_duration = 1200
                duration_known = False
                scrobble_delay = 240
            else:
                duration_known = True
                scrobble_delay = min(total_duration / 2.0, 240)
                
            track_id = f"{match['title']} - {match['artist']}"
            raw_offset = match.get('offset_seconds', 0)
            previously_played = 0
            if paused_track_memory and paused_track_memory["id"] == track_id:
                previously_played = paused_track_memory["accumulated_playtime"]
                scrobble_delay = max(2, scrobble_delay - previously_played)
                log(f"▶️ Resuming track! Recovered {int(previously_played)}s playtime.")
            else:
                if paused_track_memory: log(f"▶️ New track detected. Starting fresh scrobble timer.")
                paused_track_memory = None
                
            start_ts = int(song_start_timestamp + trimmed_seconds - raw_offset)
            if start_ts < 0: start_ts = int(song_start_timestamp)
                
            current_track = {
                "title": match['title'], "artist": match['artist'], "album": match['album'],
                "duration": total_duration, "start_timestamp": start_ts,
                "session_start_time": song_start_timestamp, "scrobble_trigger_time": song_start_timestamp + scrobble_delay,
                "duration_known": duration_known, "previously_played": previously_played,
                "source": "Shazam", "image": match.get('image', '')
            }
            scrobble_fired = False
            if experiment_harness is not None:
                try:
                    experiment_harness.track_identified(current_track, now=time.time())
                except Exception as e:
                    log(f"⚠️ Experiment track logging failed: {e}")
            log(f"🎶 MATCH FOUND: {match['title']} - {match['artist']}")
            mqtt_client.publish("vinyl_guardian/track", f"{match['title']} - {match['artist']}", retain=True)
            try: mqtt_client.publish("vinyl_guardian/attributes", json.dumps(current_track), retain=True)
            except: pass
            wake_up_time = current_track['start_timestamp'] + total_duration
            app_state = "SLEEPING"
        else:
            if current_attempt < MAX_ATTEMPTS:
                log(f"❌ No match. Retrying ({current_attempt + 1}/{MAX_ATTEMPTS})...")
                current_attempt += 1; app_state = "RECORDING"
            else:
                consecutive_failures += 1
                log(f"❌ Max attempts reached. Fallback to gap detection.")
                mqtt_client.publish("vinyl_guardian/track", "Unknown Track", retain=True)
                current_attempt = 1
                wake_up_time = time.time() + (CONSECUTIVE_FAILURE_TIMEOUT if consecutive_failures >= 10 else FALLBACK_SLEEP_SECS)
                if consecutive_failures >= 10: consecutive_failures = 0
                app_state = "SLEEPING"
                    
    try:
        if os.path.exists(wav_temp): os.remove(wav_temp)
    except: pass
    if TEST_CAPTURE_MODE:
        log("🛑 TEST CAPTURE COMPLETE."); os._exit(0)

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
            json.dumps(payload),
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
    global replay_latest_requested, rollback_profile_requested, replay_status, profile_status_text
    global audio_scan_requested, audio_scan_running, audio_source_change_requested
    global requested_audio_source_option, audio_scan_status
    global debug_countdown, debug_metrics_buffer
    global capture_false_positive_requested, capture_missed_music_requested
    
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
    idle_silence_chunks, target = 0, int(RATE / CHUNK * RECORD_SECONDS)
    trigger_chunks = 0  
    buffer = bytearray()
    ghost_buffer, ghost_max_chunks = [], int(RATE / CHUNK * 20.0)
    
    turntable_on, has_played_music, rhythm_locked = False, False, False
    power_max_score = int(RATE / CHUNK * 2.0) 
    power_score = 0
    
    consecutive_music = 0
    last_music_time, last_rhythm_time = -10.0, -10.0
    pop_history = []
    
    VALID_RPM_INTERVALS = [(1.20, 1.46), (1.65, 1.95), (2.45, 2.85), (3.35, 3.85)]
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
    motor_ceil = v6_cfg.get('motor_power_ceiling', globals().get('MOTOR_POWER_CEILING', 999.0))
    needle_lift_sec = v6_cfg.get('needle_lift_sec', globals().get('NEEDLE_LIFT_SECONDS', 15.0))

    # V8: one stateful detector owns feature extraction, power hysteresis,
    # music hysteresis and runout rhythm. Calibration replay uses this same
    # class, so passing calibration now means passing production logic.
    detector = GuardianDetector(v6_cfg, rate=RATE, channels=CHANNELS)

    profile_manager = ProfileManager(SHARE_DIR, AUTO_CALIB_FILE)
    try:
        profile_manager.ensure_active_archived()
        profile_info = profile_manager.status()
        profile_status_text = profile_info.get("active_profile_id") or "Unversioned"
    except Exception as e:
        profile_status_text = f"Profile error: {e}"
        log(f"⚠️ Profile manager initialisation failed: {e}")

    if EXPERIMENT_HARNESS_ENABLED:
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
                ghost_buffer.clear()
                buffer.clear()
                chunks = loud_chunks = silence_sleep = trigger_chunks = 0
                with state_lock:
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
                app_state = "IDLE"
                current_track = None
                scrobble_fired = False
                current_attempt = 1
            buffer.clear()
            ghost_buffer.clear()
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
            if DEBUG_GHOST_CATCHER:
                ghost_buffer.append(data)
                if len(ghost_buffer) > ghost_max_chunks:
                    ghost_buffer.pop(0)

                feedback_kind = None
                if capture_false_positive_requested:
                    feedback_kind = "ghost_trigger"
                    capture_false_positive_requested = False
                elif capture_missed_music_requested:
                    feedback_kind = "missed_music"
                    capture_missed_music_requested = False

                if feedback_kind and ghost_buffer:
                    save_feedback_clip(feedback_kind, ghost_buffer)
            
            now = time.time()
            with state_lock:
                current_state = app_state

            previous_power = detector.turntable_on
            frame = detector.update_pcm(
                data,
                now,
                force_music_active=current_state in ["RECORDING", "PROCESSING"],
            )

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
            active_m_thresh = m_hold_thresh if has_played_music and continuous_silence <= 6.0 else m_thresh

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
            if experiment_harness is not None:
                try:
                    experiment_snapshot = experiment_harness.observe(
                        data,
                        now,
                        frame,
                        current_state,
                        force_music_active=current_state in ["RECORDING", "PROCESSING"],
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
                and ghost_buffer
            ):
                save_feedback_clip("ghost_trigger_auto", ghost_buffer)

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
                    with state_lock:
                        if app_state in ["RECORDING", "PROCESSING", "SLEEPING", "COOLDOWN"]:
                            if app_state == "SLEEPING" and current_track and not scrobble_fired:
                                current_silence_sec = silence_sleep * (CHUNK / RATE)
                                time_played = (
                                    (now - current_track["session_start_time"])
                                    - current_silence_sec
                                    + current_track.get("previously_played", 0)
                                )
                                if time_played > 5:
                                    track_id = f"{current_track['title']} - {current_track['artist']}"
                                    paused_track_memory = {
                                        "id": track_id,
                                        "accumulated_playtime": time_played,
                                    }
                            app_state, current_track, scrobble_fired, current_attempt, consecutive_failures = (
                                "IDLE", None, False, 1, 0
                            )

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

            change_3_tier_status(new_vinyl_status, current_guardian_state)
            
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

                    if not turntable_on: scrob_str = "Off"
                    elif current_state == "SLEEPING" and current_track:
                        if scrobble_fired: scrob_str = f"Scrobbled: {last_scrobbled_track.split(' - ')[0]} ✅" if last_scrobbled_track else "Scrobbled ✅"
                        else:
                            current_silence_sec = silence_sleep * (CHUNK / RATE)
                            time_left = max(0, int(current_track.get('scrobble_trigger_time', 0) - (now - current_silence_sec)))
                            m, s = divmod(time_left, 60); scrob_str = f"In {m:02d}:{s:02d} ⏳" if time_left > 0 else "Scrobbling... 🚀"
                    else: scrob_str = f"Scrobbled: {last_scrobbled_track.split(' - ')[0]} ✅" if last_scrobbled_track else "Waiting ⏸️"
                    mqtt_client.publish("vinyl_guardian/scrobble_status", scrob_str, retain=True)
                    
                    if current_state == "SLEEPING" and current_track:
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
                        with state_lock: app_state = "RECORDING"
                else: trigger_chunks = 0
                    
            elif current_state == "RECORDING":
                buffer.extend(data); chunks += 1
                if music_rms > m_hold_thresh: loud_chunks += 1
                if len(buffer) > MAX_BUFFER_SIZE:
                    buffer.clear(); chunks, loud_chunks = 0, 0
                    if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                    with state_lock: app_state = "IDLE"
                    continue
                if chunks >= target:
                    if loud_chunks >= (target / 2.0):
                        with state_lock: app_state = "PROCESSING"
                        threading.Thread(target=process_audio_background, args=(bytes(buffer), song_start)).start()
                    else:
                        if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                        with state_lock: app_state = "IDLE"
                        buffer, chunks, loud_chunks = bytearray(), 0, 0
                        
            elif current_state == "SLEEPING":
                # Runout clicks are explicitly excluded from music evidence,
                # so they cannot keep a track alive as false "music".
                if is_playing: silence_sleep = 0
                else: silence_sleep += 1
                
                required_silence_chunks = int(RATE / CHUNK * needle_lift_sec)
                if silence_sleep >= required_silence_chunks:
                    if not rhythm_locked:
                        if current_track and not scrobble_fired:
                            time_played = (now - current_track['session_start_time']) - (required_silence_chunks * (CHUNK / RATE)) + current_track.get('previously_played', 0)
                            if time_played > 5:
                                track_id = f"{current_track['title']} - {current_track['artist']}"
                                with state_lock: paused_track_memory = {"id": track_id, "accumulated_playtime": time_played}
                        if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                        with state_lock: app_state, current_track, current_attempt, consecutive_failures, has_played_music = "IDLE", None, 1, 0, False
                        continue
                        
                physical_now = now - (silence_sleep * (CHUNK / RATE))
                if current_track and not scrobble_fired and physical_now >= current_track.get('scrobble_trigger_time', 0):
                    track_id = f"{current_track['title']} - {current_track['artist']}"
                    if track_id != last_scrobbled_track: scrobble_to_lastfm(current_track['artist'], current_track['title'], current_track['start_timestamp'], current_track['album'])
                    if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/scrobble_state", track_id, retain=True)
                    try: mqtt_client.publish("vinyl_guardian/scrobble", json.dumps(current_track), retain=True)
                    except: pass
                    with state_lock: scrobble_fired, last_scrobbled_track, paused_track_memory = True, track_id, None
                        
                if now >= wake_up_time:
                    cooldown_end = now + 4
                    if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                    with state_lock: app_state, current_track = "COOLDOWN", None
                        
            elif current_state == "COOLDOWN" and now >= cooldown_end:
                with state_lock: app_state = "IDLE"

def publish_calibration_status(message):
    mqtt_client.publish("vinyl_guardian/calibration/step", message[:250], retain=True)
    mqtt_client.publish("vinyl_guardian/calibration/details", json.dumps({"instruction": message}), retain=True)


if __name__ == "__main__":
    calibration_control.begin(CALIBRATION_MODE)
    start_server()
    initialise_audio_source()
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