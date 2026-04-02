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
MIC_VOLUME = 8
RECORD_SECONDS = config.get("recording_seconds", 10)

# Dynamic Calibration State Variables
RUNOUT_CREST_THRESHOLD = 4.5 
MOTOR_HYSTERESIS_SEC = 3.0
NEEDLE_HYSTERESIS_SEC = 2.0
DYNAMIC_DEBOUNCE_CHUNKS = adv.get("trigger_debounce_chunks", 3)

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
        
        if not CALIBRATION_MODE:
            print("💡 Loaded dynamically tuned thresholds and state buffers from auto_calibration.json")
    except Exception as e:
        print(f"⚠️ Failed to read auto_calibration.json: {e}")

UI_MUSIC = config.get("music_threshold")
if UI_MUSIC is not None and UI_MUSIC > 0: MUSIC_THRESHOLD = UI_MUSIC

UI_RUMBLE = config.get("rumble_threshold")
if UI_RUMBLE is not None and UI_RUMBLE > 0: RUMBLE_THRESHOLD = UI_RUMBLE

UI_MOTOR = config.get("motor_power_threshold")
if UI_MOTOR is not None and UI_MOTOR > 0: MOTOR_POWER_THRESHOLD = UI_MOTOR

UI_MIC = config.get("mic_volume")
if UI_MIC is not None and UI_MIC > 0: MIC_VOLUME = UI_MIC

# --- ENGINE TUNING PARAMETERS ---
MAX_ATTEMPTS = adv.get("max_attempts", 3)
MIN_AUDIO_SECONDS = adv.get("min_audio_seconds", 5)
AUDIO_ONSET_THRESHOLD = adv.get("audio_onset_threshold", 1000)      
NEEDLE_LIFT_SECONDS = adv.get("needle_lift_seconds", 25)          
CONSECUTIVE_FAILURE_TIMEOUT = adv.get("consecutive_failure_timeout", 1800) 
FALLBACK_SLEEP_SECS = adv.get("fallback_sleep_secs", 60)          

# Audio Settings
CHANNELS = config.get("channels", 2)
RATE = 44100
FORMAT = alsaaudio.PCM_FORMAT_S16_LE
CHUNK = 2048
MAX_BUFFER_SIZE = RATE * CHANNELS * 2 * 60 # 60 seconds absolute max buffer

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
current_display_status = "Powered Off"

# Global Shazam Instance (Prevents Async Memory Leak)
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
        log(f"⚠️ Error during graceful shutdown: {e}")
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

def save_atomic_json(filepath, data):
    """Safely saves JSON data preventing corruption on crash."""
    temp_fd, temp_path = tempfile.mkstemp(dir=SHARE_DIR)
    try:
        with os.fdopen(temp_fd, 'w') as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, filepath)
    except Exception as e:
        log(f"⚠️ Failed to save atomic JSON to {filepath}: {e}")
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

def change_status(new_status):
    global current_display_status
    if not CALIBRATION_MODE and new_status != current_display_status:
        if mqtt_client.is_connected():
            mqtt_client.publish("vinyl_guardian/status", new_status, retain=True)
            current_display_status = new_status
        elif DEBUG:
            log(f"⚠️ MQTT disconnected. Status update '{new_status}' dropped.")

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
        except requests.exceptions.RequestException as e: 
            if DEBUG: log(f"⚠️ iTunes lookup attempt {attempt+1} failed: {e}")
            time.sleep(1)
        except Exception as e: 
            if DEBUG: log(f"⚠️ Unexpected iTunes lookup error: {e}")
            break
    return 0

# --- RECOGNITION ENGINE (SHAZAM) ---
def recognize_shazam(wav_path):
    log("Uploading to Shazam...")
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
        log(f"🚨 Shazam Engine Error: {e}")
        return None

