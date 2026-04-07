import sys
import os
import json
import time
import threading
import wave
import requests
import urllib.parse
import numpy as np
import alsaaudio
import paho.mqtt.client as mqtt
import asyncio
import subprocess
import tempfile
from shazamio import Shazam
import pylast
import signal

VERSION = os.environ.get("ADDON_VERSION", "Unknown")

# --- LOAD CONFIGURATION ---
try:
    with open('/data/options.json') as f:
        config = json.load(f)
except Exception as e:
    print(f"🚨 Failed to load config: {e}")
    sys.exit(1)

# Path Setup
SHARE_DIR = "/share/vinyl_guardian"
os.makedirs(SHARE_DIR, exist_ok=True)
AUTO_CALIB_FILE = os.path.join(SHARE_DIR, "auto_calibration.json")

# System Modes
CALIBRATION_MODE = config.get("calibration_mode", False)
TEST_CAPTURE_MODE = config.get("test_capture_mode", False)
DEBUG = config.get("debug_logging", False)

# 👻 TEMPORARY DEBUG TOGGLE: Capture False Positives
DEBUG_GHOST_CATCHER = True

# MQTT & API Keys
MQTT_BROKER = config.get("mqtt_broker", "core-mosquitto")
MQTT_PORT = config.get("mqtt_port", 1883)
MQTT_USER = config.get("mqtt_user", "")
MQTT_PASS = config.get("mqtt_password", "")

LFM_USER = config.get("lastfm_username", "")
LFM_PASS = config.get("lastfm_password", "")
LFM_KEY = config.get("lastfm_api_key", "")
LFM_SECRET = config.get("lastfm_api_secret", "")

# Load advanced dictionary
adv = config.get("advanced", {})

# --- HIERARCHICAL SETTINGS RESOLUTION ---
MUSIC_THRESHOLD = 0.005
RUMBLE_THRESHOLD = 0.015
MOTOR_POWER_THRESHOLD = 0.0045
MOTOR_HFER_THRESHOLD = 0.0
MIC_VOLUME = 8
RECORD_SECONDS = config.get("recording_seconds", 10)

# Dynamic Calibration State Variables
RUNOUT_CREST_THRESHOLD = 4.5
MOTOR_HYSTERESIS_SEC = 1.5 
NEEDLE_HYSTERESIS_SEC = 2.5 
DYNAMIC_DEBOUNCE_CHUNKS = adv.get("trigger_debounce_chunks", 3)
IS_SILENT_HW = False

if os.path.exists(AUTO_CALIB_FILE):
    try:
        with open(AUTO_CALIB_FILE, 'r') as f:
            auto_cal = json.load(f)
        MUSIC_THRESHOLD = auto_cal.get("music_threshold", MUSIC_THRESHOLD)
        RUMBLE_THRESHOLD = auto_cal.get("rumble_threshold", RUMBLE_THRESHOLD)
        MOTOR_POWER_THRESHOLD = auto_cal.get("motor_power_threshold", MOTOR_POWER_THRESHOLD)
        MIC_VOLUME = auto_cal.get("mic_volume", MIC_VOLUME)
        RUNOUT_CREST_THRESHOLD = auto_cal.get("runout_crest_threshold", RUNOUT_CREST_THRESHOLD)
        MOTOR_HYSTERESIS_SEC = auto_cal.get("motor_hysteresis_sec", MOTOR_HYSTERESIS_SEC)
        NEEDLE_HYSTERESIS_SEC = auto_cal.get("needle_hysteresis_sec", NEEDLE_HYSTERESIS_SEC)
        DYNAMIC_DEBOUNCE_CHUNKS = auto_cal.get("music_debounce_chunks", DYNAMIC_DEBOUNCE_CHUNKS)
        MOTOR_HFER_THRESHOLD = auto_cal.get("motor_hfer_threshold", MOTOR_HFER_THRESHOLD)
        IS_SILENT_HW = auto_cal.get("is_silent_hw", False)
       
        if not CALIBRATION_MODE:
            print("💡 Loaded dynamically tuned thresholds and state buffers from auto_calibration.json")
    except Exception as e:
        print(f"⚠️ Failed to read auto_calibration.json: {e}")

UI_MUSIC = adv.get("manual_override_music_threshold")
if UI_MUSIC is not None and UI_MUSIC > 0: MUSIC_THRESHOLD = UI_MUSIC

