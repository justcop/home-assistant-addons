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

# Debug Dumper State
debug_countdown = 0
debug_metrics_buffer = {'rms': [], 'hfer': [], 'crest': []}

# 3-Tier State Tracking Variables
current_display_status = "Powered Off"
current_engine_status = "Off"

def signal_handler(sig, frame):
    log("🛑 Shutting down gracefully...")
    try:
        global inp
        if inp is not None: inp.close()
        
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
    if msg.topic == "vinyl_guardian/debug/trigger":
        target_chunks = int(RATE / CHUNK * 10.0) 
        log(f"🐞 Live Debug Triggered! Capturing 10 seconds ({target_chunks} chunks) of motor profile...")
        debug_metrics_buffer = {'rms': [], 'hfer': [], 'crest': []}
        debug_countdown = target_chunks

def publish_discovery():
    log("Publishing MQTT Auto-Discovery payloads...")
    device_info = {"identifiers": ["vinyl_guardian_01"], "name": "Vinyl Guardian", "manufacturer": "Custom Add-on"}
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
        # Renamed to clarify the 0-100 Target Zone logic
        "raw_volume": {"name": "Guardian Vol (Target 0-100)", "topic": "raw_volume", "icon": "mdi:volume-high", "domain": "sensor", "state_class": "measurement"},
        "raw_pitch": {"name": "Guardian Pitch (Target 0-100)", "topic": "raw_pitch", "icon": "mdi:sine-wave", "domain": "sensor", "state_class": "measurement"},
        "raw_texture": {"name": "Guardian Texture (Target 0-100)", "topic": "raw_texture", "icon": "mdi:chart-timeline-variant", "domain": "sensor", "state_class": "measurement"},
        "power_score": {"name": "Guardian Power Score", "topic": "power_score", "icon": "mdi:counter", "domain": "sensor", "state_class": "measurement"}
    }
    
    for key, c in configs.items():
        payload = {"name": c["name"], "state_topic": f"vinyl_guardian/{c['topic']}", "unique_id": f"vinyl_guardian_{key}", "device": device_info, "icon": c["icon"]}
        if c.get("attr"): payload["json_attributes_topic"] = "vinyl_guardian/attributes"
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

def connect_mqtt():
    try:
        mqtt_client.on_message = on_message
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.subscribe("vinyl_guardian/debug/trigger")
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
    local_attempt = None
    with state_lock: local_attempt = current_attempt
    log(f"🔬 Analyzing {RECORD_SECONDS}s capture (Attempt {local_attempt}/{MAX_ATTEMPTS})...")
    
    full_data = np.frombuffer(audio_data_bytes, dtype=np.int16)
    abs_data = np.abs(full_data)
    trigger = np.where(abs_data > AUDIO_ONSET_THRESHOLD)[0]
    start_idx = trigger[0] if len(trigger) > 0 else 0
    min_s = RATE * MIN_AUDIO_SECONDS
    if len(full_data) - start_idx < min_s: start_idx = max(0, len(full_data) - min_s)
        
    trimmed_bytes = full_data[start_idx:].tobytes()
    trimmed_seconds = start_idx / RATE
    wav_temp = "/tmp/process.wav"
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

# Helper function to map metrics to a 0-100 percentage range for unified graphing
def normalize_metric(val, t_min, t_max):
    if t_max - t_min == 0: return 0.0
    norm = ((val - t_min) / (t_max - t_min)) * 100.0
    # Clamp visual outliers so they don't break the graph scale
    return max(-50.0, min(150.0, norm)) 

