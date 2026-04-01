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
RUNOUT_CREST_THRESHOLD = 4.5 

if os.path.exists(AUTO_CALIB_FILE):
    try:
        with open(AUTO_CALIB_FILE, 'r') as f:
            auto_cal = json.load(f)
        MUSIC_THRESHOLD = auto_cal.get("music_threshold", MUSIC_THRESHOLD)
        RUMBLE_THRESHOLD = auto_cal.get("rumble_threshold", RUMBLE_THRESHOLD)
        MOTOR_POWER_THRESHOLD = auto_cal.get("motor_power_threshold", MOTOR_POWER_THRESHOLD)
        MIC_VOLUME = auto_cal.get("mic_volume", MIC_VOLUME)
        if not CALIBRATION_MODE:
            print("💡 Loaded tuned thresholds from auto_calibration.json")
    except Exception as e:
        print(f"⚠️ Failed to read auto_calibration.json: {e}")

UI_MUSIC = config.get("music_threshold")
if UI_MUSIC is not None and UI_MUSIC > 0:
    MUSIC_THRESHOLD = UI_MUSIC

UI_RUMBLE = config.get("rumble_threshold")
if UI_RUMBLE is not None and UI_RUMBLE > 0:
    RUMBLE_THRESHOLD = UI_RUMBLE

UI_MOTOR = config.get("motor_power_threshold")
if UI_MOTOR is not None and UI_MOTOR > 0:
    MOTOR_POWER_THRESHOLD = UI_MOTOR

UI_MIC = config.get("mic_volume")
if UI_MIC is not None and UI_MIC > 0:
    MIC_VOLUME = UI_MIC

# --- ENGINE TUNING PARAMETERS ---
MAX_ATTEMPTS = adv.get("max_attempts", 3)
MIN_AUDIO_SECONDS = adv.get("min_audio_seconds", 5)
AUDIO_ONSET_THRESHOLD = adv.get("audio_onset_threshold", 1000)      
TRIGGER_DEBOUNCE_CHUNKS = adv.get("trigger_debounce_chunks", 3)       
NEEDLE_LIFT_SECONDS = adv.get("needle_lift_seconds", 25)          
CONSECUTIVE_FAILURE_TIMEOUT = adv.get("consecutive_failure_timeout", 1800) 
FALLBACK_SLEEP_SECS = adv.get("fallback_sleep_secs", 60)          

# Audio Settings
CHANNELS = config.get("channels", 2)
RATE = 44100
FORMAT = alsaaudio.PCM_FORMAT_S16_LE
CHUNK = 2048

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

def log(message):
    print(f"[Vinyl Guardian] {message}", flush=True)

def signal_handler(sig, frame):
    log("🛑 Shutting down gracefully...")
    try:
        global inp
        if inp is not None: 
            inp.close()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    except Exception as e: pass
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# --- LAST.FM SETUP ---
lastfm_network = None
if not CALIBRATION_MODE and LFM_USER and LFM_PASS and LFM_KEY and LFM_SECRET:
    try:
        lastfm_network = pylast.LastFMNetwork(
            api_key=LFM_KEY,
            api_secret=LFM_SECRET,
            username=LFM_USER,
            password_hash=pylast.md5(LFM_PASS)
        )
        log("✅ Last.fm integration initialized.")
    except Exception as e:
        log(f"🚨 Last.fm initialization failed: {e}")

def scrobble_to_lastfm(artist, title, start_timestamp, album=None):
    if not lastfm_network: return
    try:
        kwargs = {"artist": artist, "title": title, "timestamp": start_timestamp}
        if album and album != "Unknown": kwargs["album"] = album
        lastfm_network.scrobble(**kwargs)
        log(f"🎵 Successfully scrobbled to Last.fm: {title} by {artist}")
    except Exception as e:
        log(f"🚨 Last.fm Scrobble Failed: {e}")

# --- MQTT SETUP ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

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
        mqtt_client.publish("vinyl_guardian/status", new_status, retain=True)
        current_display_status = new_status

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
            if data.get('resultCount', 0) > 0:
                return data['results'][0].get('trackTimeMillis', 0) / 1000.0
        except requests.exceptions.RequestException: time.sleep(1)
        except Exception: break
    return 0