UI_RUMBLE = adv.get("manual_override_rumble_threshold")
if UI_RUMBLE is not None and UI_RUMBLE > 0: RUMBLE_THRESHOLD = UI_RUMBLE

UI_MOTOR = adv.get("manual_override_motor_threshold")
if UI_MOTOR is not None and UI_MOTOR > 0: MOTOR_POWER_THRESHOLD = UI_MOTOR

UI_MIC = adv.get("manual_override_mic_volume")
if UI_MIC is not None and UI_MIC > 0: MIC_VOLUME = UI_MIC

# --- ENGINE TUNING PARAMETERS ---
MAX_ATTEMPTS = adv.get("max_attempts", 3)
MIN_AUDIO_SECONDS = adv.get("min_audio_seconds", 5)
AUDIO_ONSET_THRESHOLD = adv.get("audio_onset_threshold", 1000)      
NEEDLE_LIFT_SECONDS = adv.get("needle_lift_seconds", 15)
CONSECUTIVE_FAILURE_TIMEOUT = adv.get("consecutive_failure_timeout", 1800)
FALLBACK_SLEEP_SECS = adv.get("fallback_sleep_secs", 60)          

# Audio Settings
CHANNELS = config.get("channels", 2)
RATE = 44100
FORMAT = alsaaudio.PCM_FORMAT_S16_LE
CHUNK = 2048
MAX_BUFFER_SIZE = RATE * CHANNELS * 2 * 60

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

# New 3-Tier State Tracking Variables
current_display_status = "Powered Off"
current_engine_status = "Listening"

shazam_instance = Shazam()

def log(message):
    print(f"[Vinyl Guardian] {message}", flush=True)

def signal_handler(sig, frame):
    log("🛑 Shutting down gracefully...")
    try:
        global inp
        if inp is not None: inp.close()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    except Exception as e:
        log(f"⚠️ Error during shutdown: {e}")
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

def save_atomic_json(filepath, data):
    temp_fd, temp_path = tempfile.mkstemp(dir=SHARE_DIR)
    try:
        with os.fdopen(temp_fd, 'w') as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, filepath)
    except Exception as e:
        log(f"⚠️ Failed to save atomic JSON: {e}")
        try: os.unlink(temp_path)
        except: pass

# --- LAST.FM SETUP ---
lastfm_network = None
if not CALIBRATION_MODE and LFM_USER and LFM_PASS and LFM_KEY and LFM_SECRET:
    try:
        lastfm_network = pylast.LastFMNetwork(api_key=LFM_KEY, api_secret=LFM_SECRET, username=LFM_USER, password_hash=pylast.md5(LFM_PASS))
        log("✅ Last.fm integration initialized.")
    except Exception as e: log(f"🚨 Last.fm initialization failed: {e}")

def scrobble_to_lastfm(artist, title, start_timestamp, album=None):
    if not lastfm_network: return
    try:
        kwargs = {"artist": artist, "title": title, "timestamp": start_timestamp}
        if album and album != "Unknown": kwargs["album"] = album
        lastfm_network.scrobble(**kwargs)
        log(f"🎵 Successfully scrobbled to Last.fm: {title} by {artist}")
    except Exception as e: log(f"🚨 Last.fm Scrobble Failed: {e}")