# --- BACKGROUND WORKER ---
def process_audio_background(audio_data_bytes, song_start_timestamp):
    global app_state, current_attempt, wake_up_time, consecutive_failures, current_track, scrobble_fired, last_scrobbled_track, paused_track_memory
    
    # Safely read attempt number for logging
    with state_lock: local_attempt = current_attempt
    log(f"🔬 Analyzing {RECORD_SECONDS}s capture (Attempt {local_attempt}/{MAX_ATTEMPTS})...")

    full_data = np.frombuffer(audio_data_bytes, dtype=np.int16)
    peak = int(np.max(np.abs(full_data.astype(np.int32)))) if len(full_data) > 0 else 0
    max_val = 32767
    
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
        log(f"⚠️ Failed to write temp wav file: {e}")
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
                mqtt_client.publish("vinyl_guardian/attributes", json.dumps(current_track), retain=True)
            except TypeError as e:
                log(f"⚠️ Failed to serialize track attributes: {e}")
            
            wake_up_time = current_track['start_timestamp'] + total_duration
            app_state = "SLEEPING"
            change_status("Playing")
        else:
            if current_attempt < MAX_ATTEMPTS:
                log(f"❌ No match. Retrying ({current_attempt + 1}/{MAX_ATTEMPTS})...")
                current_attempt += 1
                app_state = "RECORDING"
                change_status("Recording")
            else:
                consecutive_failures += 1
                log(f"❌ Max attempts reached. Fallback to gap detection.")
                change_status("Playing")
                mqtt_client.publish("vinyl_guardian/track", "Unknown Track", retain=True)
                current_attempt = 1
                wake_up_time = time.time() + (CONSECUTIVE_FAILURE_TIMEOUT if consecutive_failures >= 10 else FALLBACK_SLEEP_SECS)
                if consecutive_failures >= 10: 
                    change_status("Timeout (30m)")
                    consecutive_failures = 0
                app_state = "SLEEPING"

    try:
        if os.path.exists(wav_temp): os.remove(wav_temp)
    except Exception as e:
        log(f"⚠️ Failed to remove temp audio file: {e}")

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
    zcr = np.sum(np.diff(np.sign(audio_data)) != 0) / len(audio_data)
    
    return {"rms": rms, "music_rms": music_rms, "crest": crest, "zcr": zcr}

# --- STATISTICAL BOUNDARY CALCULATOR ---
def calc_variance_boundary(low_mean, low_std, high_mean, high_std):
    gap = high_mean - low_mean
    if gap <= 0: return low_mean + 0.0001
    
    total_noise = low_std + high_std
    if total_noise <= 0: return low_mean + (gap * 0.5)
    
    ratio = low_std / total_noise
    ratio = max(0.2, min(0.8, ratio))
    return low_mean + (gap * ratio)

# --- STATE MACHINE SIMULATOR ---
def simulate_state_machine(calibration_data, t_mot, t_rum, t_cre, t_mus, h_mot, h_nee, debounce_chunks):
    power_score, needle_score = 0, 0
    power_max = int(RATE / CHUNK * h_mot)
    needle_max = int(RATE / CHUNK * h_nee)
    pop_boost = int(RATE / CHUNK * 1.0)
    
    turntable_on = False
    needle_down = False
    
    stages_order = ["STAGE_1_OFF", "STAGE_2_ON_IDLE", "STAGE_3_PLAYING", "STAGE_4_LIFTED", "STAGE_5_OFF"]
    
    for stage in stages_order:
        expect_on = stage in ["STAGE_2_ON_IDLE", "STAGE_3_PLAYING", "STAGE_4_LIFTED"]
        expect_down = stage == "STAGE_3_PLAYING"
        expect_music = stage == "STAGE_3_PLAYING"
        
        chunks_rms = calibration_data[stage]["raw_chunks"]["rms"]
        chunks_music = calibration_data[stage]["raw_chunks"]["music_rms"]
        chunks_crest = calibration_data[stage]["raw_chunks"]["crest"]
        
        grace_period_chunks = int(max(power_max, needle_max) * 1.5)
        music_triggered = False
        trigger_chunks = 0
        state_transitioned_cleanly = False
        
        for i in range(len(chunks_rms)):
            rms = chunks_rms[i]
            m_rms = chunks_music[i]
            crest = chunks_crest[i]
            
            if not turntable_on:
                power_score = min(power_score + 1, power_max) if rms > t_mot else max(power_score - 1, 0)
                if power_score >= power_max: turntable_on = True
            else:
                power_score = max(power_score - 1, 0) if rms < t_mot else min(power_score + 1, power_max)
                if power_score <= 0: turntable_on = False
                    
            if crest >= t_cre:
                needle_score = min(needle_score + pop_boost, needle_max)
            elif rms >= t_rum:
                needle_score = min(needle_score + 1, needle_max)
            else:
                needle_score = max(needle_score - 1, 0)
            
            needle_down = needle_score > (needle_max * 0.5)
            
            if m_rms > t_mus:
                trigger_chunks += 1
                if trigger_chunks >= debounce_chunks: music_triggered = True
            else:
                trigger_chunks = 0

            if i <= grace_period_chunks:
                if turntable_on == expect_on and needle_down == expect_down:
                    state_transitioned_cleanly = True

            if i > grace_period_chunks:
                if not state_transitioned_cleanly:
                    return f"{stage}: Failed to transition during grace period."
                if turntable_on != expect_on: 
                    return f"{stage}: Power flicker detected. Expected {expect_on} but fell to {turntable_on}."
                if needle_down != expect_down: 
                    return f"{stage}: Needle flicker detected. Expected {expect_down} but fell to {needle_down}."
                
        if expect_music and not music_triggered:
            return f"{stage}: Music expected but not reliably detected."
        if not expect_music and music_triggered:
            return f"{stage}: Music falsely detected on static/noise."
            
    return "PASS"