# --- MAIN LOOP ---
def listen_and_identify():
    global app_state, current_attempt, wake_up_time, scrobble_fired, current_track, last_scrobbled_track, paused_track_memory, inp
    global debug_countdown, debug_metrics_buffer
    
    try:
        if DEBUG: log(f"🔊 Applying tuned mic volume: {MIC_VOLUME}%")
        subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{MIC_VOLUME}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except: pass
        
    try:
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NORMAL, device='default', channels=CHANNELS, rate=RATE, format=FORMAT, periodsize=CHUNK)
    except Exception as e:
        log(f"🚨 ALSA Error: {e}"); sys.exit(1)
        
    log("Guardian Engine Online. Shields Armed.")

    last_pub, last_sleep_log, cooldown_end, chunks, loud_chunks, silence_sleep, song_start = time.time(), 0, 0, 0, 0, 0, 0
    idle_silence_chunks, target = 0, int(RATE / CHUNK * RECORD_SECONDS)
    buffer = bytearray()
    ghost_buffer, ghost_max_chunks = [], int(RATE / CHUNK * 6.0)
    
    turntable_on, has_played_music, rhythm_locked = False, False, False
    trigger_chunks, power_score, power_max_score = 0, 0, int(RATE / CHUNK * 3.0) 
    
    last_music_time, last_rhythm_time = 0.0, 0.0
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
    
    pop_amp = v6_cfg.get('pop_amplitude_threshold', globals().get('POP_AMPLITUDE_THRESHOLD', 0.0))
    motor_ceil = v6_cfg.get('motor_power_ceiling', globals().get('MOTOR_POWER_CEILING', 999.0))
    needle_lift_sec = v6_cfg.get('needle_lift_sec', globals().get('NEEDLE_LIFT_SECONDS', 15.0))

    while True:
        length, data = inp.read()
        if length > 0:
            if DEBUG_GHOST_CATCHER:
                ghost_buffer.append(data)
                if len(ghost_buffer) > ghost_max_chunks: ghost_buffer.pop(0)
            
            raw_rms, music_rms, crest_basic = calculate_audio_levels(data)
            metrics = calculate_deep_metrics(data)
            hfer = metrics["hfer"] if metrics else 0.0
            crest = get_crest(np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0)
            now = time.time()
            max_val = np.max(np.abs(np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0))

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
            
            with state_lock: current_state = app_state
            current_guardian_state = engine_state_map.get(current_state, "Listening")
            
            if current_state in ["RECORDING", "PROCESSING", "SLEEPING"]: has_played_music = True
                
            is_dust_pop = False
            if raw_rms > 0:
                if crest >= RUNOUT_CREST_THRESHOLD and max_val >= pop_amp and raw_rms <= motor_ceil:
                    is_dust_pop = True

            if music_rms > MUSIC_THRESHOLD and not is_dust_pop:
                last_music_time = now
                
            if is_dust_pop:
                pop_history.append(now)
                if len(pop_history) > 15: pop_history.pop(0)
                match_count = 0
                for p in pop_history[:-1]:
                    if any(lo <= (now - p) <= hi for lo, hi in VALID_RPM_INTERVALS):
                        match_count += 1; break
                if match_count >= 1 and has_played_music:
                    rhythm_locked = True; last_rhythm_time = now
                        
            if current_state in ["RECORDING", "PROCESSING", "SLEEPING"]:
                pop_history.clear(); rhythm_locked = False
                
            if rhythm_locked and (now - last_rhythm_time > 6.0): rhythm_locked = False
            continuous_silence = now - last_music_time

            # --- TIER 1: TURNTABLE POWER HYSTERESIS ---
            in_rms = (r_min <= raw_rms <= r_max)
            in_hfer = (h_min <= hfer <= h_max)
            in_crest = (c_min <= crest <= c_max)
            motor_on_cond = (in_rms and in_hfer and in_crest)

            if has_played_music or rhythm_locked: motor_on_cond = True
            if IS_SILENT_HW and (has_played_music or rhythm_locked): motor_on_cond = True
                
            if motor_on_cond:
                power_score = min(power_score + 1, power_max_score)
                if power_score >= power_max_score and not turntable_on:
                    turntable_on = True
                    if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/power", "ON", retain=True)
            else:
                power_score = max(power_score - 1, 0)
                if turntable_on and power_score <= 0:
                    turntable_on, has_played_music, rhythm_locked = False, False, False
                    with state_lock:
                        if app_state in ["RECORDING", "PROCESSING", "SLEEPING", "COOLDOWN"]:
                            if app_state == "SLEEPING" and current_track and not scrobble_fired:
                                current_silence_sec = silence_sleep * (CHUNK / RATE)
                                time_played = (now - current_track['session_start_time']) - current_silence_sec + current_track.get('previously_played', 0)
                                if time_played > 5:
                                    track_id = f"{current_track['title']} - {current_track['artist']}"
                                    paused_track_memory = {"id": track_id, "accumulated_playtime": time_played}
                            app_state, current_track, scrobble_fired, current_attempt, consecutive_failures = "IDLE", None, False, 1, 0
                    if mqtt_client.is_connected():
                        mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)
                        mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                        mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                        mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00", retain=True)
                        mqtt_client.publish("vinyl_guardian/scrobble_status", "Off", retain=True)
                    
            if not turntable_on: current_guardian_state = "Off"
                
            # --- TIER 2: VINYL STATUS RESOLUTION ---
            new_vinyl_status = "Motor Idle"
            if not turntable_on: new_vinyl_status = "Powered Off"
            elif current_state in ["RECORDING", "PROCESSING"]: new_vinyl_status = "Playing"
            elif has_played_music and continuous_silence < 2.0: new_vinyl_status = "Playing"
            elif rhythm_locked: new_vinyl_status = "Runout Groove"
            elif has_played_music:
                if continuous_silence < needle_lift_sec: new_vinyl_status = "Between Tracks"
                else: new_vinyl_status = "Motor Idle"; has_played_music = False
            else: new_vinyl_status = "Motor Idle"
                        
            change_3_tier_status(new_vinyl_status, current_guardian_state)
            
            # --- MQTT LOGGING & UI DISPATCH ---
            if now - last_pub >= 1.0:
                if mqtt_client.is_connected():
                    # Map properties to the visual 0-100 "Target Zone" scale
                    norm_v = normalize_metric(raw_rms, r_min, r_max)
                    norm_h = normalize_metric(hfer, h_min, h_max)
                    norm_c = normalize_metric(crest, c_min, c_max)

                    mqtt_client.publish("vinyl_guardian/raw_volume", f"{norm_v:.1f}", retain=False)
                    mqtt_client.publish("vinyl_guardian/raw_pitch", f"{norm_h:.1f}", retain=False)
                    mqtt_client.publish("vinyl_guardian/raw_texture", f"{norm_c:.1f}", retain=False)
                    mqtt_client.publish("vinyl_guardian/power_score", str(power_score), retain=False)

                    if not turntable_on: scrob_str = "Off"
                    elif current_state == "SLEEPING" and current_track:
                        if scrobble_fired: scrob_str = f"Scrobbled: {last_scrobbled_track.split(' - ')[0]} ✅"
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
                        if rhythm_changed: print(f"   ↳ {r_icon}")
                        last_logged_status, last_logged_rhythm = new_vinyl_status, rhythm_locked
                    
                    if current_state == "SLEEPING" and now - last_sleep_log >= 15.0:
                        print(f"[{time.strftime('%H:%M:%S')}] 💤 SLEEP ({max(0, int(wake_up_time - now))}s remaining)")
                        last_sleep_log = now
                    
                last_pub = now

            # --- TIER 3: GUARDIAN RECORDING MACHINE ---
            if current_state == "IDLE":
                if music_rms > MUSIC_THRESHOLD and not is_dust_pop:
                    trigger_chunks += 1
                    if trigger_chunks >= DYNAMIC_DEBOUNCE_CHUNKS:
                        if not turntable_on:
                            turntable_on, power_score = True, power_max_score
                            if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/power", "ON", retain=True)
                        if mqtt_client.is_connected():
                            mqtt_client.publish("vinyl_guardian/track", "Searching...", retain=True)
                            mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                        song_start, buffer, chunks, loud_chunks, silence_sleep, trigger_chunks = now, bytearray(data), 1, 1, 0, 0
                        with state_lock: app_state = "RECORDING"
                else: trigger_chunks = 0
                    
            elif current_state == "RECORDING":
                buffer.extend(data); chunks += 1
                if raw_rms > RUMBLE_THRESHOLD: loud_chunks += 1
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
                if music_rms > MUSIC_THRESHOLD: silence_sleep = 0
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
                else:
                    with state_lock: scrobble_fired = True
                        
                if now >= wake_up_time:
                    cooldown_end = now + 4
                    if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                    with state_lock: app_state, current_track = "COOLDOWN", None
                        
            elif current_state == "COOLDOWN" and now >= cooldown_end:
                with state_lock: app_state = "IDLE"

if __name__ == "__main__":
    connect_mqtt()
    if CALIBRATION_MODE: run_calibration()
    else:
        files_to_clean = [os.path.join(SHARE_DIR, "vinyl_debug.wav"), "/tmp/process.wav"]
        for f in files_to_clean:
            try:
                if os.path.exists(f): os.remove(f)
            except: pass
        listen_and_identify()