# --- MQTT SETUP ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER and MQTT_PASS: mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def publish_discovery():
    log("Publishing MQTT Auto-Discovery payloads...")
    device_info = {"identifiers": ["vinyl_guardian_01"], "name": "Vinyl Guardian", "manufacturer": "Custom Add-on"}
    configs = {
        "status": {"name": "Vinyl Status", "topic": "status", "icon": "mdi:record-player", "domain": "sensor"},
        "engine": {"name": "Guardian Engine State", "topic": "engine_state", "icon": "mdi:cpu-64-bit", "domain": "sensor"},
        "track": {"name": "Vinyl Current Track", "topic": "track", "icon": "mdi:music-circle", "attr": True, "domain": "sensor"},
        "progress": {"name": "Vinyl Track Progress", "topic": "progress", "icon": "mdi:clock-outline", "domain": "sensor"},
        "music_rms": {"name": "Vinyl Music RMS", "topic": "music_rms", "icon": "mdi:waveform", "domain": "sensor", "state_class": "measurement", "unit": "RMS"},
        "rumble_rms": {"name": "Vinyl Rumble RMS", "topic": "rumble_rms", "icon": "mdi:vibrate", "domain": "sensor", "state_class": "measurement", "unit": "RMS"},
        "scrobble": {"name": "Vinyl Last Scrobble", "topic": "scrobble_state", "icon": "mdi:lastpass", "attr_topic": "scrobble", "domain": "sensor"},
        "power": {"name": "Turntable Power", "topic": "power", "icon": "mdi:power", "domain": "binary_sensor"}
    }
    for key, c in configs.items():
        payload = {
            "name": c["name"], "state_topic": f"vinyl_guardian/{c['topic']}", "unique_id": f"vinyl_guardian_{key}",
            "device": device_info, "icon": c["icon"]
        }
        if c.get("attr"): payload["json_attributes_topic"] = "vinyl_guardian/attributes"
        if c.get("attr_topic"): payload["json_attributes_topic"] = f"vinyl_guardian/{c['attr_topic']}"
        if c.get("state_class"): payload["state_class"] = c["state_class"]
        if c.get("unit"): payload["unit_of_measurement"] = c["unit"]
        if c["domain"] == "binary_sensor":
            payload["payload_on"] = "ON"
            payload["payload_off"] = "OFF"
        mqtt_client.publish(f"homeassistant/{c['domain']}/vinyl_guardian/{key}/config", json.dumps(payload), retain=True)

    mqtt_client.publish("vinyl_guardian/status", "Powered Off", retain=True)
    mqtt_client.publish("vinyl_guardian/engine_state", "Listening", retain=True)
    mqtt_client.publish("vinyl_guardian/track", "None", retain=True)
    mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00", retain=True)
    mqtt_client.publish("vinyl_guardian/scrobble_state", "None", retain=True)
    mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)

def connect_mqtt():
    if CALIBRATION_MODE: return
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        publish_discovery()
    except Exception as e: log(f"🚨 MQTT Failed: {e}")

def change_3_tier_status(new_vinyl_status, new_engine_status):
    global current_display_status, current_engine_status
    if not CALIBRATION_MODE:
        if mqtt_client.is_connected():
            if new_vinyl_status != current_display_status:
                mqtt_client.publish("vinyl_guardian/status", new_vinyl_status, retain=True)
                current_display_status = new_vinyl_status
            if new_engine_status != current_engine_status:
                mqtt_client.publish("vinyl_guardian/engine_state", new_engine_status, retain=True)
                current_engine_status = new_engine_status

# --- HELPER: GET TRACK DURATION ---
def get_track_duration(title, artist, adamid=None):
    for attempt in range(2):
        try:
            if adamid: url = f"https://itunes.apple.com/lookup?id={adamid}"
            else:
                query = urllib.parse.quote(f"{title} {artist}")
                url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=1"
            res = requests.get(url, timeout=10)
            data = res.json()
            if data.get('resultCount', 0) > 0: return data['results'][0].get('trackTimeMillis', 0) / 1000.0
        except Exception: time.sleep(1)
    return 0

# --- RECOGNITION ENGINE (SHAZAM) ---
def recognize_shazam(wav_path):
    if DEBUG: log("Uploading to Shazam...")
    try:
        async def _recognize():
            return await shazam_instance.recognize(wav_path)
        res_json = asyncio.run(_recognize())
       
        if isinstance(res_json, dict) and 'track' in res_json and isinstance(res_json.get('matches'), list) and len(res_json['matches']) > 0:
            track = res_json['track']
            if not isinstance(track, dict): return None
            title, artist = track.get('title', 'Unknown'), track.get('subtitle', 'Unknown')
            album, duration, release_year = "Unknown", 0, "Unknown"
            adamid = track.get('trackadamid')
           
            for section in track.get('sections', []):
                if isinstance(section, dict) and section.get('type') == 'SONG':
                    for meta in section.get('metadata', []):
                        if isinstance(meta, dict):
                            if meta.get('title') == 'Album': album = meta.get('text')
                            elif meta.get('title') == 'Length':
                                p = meta.get('text', '').split(':')
                                if len(p) == 2: duration = int(p[0])*60 + int(p[1])
                                elif len(p) == 3: duration = int(p[0])*3600 + int(p[1])*60 + int(p[2])
                            elif meta.get('title') == 'Released': release_year = meta.get('text')
            return {"title": title, "artist": artist, "album": album, "release_year": release_year, "offset_seconds": res_json['matches'][0].get('offset', 0) if isinstance(res_json['matches'][0], dict) else 0, "duration": duration, "adamid": adamid}
        return None
    except Exception as e:
        log(f"🚨 Shazam Error: {e}")
        return None