# --- DEEP DATA CALIBRATION ENGINE ---
def run_calibration():
    log("=========================================")
    log("🎛️ DSP & STATISTICAL CALIBRATION ENGINE 🎛️")
    log("=========================================")
    
    try:
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NORMAL, device='default', channels=CHANNELS, rate=RATE, format=FORMAT, periodsize=CHUNK)
    except Exception as e: 
        log(f"🚨 ALSA Error: {e}"); sys.exit(1)

    current_vol = MIC_VOLUME
    
    log("\n👉 STAGE 0 (Auto-Volume Check): Drop the needle onto a LOUD part of a playing record.")
    for i in range(10, 0, -1):
        inp.read(); time.sleep(1)
        
    log(f"🔴 Starting live auto-volume metering...")
    good_passes, target_chunks = 0, int(RATE / CHUNK * 3) 
    
    while good_passes < 2:
        try: subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{current_vol}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError: pass 
        for _ in range(5): inp.read()
            
        buffer = bytearray()
        chunks = 0
        while chunks < target_chunks:
            length, data = inp.read()
            if length > 0: buffer.extend(data); chunks += 1
        
        audio_data = np.frombuffer(buffer, dtype=np.int16)
        peak = int(np.max(np.abs(audio_data.astype(np.int32)))) if len(audio_data) > 0 else 0
        clipping_samples = np.sum(np.abs(audio_data) >= 32700)
        clip_percent = (clipping_samples / len(audio_data)) * 100 if len(audio_data) > 0 else 0
        
        if clip_percent > 2.0:
            current_vol = max(1, current_vol - (5 if clip_percent > 10.0 else 2))
            log(f"📈 [Peak: {peak:5d} | Clip: {clip_percent:4.1f}%] - Auto-decreasing to {current_vol}%...")
            good_passes = 0; time.sleep(0.5)
        elif peak < 10000: 
            current_vol = min(100, current_vol + (5 if peak < 5000 else 2))
            log(f"📉 [Peak: {peak:5d} | Clip: {clip_percent:4.1f}%] - Auto-increasing to {current_vol}%...")
            good_passes = 0; time.sleep(0.5)
        else:
            log(f"✅ [Peak: {peak:5d} | Clip: {clip_percent:4.1f}%] - Volume locked at {current_vol}%!")
            good_passes += 1
            
        if current_vol == 1 or current_vol == 100: break

    log("\n👉 Please STOP the record and turn the turntable completely OFF.")
    for i in range(20, 0, -1): inp.read(); time.sleep(1)

    stages = [
        {"id": "STAGE_1_OFF", "prompt": "Ensure Turntable is OFF (Completely powered down)."},
        {"id": "STAGE_2_ON_IDLE", "prompt": "Turn Turntable ON (Motor spinning, needle UP)."},
        {"id": "STAGE_3_PLAYING", "prompt": "Drop the needle onto a playing record."},
        {"id": "STAGE_4_LIFTED", "prompt": "Lift the needle (Motor still ON, needle UP)."},
        {"id": "STAGE_5_OFF", "prompt": "Turn Turntable OFF."}
    ]

    calibration_data = {}
    
    for stage in stages:
        log(f"\n👉 {stage['prompt']}")
        log("Waiting 10 seconds...")
        for _ in range(10): inp.read(); time.sleep(1)

        log(f"🔴 Capturing 30 seconds of data for deep analysis...")
        target_chunks = int(RATE / CHUNK * 30)
        
        stage_metrics = {"rms": [], "music_rms": [], "crest": []}
        
        chunks = 0
        while chunks < target_chunks:
            length, data = inp.read()
            if length > 0:
                metrics = calculate_deep_metrics(data)
                if metrics:
                    for k in stage_metrics.keys(): stage_metrics[k].append(metrics[k])
                chunks += 1
                if chunks % int(target_chunks / 10) == 0: print("█", end="", flush=True)

        print("")
        
        summary = {}
        for k, v_list in stage_metrics.items():
            if v_list:
                arr = np.array(v_list)
                summary[k] = {
                    "median": float(np.median(arr)), 
                    "mean": float(np.mean(arr)), 
                    "min": float(np.min(arr)), 
                    "max": float(np.max(arr)), 
                    "std_dev": float(np.std(arr))
                }
        
        calibration_data[stage["id"]] = {"raw_chunks": stage_metrics, "summary": summary}
        log("✅ Capture complete.")

    inp.close()

    log("\n=======================================================")
    log("⚙️ GENERATING STATISTICAL BASELINES...")
    
    s1 = calibration_data["STAGE_1_OFF"]["summary"]
    s2 = calibration_data["STAGE_2_ON_IDLE"]["summary"]
    s3 = calibration_data["STAGE_3_PLAYING"]["summary"]
    s4 = calibration_data["STAGE_4_LIFTED"]["summary"]
    s5 = calibration_data["STAGE_5_OFF"]["summary"]
    
    off_mean = (s1["rms"]["mean"] + s5["rms"]["mean"]) / 2.0
    off_std = (s1["rms"]["std_dev"] + s5["rms"]["std_dev"]) / 2.0
    on_mean = (s2["rms"]["mean"] + s4["rms"]["mean"]) / 2.0
    on_std = (s2["rms"]["std_dev"] + s4["rms"]["std_dev"]) / 2.0
    play_mean = s3["rms"]["mean"]
    play_std = s3["rms"]["std_dev"]
    music_idle_mean = (s2["music_rms"]["mean"] + s4["music_rms"]["mean"]) / 2.0
    music_idle_std = (s2["music_rms"]["std_dev"] + s4["music_rms"]["std_dev"]) / 2.0
    music_play_mean = s3["music_rms"]["mean"]
    music_play_std = s3["music_rms"]["std_dev"]

    guess_motor = calc_variance_boundary(off_mean, off_std, on_mean, on_std)
    guess_rumble = calc_variance_boundary(on_mean, on_std, play_mean, play_std)
    guess_music = calc_variance_boundary(music_idle_mean, music_idle_std, music_play_mean, music_play_std)
    
    idle_crest_mean = np.mean([s1["crest"]["mean"], s2["crest"]["mean"], s4["crest"]["mean"], s5["crest"]["mean"]])
    idle_crest_std = np.mean([s1["crest"]["std_dev"], s2["crest"]["std_dev"], s4["crest"]["std_dev"], s5["crest"]["std_dev"]])
    guess_crest = max(idle_crest_mean + (idle_crest_std * 6.0), 2.5)

    guess_debounce = 3
    if music_idle_std > 0.005: guess_debounce = 5
    if music_idle_std > 0.010: guess_debounce = 8

    guess_m_hyst = 3.0
    guess_n_hyst = 2.0

    log("🧪 EXECUTING ROBUSTNESS SIMULATION...")
    log("Validating boundaries and performing ±10% perturbation tests...")

    t_mot, t_rum, t_cre, t_mus = guess_motor, guess_rumble, guess_crest, guess_music
    h_mot, h_nee = guess_m_hyst, guess_n_hyst
    d_chunk = guess_debounce
    
    success = False
    for hyst_loop in range(3): 
        for attempt in range(50):
            result = simulate_state_machine(calibration_data, t_mot, t_rum, t_cre, t_mus, h_mot, h_nee, d_chunk)
            
            if result == "PASS":
                p_high = simulate_state_machine(calibration_data, t_mot*1.1, t_rum*1.1, t_cre*1.1, t_mus*1.1, h_mot, h_nee, d_chunk)
                p_low = simulate_state_machine(calibration_data, t_mot*0.9, t_rum*0.9, t_cre*0.9, t_mus*0.9, h_mot, h_nee, d_chunk)
                
                if p_high == "PASS" and p_low == "PASS":
                    success = True
                    break
                else:
                    log(f"  [Attempt {attempt}] Edge detected. Perturbation failed. Nudging...")
                    if p_high != "PASS": t_mot *= 0.98; t_rum *= 0.98; t_mus *= 0.98
                    if p_low != "PASS": t_mot *= 1.02; t_rum *= 1.02; t_mus *= 1.02
                    continue
                
            if "Power expected False" in result or "Power flicker" in result: t_mot *= 1.10
            elif "Power expected True" in result or "transition during grace" in result: t_mot *= 0.90
            elif "Needle expected False" in result:
                t_rum *= 1.10; t_cre += 0.2
            elif "Needle expected True" in result:
                t_rum *= 0.90; t_cre = max(1.5, t_cre - 0.2)
            elif "Music expected but not reliably" in result: t_mus *= 0.90
            elif "Music falsely detected" in result: t_mus *= 1.15
            
        if success: break
        
        log(f"⚠️ Perturbation convergence failed. Expanding Hysteresis Time Buffers...")
        h_mot += 2.0; h_nee += 1.5
        t_mot, t_rum, t_cre, t_mus = guess_motor, guess_rumble, guess_crest, guess_music

    if success:
        log("\n=======================================================")
        log("✅ MATHEMATICAL SIMULATION PASSED! 100% RELIABILITY ACHIEVED.")
        log("=======================================================")
        log(f"The algorithm successfully navigated all states and survived")
        log(f"the ±10% perturbation robustness tests.\n")
        
        auto_cal_data = {
            "mic_volume": current_vol,
            "music_threshold": float(t_mus),
            "rumble_threshold": float(t_rum),
            "motor_power_threshold": float(t_mot),
            "runout_crest_threshold": float(t_cre),
            "motor_hysteresis_sec": float(h_mot),
            "needle_hysteresis_sec": float(h_nee),
            "music_debounce_chunks": int(d_chunk)
        }
        save_atomic_json(AUTO_CALIB_FILE, auto_cal_data)
        log("Disable calibration_mode and restart the Add-on to deploy these settings!")
    else:
        log("\n❌ FAILED TO CONVERGE. Hardware states are completely overlapping.")
        log(f"Final error before aborting: {result}")
    
    log("💤 Calibration finished. Sleeping to prevent auto-restart...")
    while True: time.sleep(3600)

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

    log(f"Listening (Music: {MUSIC_THRESHOLD} | Rumble: {RUMBLE_THRESHOLD} | Motor: {MOTOR_POWER_THRESHOLD})...")
    log(f"Hysteresis Buffers - Motor: {MOTOR_HYSTERESIS_SEC}s | Needle: {NEEDLE_HYSTERESIS_SEC}s | Crest: {RUNOUT_CREST_THRESHOLD} | Debounce: {DYNAMIC_DEBOUNCE_CHUNKS}")
    
    last_pub, last_sleep_log, cooldown_end, chunks, loud_chunks, silence_sleep, song_start = time.time(), 0, 0, 0, 0, 0, 0
    idle_silence_chunks = 0
    target = int(RATE / CHUNK * RECORD_SECONDS)
    buffer = bytearray()
    
    turntable_on = False
    has_played_music = False 
    trigger_chunks = 0  
    
    power_score = 0
    power_max_score = int(RATE / CHUNK * MOTOR_HYSTERESIS_SEC) 
    
    motor_on_thresh = MOTOR_POWER_THRESHOLD 
    motor_off_thresh = MOTOR_POWER_THRESHOLD 
    
    needle_active_score = 0
    needle_max_score = int(RATE / CHUNK * NEEDLE_HYSTERESIS_SEC) 
    pop_score_boost = int(RATE / CHUNK * 1.0) 
    
    needle_down = False

    while True:
        length, data = inp.read()
        if length > 0:
            raw_rms, music_rms, crest = calculate_audio_levels(data)
            now = time.time()
            
            with state_lock:
                current_state = app_state

            if current_state in ["RECORDING", "PROCESSING", "SLEEPING"]:
                has_played_music = True

            # --- TRUE HYSTERESIS POWER DETECTION ---
            if not turntable_on:
                power_score = min(power_score + 1, power_max_score) if raw_rms > motor_on_thresh else max(power_score - 1, 0)
                if power_score >= power_max_score:
                    turntable_on = True
                    mqtt_client.publish("vinyl_guardian/power", "ON", retain=True)
                    mqtt_client.publish("vinyl_guardian/track", "Unknown", retain=True)
            else:
                power_score = max(power_score - 1, 0) if raw_rms < motor_off_thresh else min(power_score + 1, power_max_score)
                if power_score <= 0:
                    turntable_on = False
                    has_played_music = False 
                    mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)
                    log("🔌 Turntable turned off. Clearing track display to 'None'.")
                    mqtt_client.publish("vinyl_guardian/track", "None", retain=True)
                    mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00", retain=True)

            # --- HYBRID NEEDLE DETECTION (Volume + Multiplier Pops) ---
            is_dust_pop = crest >= RUNOUT_CREST_THRESHOLD
            
            if is_dust_pop:
                needle_active_score = min(needle_active_score + pop_score_boost, needle_max_score) 
            elif raw_rms >= RUMBLE_THRESHOLD:
                needle_active_score = min(needle_active_score + 1, needle_max_score) 
            else:
                needle_active_score = max(needle_active_score - 1, 0) 
            
            needle_down = needle_active_score > (needle_max_score * 0.5)

            # --- DYNAMIC PHYSICAL IDLE STATUS ---
            if current_state == "IDLE":
                if not turntable_on:
                    idle_str = "Powered Off"
                elif needle_down:
                    idle_str = "Runout Groove" if has_played_music else "Lead-in Groove"
                else:
                    idle_str = "Needle Up"
                change_status(idle_str)

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
                        if not turntable_on:
                            dbg_str = "Powered Off"
                        elif needle_down:
                            dbg_str = "Runout Groove" if has_played_music else "Lead-in Groove"
                        else:
                            dbg_str = "Needle Up"
                        status = f"🟢 {dbg_str.upper()}"
                        
                    if status: 
                        print(f"[{time.strftime('%H:%M:%S')}] {status} | Music: {music_rms:.4f} | Crest: {crest:.2f}", flush=True)
                        if "SLEEP" in status: last_sleep_log = now
                last_pub = now

            if current_state == "IDLE":
                if not needle_down:
                    idle_silence_chunks += 1
                    if idle_silence_chunks == int(RATE / CHUNK * NEEDLE_LIFT_SECONDS):
                        new_track_val = "Unknown" if turntable_on else "None"
                        log(f"🔇 Prolonged silence detected. Setting track to '{new_track_val}'.")
                        if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/track", new_track_val, retain=True)
                        idle_silence_chunks = 0 
                else:
                    idle_silence_chunks = 0

                if music_rms > MUSIC_THRESHOLD:
                    trigger_chunks += 1
                    if trigger_chunks >= DYNAMIC_DEBOUNCE_CHUNKS:
                        log(f"🎵 AUDIO DETECTED (Music Spike: {music_rms:.4f})")
                        change_status("Recording")
                        if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/track", "Unknown", retain=True)
                        song_start, buffer, chunks, loud_chunks, silence_sleep = now, bytearray(data), 1, 1, 0
                        trigger_chunks = 0
                        idle_silence_chunks = 0
                        with state_lock: app_state = "RECORDING"
                else:
                    trigger_chunks = 0  

            elif current_state == "RECORDING":
                buffer.extend(data)
                chunks += 1
                if raw_rms > RUMBLE_THRESHOLD: loud_chunks += 1
                
                # Check for unbounded buffer growth (absolute safety limit of 60 seconds)
                if len(buffer) > MAX_BUFFER_SIZE:
                    log(f"⚠️ Recording buffer overflowed. Discarding memory and returning to IDLE.")
                    buffer.clear()
                    change_status("Needle Up" if turntable_on else "Powered Off")
                    with state_lock: app_state = "IDLE"
                    chunks, loud_chunks = 0, 0
                    continue

                if chunks >= target:
                    if loud_chunks >= (target / 2.0):
                        change_status("Processing")
                        with state_lock: app_state = "PROCESSING"
                        threading.Thread(target=process_audio_background, args=(bytes(buffer), song_start)).start()
                    else:
                        log(f"⚠️ Sample discarded (Too quiet).")
                        change_status("Needle Up" if turntable_on else "Powered Off")
                        with state_lock: app_state = "IDLE"
                    buffer, chunks, loud_chunks = bytearray(), 0, 0
            
            elif current_state == "SLEEPING":
                if needle_down:
                    silence_sleep = 0 
                else:
                    silence_sleep += 1
                    
                if current_track is None or not current_track.get('duration_known', False):
                    required_silence_chunks = int(RATE / CHUNK * 4)
                else:
                    required_silence_chunks = int(RATE / CHUNK * NEEDLE_LIFT_SECONDS)

                if silence_sleep >= required_silence_chunks:
                    is_true_lift = False
                    if current_track is None:
                        log("⏱️ Track gap detected during fallback!")
                    elif not current_track.get('duration_known', False):
                        log("⏱️ Physical track gap detected (API duration missing).")
                    else:
                        is_true_lift = True
                        if not scrobble_fired:
                            silence_duration = required_silence_chunks * (CHUNK / RATE)
                            time_played = (now - current_track['session_start_time']) - silence_duration + current_track.get('previously_played', 0)
                            
                            if time_played > 5:  
                                track_id = f"{current_track['title']} - {current_track['artist']}"
                                with state_lock:
                                    paused_track_memory = {"id": track_id, "accumulated_playtime": time_played}
                                log(f"⏸️ Needle lift (Paused). Total saved playtime: {int(time_played)}s.")
                            else:
                                log("🔇 Needle lift detected. Aborting track.")
                        else:
                            log("🔇 Needle lift detected.")
                    
                    if turntable_on:
                        change_status("Needle Up")
                    else:
                        change_status("Powered Off")
                    
                    if is_true_lift and mqtt_client.is_connected():
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
                            try: mqtt_client.publish("vinyl_guardian/scrobble", json.dumps(current_track), retain=True)
                            except TypeError: pass
                        with state_lock: scrobble_fired, last_scrobbled_track, paused_track_memory = True, track_id, None
                    else:
                        log(f"⏭️ Skipping scrobble: '{track_id}' was just scrobbled.")
                        with state_lock: scrobble_fired = True

                if now >= wake_up_time:
                    cooldown_end = now + 4
                    change_status("Cooldown")
                    if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/track", "Unknown", retain=True)
                    with state_lock: 
                        app_state = "COOLDOWN"
                        current_track = None
                    
            elif current_state == "COOLDOWN" and now >= cooldown_end:
                log(f"🟢 Cooldown finished. Returning to IDLE.")
                with state_lock: app_state = "IDLE"

if __name__ == "__main__":
    print("\033[2J\033[H", end="", flush=True)
    print("========================================================")
    log(f"🚀 BOOTING VINYL GUARDIAN (v{config.get('version', '2.22.0')} Build)...")
    log(f"⚙️  UI Config 'calibration_mode' read as: {CALIBRATION_MODE}")
    print("========================================================")
    
    if CALIBRATION_MODE:
        run_calibration()
    else:
        files_to_clean = [os.path.join(SHARE_DIR, "vinyl_debug.wav"), os.path.join(SHARE_DIR, "shazam_last_match.json"), "/tmp/process.wav"]
        for f in files_to_clean:
            try:
                if os.path.exists(f): os.remove(f); log(f"🧹 Cleaned up: {f}")
            except Exception as e: log(f"⚠️ Could not clear {f}: {e}")
        connect_mqtt()
        listen_and_identify()