# --- RECOGNITION ENGINE (SHAZAM) ---
def recognize_shazam(wav_path):
    log("Uploading to Shazam...")
    try:
        async def _recognize():
            shazam = Shazam()
            return await shazam.recognize(wav_path)
        res_json = asyncio.run(_recognize())
        if DEBUG:
            try:
                with open(os.path.join(SHARE_DIR, "shazam_last_match.json"), "w") as f: json.dump(res_json, f, indent=2)
            except: pass

        if isinstance(res_json, dict) and 'track' in res_json and isinstance(res_json.get('matches'), list) and len(res_json['matches']) > 0:
            track = res_json['track']
            if not isinstance(track, dict): return None
            title = track.get('title', 'Unknown')
            artist = track.get('subtitle', 'Unknown')
            album = "Unknown"
            duration = 0
            release_year = "Unknown"
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
    log(f"🔬 Analyzing {RECORD_SECONDS}s capture (Attempt {current_attempt}/{MAX_ATTEMPTS})...")

    full_data = np.frombuffer(audio_data_bytes, dtype=np.int16)
    peak = int(np.max(np.abs(full_data.astype(np.int32)))) if len(full_data) > 0 else 0
    max_val = 32767
    
    if peak >= max_val: log(f"🎸 Audio peaked at max digital volume.")
    elif peak < 2000: log(f"⚠️ VERY QUIET (Peak: {peak}/{max_val}).")

    abs_data = np.abs(full_data)
    trigger = np.where(abs_data > AUDIO_ONSET_THRESHOLD)[0]
    start_idx = trigger[0] if len(trigger) > 0 else 0
    min_s = RATE * MIN_AUDIO_SECONDS 
    if len(full_data) - start_idx < min_s: start_idx = max(0, len(full_data) - min_s)

    trimmed_bytes = full_data[start_idx:].tobytes()
    trimmed_seconds = start_idx / RATE
    wav_temp = "/tmp/process.wav"

    with wave.open(wav_temp, "wb") as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(trimmed_bytes)
    
    if DEBUG:
        try:
            with wave.open(os.path.join(SHARE_DIR, "vinyl_debug.wav"), "wb") as wf:
                wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(trimmed_bytes)
        except: pass

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
            if raw_offset > 2:
                log(f"📍 Late start detected (Offset: {int(raw_offset)}s).")
                scrobble_delay = max(2, scrobble_delay - late_start_offset)

            previously_played = 0
            if paused_track_memory and paused_track_memory["id"] == track_id:
                previously_played = paused_track_memory["accumulated_playtime"]
                scrobble_delay = max(2, scrobble_delay - previously_played)
                log(f"▶️ Resuming track! Recovered {int(previously_played)}s playtime. Scrobbling in {int(scrobble_delay)}s.")
            
            paused_track_memory = None 
            
            current_track = {
                "title": match['title'], "artist": match['artist'], "album": match['album'],
                "duration": total_duration, "start_timestamp": int(song_start_timestamp + trimmed_seconds - raw_offset),
                "session_start_time": song_start_timestamp, "scrobble_trigger_time": song_start_timestamp + scrobble_delay, 
                "duration_known": duration_known, "previously_played": previously_played + late_start_offset,
                "source": "Shazam"
            }
            scrobble_fired = False

            log(f"🎶 MATCH FOUND: {match['title']} - {match['artist']}")
            mqtt_client.publish("vinyl_guardian/track", f"{match['title']} - {match['artist']}", retain=True)
            mqtt_client.publish("vinyl_guardian/attributes", json.dumps(current_track), retain=True)
            
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
    """Calculates an exhaustive list of DSP metrics for calibration analysis."""
    audio_data = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    if len(audio_data) <= 1: return None
    
    # Volume
    rms = float(np.sqrt(np.mean(np.square(audio_data)))) / 32768.0
    
    # Music/High Pass
    filtered_data = audio_data[1:] - 0.95 * audio_data[:-1]
    music_rms = float(np.sqrt(np.mean(np.square(filtered_data)))) / 32768.0
    
    # Spikiness
    peak = np.max(np.abs(audio_data)) / 32768.0
    crest = peak / rms if rms > 0 else 1.0
    
    # Frequency ZCR
    zcr = np.sum(np.diff(np.sign(audio_data)) != 0) / len(audio_data)
    
    # High-Freq Energy (>10kHz)
    fft_out = np.abs(np.fft.rfft(audio_data))
    freqs = np.fft.rfftfreq(len(audio_data), 1.0/RATE)
    hf_energy = np.sum(fft_out[freqs > 10000])
    total_energy = np.sum(fft_out)
    hf_ratio = hf_energy / total_energy if total_energy > 0 else 0
    
    return {"rms": rms, "music_rms": music_rms, "crest": crest, "zcr": zcr, "hf_ratio": hf_ratio}