# --- BACKGROUND WORKER ---
def process_audio_background(audio_data_bytes, song_start_timestamp):
    global app_state, current_attempt, wake_up_time, consecutive_failures, current_track, scrobble_fired, last_scrobbled_track, paused_track_memory
   
    local_attempt = None
    with state_lock: 
        local_attempt = current_attempt
        
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
            late_start_offset = min(raw_offset, 30)
            if raw_offset > 2: scrobble_delay = max(2, scrobble_delay - late_start_offset)

            previously_played = 0
            if paused_track_memory and paused_track_memory["id"] == track_id:
                previously_played = paused_track_memory["accumulated_playtime"]
                scrobble_delay = max(2, scrobble_delay - previously_played)
                log(f"▶️ Resuming track! Recovered {int(previously_played)}s playtime.")
           
            paused_track_memory = None
           
            start_ts = int(song_start_timestamp + trimmed_seconds - raw_offset)
            if start_ts < 0: start_ts = int(song_start_timestamp)
           
            current_track = {
                "title": match['title'], "artist": match['artist'], "album": match['album'],
                "duration": total_duration, "start_timestamp": start_ts,
                "session_start_time": song_start_timestamp, "scrobble_trigger_time": song_start_timestamp + scrobble_delay,
                "duration_known": duration_known, "previously_played": previously_played + late_start_offset,
                "source": "Shazam"
            }
            scrobble_fired = False

            log(f"🎶 MATCH FOUND: {match['title']} - {match['artist']}")
            mqtt_client.publish("vinyl_guardian/track", f"{match['title']} - {match['artist']}", retain=True)
            try: 
                payload = json.dumps(current_track)
                mqtt_client.publish("vinyl_guardian/attributes", payload, retain=True)
            except TypeError as e:
                log(f"⚠️ MQTT attribute serialization failed: {e}")
            except Exception as e:
                pass
           
            wake_up_time = current_track['start_timestamp'] + total_duration
            app_state = "SLEEPING"
        else:
            if current_attempt < MAX_ATTEMPTS:
                log(f"❌ No match. Retrying ({current_attempt + 1}/{MAX_ATTEMPTS})...")
                current_attempt += 1
                app_state = "RECORDING"
            else:
                consecutive_failures += 1
                log(f"❌ Max attempts reached. Fallback to gap detection.")
                mqtt_client.publish("vinyl_guardian/track", "Unknown Track", retain=True)
                current_attempt = 1
                wake_up_time = time.time() + (CONSECUTIVE_FAILURE_TIMEOUT if consecutive_failures >= 10 else FALLBACK_SLEEP_SECS)
                if consecutive_failures >= 10:
                    consecutive_failures = 0
                app_state = "SLEEPING"

    try:
        if os.path.exists(wav_temp): os.remove(wav_temp)
    except: pass
    if TEST_CAPTURE_MODE: log("🛑 TEST CAPTURE COMPLETE."); os._exit(0)

# --- AUDIO MATH ---
def calculate_audio_levels(data):
    try:
        audio_data = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        if len(audio_data) <= 1: return 0.0, 0.0, 1.0
        raw_rms = float(np.sqrt(np.mean(np.square(audio_data)))) / 32768.0
        filtered_data = audio_data[1:] - 0.95 * audio_data[:-1]
        music_rms = float(np.sqrt(np.mean(np.square(filtered_data)))) / 32768.0
        peak = np.max(np.abs(audio_data)) / 32768.0
        crest = peak / raw_rms if raw_rms > 0 else 1.0
        return raw_rms, music_rms, crest
    except Exception: return 0.0, 0.0, 1.0

def calculate_deep_metrics(data):
    audio_data = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    if len(audio_data) <= 1: return None
   
    rms = float(np.sqrt(np.mean(np.square(audio_data)))) / 32768.0
    
    filtered_data = audio_data[1:] - 0.95 * audio_data[:-1]
    music_rms = float(np.sqrt(np.mean(np.square(filtered_data)))) / 32768.0
    
    peak = np.max(np.abs(audio_data)) / 32768.0
    crest = peak / rms if rms > 0 else 1.0
    
    # HFER Z-Div Protection
    if rms < 0.0001:
        hfer = 0.0
    else:
        hf_data = audio_data[1:] - audio_data[:-1] 
        hf_rms = float(np.sqrt(np.mean(np.square(hf_data)))) / 32768.0
        hfer = hf_rms / rms
   
    return {"rms": rms, "music_rms": music_rms, "crest": crest, "hfer": float(hfer)}

# --- MAIN LOOP ---
def listen_and_identify():
    global app_state, current_attempt, wake_up_time, scrobble_fired, current_track, last_scrobbled_track, paused_track_memory, inp
   
    try:
        log(f"🔊 Applying tuned mic volume: {MIC_VOLUME}%")
        subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{MIC_VOLUME}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError: pass
       
    try:
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NORMAL, device='default', channels=CHANNELS, rate=RATE, format=FORMAT, periodsize=CHUNK)
    except Exception as e: log(f"🚨 ALSA Error: {e}"); sys.exit(1)

    log("Listening for needle drop...")
    if DEBUG:
        log(f"[DEBUG] Settings: Mus: {MUSIC_THRESHOLD:.4f} | Rum: {RUMBLE_THRESHOLD:.4f} | Mot: {MOTOR_POWER_THRESHOLD:.4f} | HFER: {MOTOR_HFER_THRESHOLD:.4f}")
        log(f"[DEBUG] Buffers: Mot: {MOTOR_HYSTERESIS_SEC}s | Nee: {NEEDLE_HYSTERESIS_SEC}s | Crest: {RUNOUT_CREST_THRESHOLD} | Deb: {DYNAMIC_DEBOUNCE_CHUNKS}")
        log(f"[DEBUG] Hardware Profile: {'Silent (Rhythm Tracker)' if IS_SILENT_HW else 'Standard (Rumble)'}")
   
    last_pub, last_sleep_log, cooldown_end, chunks, loud_chunks, silence_sleep, song_start = time.time(), 0, 0, 0, 0, 0, 0
    idle_silence_chunks = 0
    target = int(RATE / CHUNK * RECORD_SECONDS)
    buffer = bytearray()
    
    # 👻 Ghost Catcher Setup
    ghost_buffer = []
    ghost_max_chunks = int(RATE / CHUNK * 6.0) # 6 second rolling buffer
   
    turntable_on = False
    has_played_music = False
    trigger_chunks = 0  
   
    power_score = 0
    power_max_score = int(RATE / CHUNK * MOTOR_HYSTERESIS_SEC)
    
    power_off_max_score = int(RATE / CHUNK * 1.5)
   
    motor_on_thresh = MOTOR_POWER_THRESHOLD
    motor_off_thresh = MOTOR_POWER_THRESHOLD
   
    needle_active_score = 0
    needle_max_score = int(RATE / CHUNK * NEEDLE_HYSTERESIS_SEC)
    
    avg_pop_interval = (1.8 + 1.33) / 2.0
    pop_score_boost = int(RATE / CHUNK * (avg_pop_interval * 0.6))
    pop_score_boost = max(int(RATE / CHUNK * 1.0), min(int(RATE / CHUNK * 3.0), pop_score_boost))
   
    needle_down = False
    last_music_time = 0
    
    # --- ADVANCED RHYTHM TRACKER VARIABLES ---
    pop_history = []
    rhythm_locked = False
    last_rhythm_time = 0
    
    VALID_RPM_INTERVALS = [
        (0.90, 1.50),   # 45 RPM
        (1.65, 1.95),   # 33⅓ RPM
        (2.50, 2.90),   # 45 RPM 2x harmonic
        (3.40, 3.80)    # 33⅓ RPM 2x harmonic
    ]
    
    # Guardian Engine States
    engine_state_map = {
        "IDLE": "Listening",
        "RECORDING": "Recording",
        "PROCESSING": "Processing",
        "SLEEPING": "Tracking",
        "COOLDOWN": "Cooldown"
    }

    while True:
        length, data = inp.read()
        if length > 0:
            if DEBUG_GHOST_CATCHER:
                ghost_buffer.append(data)
                if len(ghost_buffer) > ghost_max_chunks:
                    ghost_buffer.pop(0)

            raw_rms, music_rms, crest = calculate_audio_levels(data)
            
            metrics = calculate_deep_metrics(data)
            hfer = metrics["hfer"] if metrics else 0.0
            
            now = time.time()
           
            with state_lock:
                current_state = app_state
                
            current_guardian_state = engine_state_map.get(current_state, "Listening")

            if current_state in ["RECORDING", "PROCESSING", "SLEEPING"]:
                has_played_music = True
                last_music_time = now

            if music_rms > MUSIC_THRESHOLD:
                last_music_time = now

            # --- TIER 1: TURNTABLE POWER ---
            upper_limit = max(motor_on_thresh * 4.5, 0.008)
            motor_on_cond = raw_rms > motor_on_thresh
            if MOTOR_HFER_THRESHOLD > 0.0 and raw_rms < upper_limit:
                if hfer > MOTOR_HFER_THRESHOLD:
                    motor_on_cond = False 
                    
            if IS_SILENT_HW and not turntable_on and not needle_down and not has_played_music:
                if raw_rms > upper_limit:
                    motor_on_cond = False
            
            if motor_on_cond:
                power_score = min(power_score + 1, power_max_score)
                if power_score >= power_max_score:
                    if not turntable_on:
                        turntable_on = True
                        mqtt_client.publish("vinyl_guardian/power", "ON", retain=True)
                        
                        # 👻 DUMP THE GHOST CATCHER TO FILE!
                        if DEBUG_GHOST_CATCHER:
                            ts = int(time.time())
                            wav_name = os.path.join(SHARE_DIR, f"ghost_trigger_{ts}.wav")
                            log(f"👻 DEBUG GHOST CATCHER: False positive overnight trigger caught!")
                            log(f"   ┣ Saved 6-second memory to: {wav_name}")
                            log(f"   ┣ Triggered at RMS: {raw_rms:.6f} (Threshold: {motor_on_thresh:.6f}, Block Limit: {upper_limit:.6f})")
                            log(f"   ┗ Triggered at HFER: {hfer:.4f} (Threshold: {MOTOR_HFER_THRESHOLD:.4f})")
                            try:
                                with wave.open(wav_name, "wb") as wf:
                                    wf.setnchannels(CHANNELS)
                                    wf.setsampwidth(2)
                                    wf.setframerate(RATE)
                                    wf.writeframes(b"".join(ghost_buffer))
                            except Exception as e:
                                log(f"⚠️ Failed to save Ghost Catcher wav: {e}")
            else:
                power_score = max(power_score - 1, 0)
                if turntable_on and power_score <= (power_max_score - power_off_max_score):
                    power_score = 0
                    turntable_on = False
                    has_played_music = False
                    rhythm_locked = False
                    mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)
                    mqtt_client.publish("vinyl_guardian/track", "None", retain=True)
                    mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00", retain=True)

            # --- TIER 2: VINYL STATUS (Needle / Rhythm Logic) ---
            is_dust_pop = crest >= RUNOUT_CREST_THRESHOLD
           
            if is_dust_pop:
                needle_active_score = min(needle_active_score + pop_score_boost, needle_max_score)
                
                pop_history.append(now)
                if len(pop_history) > 10:
                    pop_history.pop(0)
                    
                match_count = 0
                for p in pop_history[:-1]:
                    delta = now - p
                    for lo, hi in VALID_RPM_INTERVALS:
                        if lo <= delta <= hi:
                            match_count += 1
                            break
                            
                if match_count >= 2:
                    rhythm_locked = True
                    last_rhythm_time = now
                        
            elif raw_rms >= RUMBLE_THRESHOLD:
                needle_active_score = min(needle_active_score + 1, needle_max_score)
            else:
                needle_active_score = max(needle_active_score - 1, 0)
                
            if rhythm_locked and (now - last_rhythm_time > 4.0):
                rhythm_locked = False
                has_played_music = False 
           
            needle_down = needle_active_score > (needle_max_score * 0.5)

            new_vinyl_status = "Motor Idle"
            if not turntable_on:
                new_vinyl_status = "Powered Off"
                has_played_music = False
                rhythm_locked = False
            elif rhythm_locked:
                new_vinyl_status = "Runout Groove"
            elif current_state in ["RECORDING", "PROCESSING", "SLEEPING"]:
                new_vinyl_status = "Playing"
            elif has_played_music:
                if (now - last_music_time) < 4.0:
                    new_vinyl_status = "Runout Groove"
                else:
                    has_played_music = False
                    new_vinyl_status = "Motor Idle"
            else:
                new_vinyl_status = "Motor Idle"
                
            change_3_tier_status(new_vinyl_status, current_guardian_state)

            if now - last_pub >= 1.0:
                if mqtt_client.is_connected():
                    mqtt_client.publish("vinyl_guardian/music_rms", f"{music_rms:.4f}")
                    mqtt_client.publish("vinyl_guardian/rumble_rms", f"{raw_rms:.4f}")
                   
                    if current_state == "SLEEPING" and current_track:
                        pos_sec = max(0, int(now - current_track['start_timestamp']))
                        dur_sec = int(current_track['duration'])
                        if pos_sec > dur_sec > 0: pos_sec = dur_sec
                        p_m, p_s = divmod(pos_sec, 60)
                        d_m, d_s = divmod(dur_sec, 60)
                       
                        if current_track.get('duration_known', True) and dur_sec > 0:
                            percent = pos_sec / dur_sec
                            filled = int(percent * 10)
                            bar = '█' * filled + '░' * (10 - filled)
                            prog_str = f"[{bar}] {p_m:02d}:{p_s:02d} / {d_m:02d}:{d_s:02d}"
                        else:
                            prog_str = f"▶️ {p_m:02d}:{p_s:02d} / ??:??"
                           
                        mqtt_client.publish("vinyl_guardian/progress", prog_str)

                    elif current_state in ["RECORDING", "PROCESSING"]:
                        pos_sec = max(0, int(now - song_start))
                        p_m, p_s = divmod(pos_sec, 60)
                        mqtt_client.publish("vinyl_guardian/progress", f"▶️ {p_m:02d}:{p_s:02d} / ??:??")

                    elif current_state in ["IDLE", "COOLDOWN"]:
                        if turntable_on:
                            mqtt_client.publish("vinyl_guardian/progress", "▶️ 00:00 / ??:??")
                        else:
                            mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00")
               
                if DEBUG:
                    if current_state == "RECORDING":
                        pct = int((chunks/target)*100) if target > 0 else 0
                        status = f"🔴 REC {pct}%"
                    elif current_state == "SLEEPING": status = f"💤 SLEEP ({max(0, int(wake_up_time - now))}s)" if now - last_sleep_log >= 15.0 else None
                    elif current_state == "COOLDOWN": status = f"⏳ COOLDOWN ({max(0, int(cooldown_end - now))}s)"
                    elif current_state == "PROCESSING": status = "⚙️ PROC"
                    else:
                        status = f"🟢 {new_vinyl_status.upper()}"
                       
                    if status:
                        rhythm_flag = " | 🥁 RHYTHM LOCK" if rhythm_locked else ""
                        print(f"[{time.strftime('%H:%M:%S')}] {status} | RMS: {raw_rms:.4f} | Music: {music_rms:.4f} | Crest: {crest:.2f}{rhythm_flag}", flush=True)
                        if "SLEEP" in status: last_sleep_log = now
                last_pub = now

            # --- TIER 3: GUARDIAN ENGINE STATE MACHINE ---
            if current_state == "IDLE":
                if music_rms > MUSIC_THRESHOLD and not is_dust_pop:
                    trigger_chunks += 1
                    if trigger_chunks >= DYNAMIC_DEBOUNCE_CHUNKS:
                        log(f"🎵 AUDIO DETECTED (Music Spike: {music_rms:.4f})")
                        if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/track", "Unknown", retain=True)
                        song_start, buffer, chunks, loud_chunks, silence_sleep = now, bytearray(data), 1, 1, 0
                        trigger_chunks = 0
                        with state_lock: app_state = "RECORDING"
                else:
                    trigger_chunks = 0  

            elif current_state == "RECORDING":
                buffer.extend(data)
                chunks += 1
                if raw_rms > RUMBLE_THRESHOLD: loud_chunks += 1
               
                if len(buffer) > MAX_BUFFER_SIZE:
                    log(f"⚠️ Recording buffer overflowed. Discarding memory and returning to Listening.")
                    buffer.clear()
                    with state_lock: app_state = "IDLE"
                    chunks, loud_chunks = 0, 0
                    continue

                if chunks >= target:
                    if loud_chunks >= (target / 2.0):
                        with state_lock: app_state = "PROCESSING"
                        threading.Thread(target=process_audio_background, args=(bytes(buffer), song_start)).start()
                    else:
                        log(f"⚠️ Sample discarded (Too quiet).")
                        with state_lock: app_state = "IDLE"
                    buffer, chunks, loud_chunks = bytearray(), 0, 0
           
            elif current_state == "SLEEPING":
                # Dropout Timer logic
                if music_rms > MUSIC_THRESHOLD:
                    silence_sleep = 0
                else:
                    silence_sleep += 1
                   
                required_silence_chunks = int(RATE / CHUNK * NEEDLE_LIFT_SECONDS)

                if silence_sleep >= required_silence_chunks:
                    log(f"🔇 {NEEDLE_LIFT_SECONDS}-second dropout timer expired. Music has stopped.")
                    if current_track and not scrobble_fired:
                        silence_duration = required_silence_chunks * (CHUNK / RATE)
                        time_played = (now - current_track['session_start_time']) - silence_duration + current_track.get('previously_played', 0)
                       
                        if time_played > 5:  
                            track_id = f"{current_track['title']} - {current_track['artist']}"
                            with state_lock:
                                paused_track_memory = {"id": track_id, "accumulated_playtime": time_played}
                            log(f"⏸️ Track aborted (Paused). Total saved playtime: {int(time_played)}s.")
                        else:
                            log("🔇 Track aborted before scrobble threshold.")
                   
                    if mqtt_client.is_connected():
                        mqtt_client.publish("vinyl_guardian/track", "None", retain=True)

                    with state_lock:
                        app_state, current_track, current_attempt, consecutive_failures, has_played_music = "IDLE", None, 1, 0, False
                    continue

                current_silence_sec = silence_sleep * (CHUNK / RATE)
                physical_now = now - current_silence_sec

                if current_track and not scrobble_fired and physical_now >= current_track.get('scrobble_trigger_time', 0):
                    track_id = f"{current_track['title']} - {current_track['artist']}"
                    if track_id != last_scrobbled_track:
                        scrobble_to_lastfm(current_track['artist'], current_track['title'], current_track['start_timestamp'], current_track['album'])
                        if mqtt_client.is_connected():
                            mqtt_client.publish("vinyl_guardian/scrobble_state", track_id, retain=True)
                            try:
                                payload = json.dumps(current_track)
                                mqtt_client.publish("vinyl_guardian/scrobble", payload, retain=True)
                            except TypeError as e:
                                log(f"⚠️ MQTT attribute serialization failed: {e}")
                            except Exception:
                                pass
                        with state_lock: scrobble_fired, last_scrobbled_track, paused_track_memory = True, track_id, None
                    else:
                        if DEBUG: log(f"⏭️ Skipping scrobble: '{track_id}' was just scrobbled.")
                        with state_lock: scrobble_fired = True

                if now >= wake_up_time:
                    cooldown_end = now + 4
                    if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/track", "None", retain=True)
                    with state_lock:
                        app_state = "COOLDOWN"
                        current_track = None
                   
            elif current_state == "COOLDOWN" and now >= cooldown_end:
                log(f"🟢 Cooldown finished. Returning to Listening.")
                with state_lock: app_state = "IDLE"

if __name__ == "__main__":
    print("\033[2J\033[H", end="", flush=True)
    print("========================================================")
    log(f"🚀 BOOTING VINYL GUARDIAN (v{VERSION} Build)...")
    print("========================================================")
   
    # NOTE: Do NOT run calibration! Just load this into the live run so it catches the ghost trigger tonight!
    if CALIBRATION_MODE:
        log("⚠️ Please turn off Calibration Mode. We need to catch the ghost triggers in the live loop!")
        sys.exit(0)
    else:
        files_to_clean = [os.path.join(SHARE_DIR, "vinyl_debug.wav"), os.path.join(SHARE_DIR, "shazam_last_match.json"), "/tmp/process.wav"]
        for f in files_to_clean:
            try:
                if os.path.exists(f):
                    os.remove(f)
                    if DEBUG: log(f"🧹 Cleaned up: {f}")
            except Exception as e:
                if DEBUG: log(f"⚠️ Could not clear {f}: {e}")
        connect_mqtt()
        listen_and_identify()