# --- DEEP DATA CALIBRATION ENGINE ---
def run_calibration():
    log("=========================================")
    log("🎛️ VINYL GUARDIAN DEEP DATA COLLECTOR 🎛️")
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
        
        stage_metrics = {"rms": [], "music_rms": [], "crest": [], "zcr": [], "hf_ratio": []}
        
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
        
        # Calculate summary statistics for this stage
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
    
    # Save the massive dataset for deep review
    json_path = os.path.join(SHARE_DIR, "calibration_deep_data.json")
    try:
        with open(json_path, "w") as f: json.dump(calibration_data, f, indent=4)
        log(f"\n💾 Full dataset saved to {json_path}")
    except Exception as e:
        log(f"⚠️ Failed to save JSON: {e}")

    # --- PRINT THE DIAGNOSTIC MATRIX (OFF vs ON) ---
    log("\n=========================================================================")
    log("📊 CRITICAL DIAGNOSTIC: TURNTABLE OFF vs. MOTOR ON (NEEDLE UP) 📊")
    log("=========================================================================")
    
    s1 = calibration_data["STAGE_1_OFF"]["summary"]
    s2 = calibration_data["STAGE_2_ON_IDLE"]["summary"]
    
    def calc_diff(off_val, on_val):
        return f"{on_val/off_val:.2f}x" if off_val > 0 else "N/A"

    log(f"{'Metric':<25} | {'OFF (Stage 1)':<15} | {'ON (Stage 2)':<15} | {'Diff'}")
    log(f"-" * 68)
    log(f"Raw RMS (Median)          | {s1['rms']['median']:<15.6f} | {s2['rms']['median']:<15.6f} | {calc_diff(s1['rms']['median'], s2['rms']['median'])}")
    log(f"Raw RMS (Mean)            | {s1['rms']['mean']:<15.6f} | {s2['rms']['mean']:<15.6f} | {calc_diff(s1['rms']['mean'], s2['rms']['mean'])}")
    log(f"Raw RMS (Max Peak)        | {s1['rms']['max']:<15.6f} | {s2['rms']['max']:<15.6f} | {calc_diff(s1['rms']['max'], s2['rms']['max'])}")
    log(f"Raw RMS (Min Floor)       | {s1['rms']['min']:<15.6f} | {s2['rms']['min']:<15.6f} | {calc_diff(s1['rms']['min'], s2['rms']['min'])}")
    log(f"Raw RMS (Std Dev/Flutter) | {s1['rms']['std_dev']:<15.6f} | {s2['rms']['std_dev']:<15.6f} | {calc_diff(s1['rms']['std_dev'], s2['rms']['std_dev'])}")
    log(f"-" * 68)
    log(f"Crest Factor (Max)        | {s1['crest']['max']:<15.6f} | {s2['crest']['max']:<15.6f} | {calc_diff(s1['crest']['max'], s2['crest']['max'])}")
    log(f"Crest Factor (Median)     | {s1['crest']['median']:<15.6f} | {s2['crest']['median']:<15.6f} | {calc_diff(s1['crest']['median'], s2['crest']['median'])}")
    log(f"-" * 68)
    log(f"ZCR (Median Freq Density) | {s1['zcr']['median']:<15.6f} | {s2['zcr']['median']:<15.6f} | {calc_diff(s1['zcr']['median'], s2['zcr']['median'])}")
    log(f"High-Freq Ratio (Median)  | {s1['hf_ratio']['median']:<15.6f} | {s2['hf_ratio']['median']:<15.6f} | {calc_diff(s1['hf_ratio']['median'], s2['hf_ratio']['median'])}")
    log("=========================================================================\n")

    # Reverting to basic thresholding for immediate safe use while we analyze data
    off_rumble = (s1["rms"]["median"] + calibration_data["STAGE_5_OFF"]["summary"]["rms"]["median"]) / 2.0
    on_rumble = (s2["rms"]["median"] + calibration_data["STAGE_4_LIFTED"]["summary"]["rms"]["median"]) / 2.0
    play_rumble = calibration_data["STAGE_3_PLAYING"]["summary"]["rms"]["median"]
    
    on_music = (s2["music_rms"]["median"] + calibration_data["STAGE_4_LIFTED"]["summary"]["music_rms"]["median"]) / 2.0
    play_music = calibration_data["STAGE_3_PLAYING"]["summary"]["music_rms"]["median"]

    guess_motor = off_rumble + ((on_rumble - off_rumble) * 0.5) 
    guess_rumble = on_rumble + ((play_rumble - on_rumble) * 0.15) 
    guess_music = on_music + ((play_music - on_music) * 0.08) 

    def round_sig(x, sig=2):
        if x <= 0: return 0.0001
        return round(x, sig-int(np.floor(np.log10(abs(x))))-1)
        
    final_motor = round_sig(guess_motor)
    final_rumble = round_sig(guess_rumble)
    final_music = round_sig(guess_music)

    auto_cal_data = {
        "mic_volume": current_vol,
        "music_threshold": final_music,
        "rumble_threshold": final_rumble,
        "motor_power_threshold": final_motor
    }
    try:
        with open(AUTO_CALIB_FILE, "w") as f: json.dump(auto_cal_data, f, indent=4)
    except: pass

    log("🎯 Baseline thresholds saved so the Add-on can still function.")
    log("Please copy the CRITICAL DIAGNOSTIC matrix above so we can find the hidden motor fingerprint!")
    
    log("💤 Calibration finished. Sleeping to prevent auto-restart...")
    while True:
        time.sleep(3600)

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
    
    last_pub, last_sleep_log, cooldown_end, chunks, loud_chunks, silence_sleep, song_start = time.time(), 0, 0, 0, 0, 0, 0
    idle_silence_chunks = 0
    target = int(RATE / CHUNK * RECORD_SECONDS)
    buffer = bytearray()
    
    turntable_on = False
    has_played_music = False 
    trigger_chunks = 0  
    
    power_score = 0
    power_max_score = int(RATE / CHUNK * 4) 
    
    motor_on_thresh = MOTOR_POWER_THRESHOLD 
    motor_off_thresh = MOTOR_POWER_THRESHOLD 
    
    needle_active_score = 0
    needle_max_score = int(RATE / CHUNK * 3.0) 
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

            # --- TRUE HYSTERESIS POWER DETECTION (RESTORED) ---
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

            # --- HYBRID NEEDLE DETECTION ---
            is_dust_pop = crest >= RUNOUT_CREST_THRESHOLD
            
            if is_dust_pop:
                needle_active_score = needle_max_score 
            elif raw_rms >= RUMBLE_THRESHOLD:
                needle_active_score = min(needle_active_score + 1, needle_max_score) 
            else:
                needle_active_score = max(needle_active_score - 1, 0) 
            
            needle_down = needle_active_score > 0

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
                    if current_state == "RECORDING": status = f"🔴 REC {int((chunks/target)*100)}%"
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
                        mqtt_client.publish("vinyl_guardian/track", new_track_val, retain=True)
                        idle_silence_chunks = 0 
                else:
                    idle_silence_chunks = 0

                if music_rms > MUSIC_THRESHOLD:
                    trigger_chunks += 1
                    if trigger_chunks >= TRIGGER_DEBOUNCE_CHUNKS:
                        log(f"🎵 AUDIO DETECTED (Music Spike: {music_rms:.4f})")
                        change_status("Recording")
                        mqtt_client.publish("vinyl_guardian/track", "Unknown", retain=True)
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
                    
                    if is_true_lift:
                        new_track_val = "Unknown" if turntable_on else "None"
                        mqtt_client.publish("vinyl_guardian/track", new_track_val, retain=True)

                    with state_lock: 
                        app_state, current_track, current_attempt, consecutive_failures, has_played_music = "IDLE", None, 1, 0, False
                    continue

                current_silence_sec = silence_sleep * (CHUNK / RATE)
                physical_now = now - current_silence_sec

                if current_track and not scrobble_fired and physical_now >= current_track.get('scrobble_trigger_time', 0):
                    track_id = f"{current_track['title']} - {current_track['artist']}"
                    if track_id != last_scrobbled_track:
                        scrobble_to_lastfm(current_track['artist'], current_track['title'], current_track['start_timestamp'], current_track['album'])
                        mqtt_client.publish("vinyl_guardian/scrobble_state", track_id, retain=True)
                        mqtt_client.publish("vinyl_guardian/scrobble", json.dumps(current_track), retain=True)
                        with state_lock: scrobble_fired, last_scrobbled_track, paused_track_memory = True, track_id, None
                    else:
                        log(f"⏭️ Skipping scrobble: '{track_id}' was just scrobbled.")
                        with state_lock: scrobble_fired = True

                if now >= wake_up_time:
                    cooldown_end = now + 4
                    change_status("Cooldown")
                    mqtt_client.publish("vinyl_guardian/track", "Unknown", retain=True)
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
            except: pass
        connect_mqtt()
        listen_and_identify()