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

# Core Thresholds
MUSIC_THRESHOLD = config.get("music_threshold", 0.005)
RUMBLE_THRESHOLD = config.get("rumble_threshold", 0.015)
MOTOR_POWER_THRESHOLD = config.get("motor_power_threshold", 0.0045)
RECORD_SECONDS = config.get("recording_seconds", 10)

# Load advanced dictionary
adv = config.get("advanced", {})

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
    """Ensures audio devices and MQTT connections are safely released on exit."""
    log("🛑 Shutting down gracefully...")
    try:
        global inp
        if inp is not None: 
            inp.close()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    except Exception as e: 
        pass
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
    """Pushes the identified track to the Last.fm backend."""
    if not lastfm_network:
        return
    try:
        kwargs = {"artist": artist, "title": title, "timestamp": start_timestamp}
        if album and album != "Unknown":
            kwargs["album"] = album
        lastfm_network.scrobble(**kwargs)
        log(f"🎵 Successfully scrobbled to Last.fm: {title} by {artist}")
    except Exception as e:
        log(f"🚨 Last.fm Scrobble Failed: {e}")

# --- MQTT SETUP & DISCOVERY ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def publish_discovery():
    """Publishes Home Assistant MQTT Auto-Discovery payloads to instantly create dashboard sensors."""
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
            "name": c["name"],
            "state_topic": f"vinyl_guardian/{c['topic']}",
            "unique_id": f"vinyl_guardian_{key}",
            "device": device_info,
            "icon": c["icon"]
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
    """Globally deduplicated MQTT status publisher."""
    global current_display_status
    if not CALIBRATION_MODE and new_status != current_display_status:
        mqtt_client.publish("vinyl_guardian/status", new_status, retain=True)
        current_display_status = new_status

# --- HELPER: GET TRACK DURATION (Unique adamID Lookup with Retry) ---
def get_track_duration(title, artist, adamid=None):
    """Fetches exact track durations from iTunes to support gapless playback sleeping."""
    max_retries = 2
    for attempt in range(max_retries):
        try:
            if adamid:
                url = f"https://itunes.apple.com/lookup?id={adamid}"
            else:
                query = urllib.parse.quote(f"{title} {artist}")
                url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=1"
                
            res = requests.get(url, timeout=10)
            data = res.json()
            if data.get('resultCount', 0) > 0:
                return data['results'][0].get('trackTimeMillis', 0) / 1000.0
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                if DEBUG: log(f"[DEBUG] iTunes timeout, retry {attempt + 1}...")
                time.sleep(1)
            else:
                if DEBUG: log(f"[DEBUG] Failed to fetch track duration: {e}")
        except Exception as e:
            if DEBUG: log(f"[DEBUG] Unexpected iTunes error: {e}")
            break
    return 0

# --- RECOGNITION ENGINE (SHAZAM) ---
def recognize_shazam(wav_path):
    """Submits the captured audio file to Shazam and safely parses the response."""
    log("Uploading to Shazam...")
    try:
        async def _recognize():
            shazam = Shazam()
            return await shazam.recognize(wav_path)
            
        res_json = asyncio.run(_recognize())
        
        if DEBUG:
            try:
                result_file = os.path.join(SHARE_DIR, "shazam_last_match.json")
                with open(result_file, "w") as f: 
                    json.dump(res_json, f, indent=2)
            except (IOError, OSError): pass

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
    """Isolated thread that handles formatting audio, calling APIs, and updating global track states."""
    global app_state, current_attempt, wake_up_time, consecutive_failures, current_track, scrobble_fired, last_scrobbled_track, paused_track_memory
    log(f"🔬 Analyzing {RECORD_SECONDS}s capture (Attempt {current_attempt}/{MAX_ATTEMPTS})...")

    full_data = np.frombuffer(audio_data_bytes, dtype=np.int16)
    
    peak = int(np.max(np.abs(full_data.astype(np.int32)))) if len(full_data) > 0 else 0
    max_val = 32767
    
    # Relaxed clipping warning - Shazam survives clipping easily
    if peak >= max_val: 
        log(f"🎸 Audio peaked at max digital volume. (If matches are failing, try running calibration_mode)")
    elif peak < 2000: 
        log(f"⚠️ VERY QUIET (Peak: {peak}/{max_val}). (If matches are failing, try running calibration_mode)")

    abs_data = np.abs(full_data)
    trigger = np.where(abs_data > AUDIO_ONSET_THRESHOLD)[0]
    start_idx = trigger[0] if len(trigger) > 0 else 0
    
    min_s = RATE * MIN_AUDIO_SECONDS 
    if len(full_data) - start_idx < min_s: start_idx = max(0, len(full_data) - min_s)

    trimmed_bytes = full_data[start_idx:].tobytes()
    trimmed_seconds = start_idx / RATE
    
    wav_temp = "/tmp/process.wav"
    wav_debug = os.path.join(SHARE_DIR, "vinyl_debug.wav")

    with wave.open(wav_temp, "wb") as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(trimmed_bytes)
    
    if DEBUG:
        try:
            with wave.open(wav_debug, "wb") as wf:
                wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(trimmed_bytes)
        except (IOError, OSError): pass

    match = recognize_shazam(wav_temp)

    with state_lock:
        if match:
            current_attempt = 1 
            consecutive_failures = 0
            total_duration = match.get('duration', 0)
            
            if total_duration <= 0:
                log("Fetching total track duration from iTunes lookup...")
                total_duration = get_track_duration(match['title'], match['artist'], match.get('adamid'))
                
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
                log(f"📍 Late start detected (Offset: {int(raw_offset)}s). Crediting {int(late_start_offset)}s of playtime.")
                scrobble_delay = max(2, scrobble_delay - late_start_offset)

            previously_played = 0
            if paused_track_memory and paused_track_memory["id"] == track_id:
                previously_played = paused_track_memory["accumulated_playtime"]
                scrobble_delay = max(2, scrobble_delay - previously_played)
                log(f"▶️ Resuming track! Recovered {int(previously_played)}s playtime. Scrobbling in {int(scrobble_delay)}s.")
            
            paused_track_memory = None 
            
            current_track = {
                "title": match['title'], "artist": match['artist'], "album": match['album'],
                "duration": total_duration, 
                "start_timestamp": int(song_start_timestamp + trimmed_seconds - raw_offset),
                "session_start_time": song_start_timestamp, 
                "scrobble_trigger_time": song_start_timestamp + scrobble_delay, 
                "duration_known": duration_known,
                "previously_played": previously_played + late_start_offset,
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
                log(f"❌ Max attempts reached. Fallback to 1m sleep / Track Gap detection.")
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
    except OSError: pass

    if TEST_CAPTURE_MODE:
        log("🛑 TEST CAPTURE COMPLETE. Shutting down."); os._exit(0)

# --- AUDIO MATH ---
def calculate_audio_levels(data):
    """Calculates both broadband rumble (motor/needle) and high-pass music volume."""
    try:
        audio_data = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        if len(audio_data) <= 1: return 0.0, 0.0
        
        # Standard RMS
        raw_rms = float(np.sqrt(np.mean(np.square(audio_data)))) / 32768.0
        
        # High Pass RMS (Music)
        filtered_data = audio_data[1:] - 0.95 * audio_data[:-1]
        music_rms = float(np.sqrt(np.mean(np.square(filtered_data)))) / 32768.0

        return raw_rms, music_rms
    except Exception as e: 
        if DEBUG: log(f"[DEBUG] Audio level calculation error: {e}")
        return 0.0, 0.0

# --- CALIBRATION MODE ENGINE ---
def run_calibration():
    """Guided setup sequence to verify volume and generate empirical threshold data."""
    log("=========================================")
    log("🎛️ VINYL GUARDIAN CALIBRATION MODE 🎛️")
    log("=========================================")
    
    try:
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NORMAL, device='default', channels=CHANNELS, rate=RATE, format=FORMAT, periodsize=CHUNK)
    except Exception as e: 
        log(f"🚨 ALSA Error: {e}"); sys.exit(1)

    # --- AUTO-VOLUME CALIBRATION (STAGE 0) ---
    current_vol = config.get("mic_volume", 10)
    
    log("\n👉 STAGE 0 (Auto-Volume Check): Drop the needle onto a LOUD part of a playing record.")
    log("The script will now automatically adjust your software input volume until it finds the sweet spot.")
    log("Waiting 10 seconds for you to drop the needle...")
    for i in range(10, 0, -1):
        log(f"... {i} ...")
        inp.read() # Drain buffer
        time.sleep(1)
        
    log(f"🔴 Starting live auto-volume metering (Current Start Volume: {current_vol}%)...")
    
    good_passes = 0
    target_chunks = int(RATE / CHUNK * 3) # 3 seconds per check for fast live feedback
    
    while good_passes < 2:
        # Apply the current volume setting directly to the pulse/alsa system
        subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{current_vol}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Drain old audio from the buffer before analyzing the new volume
        for _ in range(5):
            inp.read()
            
        buffer = bytearray()
        chunks = 0
        while chunks < target_chunks:
            length, data = inp.read()
            if length > 0:
                buffer.extend(data)
                chunks += 1
        
        audio_data = np.frombuffer(buffer, dtype=np.int16)
        peak = int(np.max(np.abs(audio_data.astype(np.int32)))) if len(audio_data) > 0 else 0
        
        clipping_samples = np.sum(np.abs(audio_data) >= 32700)
        clip_percent = (clipping_samples / len(audio_data)) * 100 if len(audio_data) > 0 else 0
        
        if clip_percent > 5.0 or peak >= 32000:
            step = 5 if clip_percent > 15.0 else 2
            current_vol = max(1, current_vol - step)
            log(f"📈 [Peak: {peak:5d} | Clip: {clip_percent:4.1f}%] - TOO LOUD. Auto-decreasing to {current_vol}%...")
            good_passes = 0
            time.sleep(0.5)
        elif peak < 3000:
            step = 5 if peak < 1000 else 2
            current_vol = min(100, current_vol + step)
            log(f"📉 [Peak: {peak:5d} | Clip: {clip_percent:4.1f}%] - TOO QUIET. Auto-increasing to {current_vol}%...")
            good_passes = 0
            time.sleep(0.5)
        else:
            log(f"✅ [Peak: {peak:5d} | Clip: {clip_percent:4.1f}%] - PERFECT volume locked at {current_vol}%! Holding...")
            good_passes += 1
            
        if current_vol == 1 or current_vol == 100:
            log(f"⚠️ Hit volume limit ({current_vol}%). Cannot adjust further. Proceeding with best effort.")
            break
            
    log("\n=========================================")
    log(f"👉 VOLUME LOCKED IN AT {current_vol}%. Transitioning to Threshold Calibration.")
    log("👉 Please STOP the record and turn the turntable completely OFF.")
    log("Waiting 20 seconds for you to prepare...")
    for i in range(20, 0, -1):
        log(f"... {i} ...")
        inp.read()
        time.sleep(1)

    calibration_data = {}
    
    stages = [
        {"id": "STAGE_1_OFF", "prompt": "Ensure Turntable is OFF (Completely powered down).", "desc": "Baseline noise floor"},
        {"id": "STAGE_2_ON_IDLE", "prompt": "Turn Turntable ON (Motor spinning, needle UP).", "desc": "Motor Hum detection"},
        {"id": "STAGE_3_PLAYING", "prompt": "Drop the needle onto a playing record.", "desc": "Needle drop / Music detection"},
        {"id": "STAGE_4_LIFTED", "prompt": "Lift the needle (Motor still ON, needle UP).", "desc": "Verifying recovery"},
        {"id": "STAGE_5_OFF", "prompt": "Turn Turntable OFF.", "desc": "Verifying complete silence"}
    ]

    for stage in stages:
        log(f"\n👉 {stage['prompt']}")
        log("Waiting 10 seconds for you to do this...")
        
        # Clear buffer and countdown
        for i in range(10, 0, -1):
            log(f"... {i} ...")
            inp.read() # Drain buffer so we don't capture the past
            time.sleep(1)

        log(f"🔴 Capturing 30 seconds of data for: {stage['desc']}...")
        
        target_chunks = int(RATE / CHUNK * 30)
        raw_history = []
        music_history = []
        
        chunks = 0
        while chunks < target_chunks:
            length, data = inp.read()
            if length > 0:
                raw_rms, music_rms = calculate_audio_levels(data)
                raw_history.append(raw_rms)
                music_history.append(music_rms)
                chunks += 1
                
                # Print progress bar
                if chunks % int(target_chunks / 10) == 0:
                    print("█", end="", flush=True)

        print("") # Newline after progress bar
        
        calibration_data[stage["id"]] = {
            "raw_rms": raw_history,
            "music_rms": music_history
        }
        log("✅ Capture complete.")

    inp.close()
    
    log("\n=========================================")
    log("📊 CALIBRATION COMPLETE. CALCULATING... ")
    log("=========================================")
    
    # Analyze Data (Using Median to ignore static pops)
    results = {}
    for stage_id, data in calibration_data.items():
        results[stage_id] = {
            "raw_median": float(np.median(data["raw_rms"])),
            "music_median": float(np.median(data["music_rms"]))
        }
        log(f"{stage_id}: Raw Rumble Median = {results[stage_id]['raw_median']:.5f} | Music Median = {results[stage_id]['music_median']:.5f}")

    # Dump full arrays to JSON for review
    json_path = os.path.join(SHARE_DIR, "calibration_raw_data.json")
    try:
        with open(json_path, "w") as f:
            json.dump(calibration_data, f)
        log(f"\n💾 Full arrays saved to {json_path}")
    except Exception as e:
        log(f"⚠️ Failed to save JSON: {e}")

    # --- AUTO-TITRATION MATH ---
    try:
        off_rumble = (results["STAGE_1_OFF"]["raw_median"] + results["STAGE_5_OFF"]["raw_median"]) / 2.0
        on_rumble = (results["STAGE_2_ON_IDLE"]["raw_median"] + results["STAGE_4_LIFTED"]["raw_median"]) / 2.0
        play_rumble = results["STAGE_3_PLAYING"]["raw_median"]
        
        on_music = (results["STAGE_2_ON_IDLE"]["music_median"] + results["STAGE_4_LIFTED"]["music_median"]) / 2.0
        play_music = results["STAGE_3_PLAYING"]["music_median"]

        # Calculate exact midpoints
        raw_motor = off_rumble + ((on_rumble - off_rumble) * 0.6) # Skewed slightly higher to ignore hum fluctuations
        raw_rumble = on_rumble + ((play_rumble - on_rumble) * 0.3) # Skewed closer to IDLE to catch quiet grooves
        raw_music = on_music + ((play_music - on_music) * 0.3)

        # Round to 1 significant figure for cleaner UI input
        def round_1_sig(x):
            return round(x, -int(np.floor(np.log10(abs(x))))) if x != 0 else 0.0
            
        suggested_motor = round_1_sig(raw_motor)
        suggested_rumble = round_1_sig(raw_rumble)
        suggested_music = round_1_sig(raw_music)

        log("\n=========================================")
        log("🎯 RECOMMENDED CONFIGURATION VALUES 🎯")
        log("=========================================")
        log(f"Type these numbers into your options.json / Add-on UI:")
        log(f"")
        log(f"  mic_volume:            {current_vol}")
        log(f"  motor_power_threshold: {suggested_motor}")
        log(f"  rumble_threshold:      {suggested_rumble}")
        log(f"  music_threshold:       {suggested_music}")
        log(f"")
        log("=========================================")
        log("Disable calibration_mode and restart the Add-on to resume normal operation.")
        
    except Exception as e:
        log("Failed to auto-calculate thresholds. Review the JSON file manually.")

    log("💤 Calibration finished. Sleeping to prevent auto-restart...")
    while True:
        time.sleep(3600)

# --- MAIN LOOP ---
def listen_and_identify():
    """Main continuous ALSA loop managing states, debouncing, and MQTT publishing."""
    global app_state, current_attempt, wake_up_time, scrobble_fired, current_track, last_scrobbled_track, paused_track_memory, inp
    try:
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NORMAL, device='default', channels=CHANNELS, rate=RATE, format=FORMAT, periodsize=CHUNK)
    except Exception as e: log(f"🚨 ALSA Error: {e}"); sys.exit(1)

    log(f"Listening (Music Threshold: {MUSIC_THRESHOLD} | Rumble Threshold: {RUMBLE_THRESHOLD})...")
    
    last_pub, last_sleep_log, cooldown_end, chunks, loud_chunks, silence_sleep, song_start = time.time(), 0, 0, 0, 0, 0, 0
    idle_silence_chunks = 0
    target = int(RATE / CHUNK * RECORD_SECONDS)
    buffer = bytearray()
    
    turntable_on = False
    trigger_chunks = 0  
    
    power_score = 0
    power_max_score = int(RATE / CHUNK * 4) 
    
    # Hysteresis Thresholds for Motor Power (Midpoint buffering)
    motor_on_thresh = MOTOR_POWER_THRESHOLD * 1.2
    motor_off_thresh = MOTOR_POWER_THRESHOLD * 0.8
    
    needle_active_score = 0
    needle_max_score = int(RATE / CHUNK * 0.5) 
    needle_down = False

    while True:
        length, data = inp.read()
        if length > 0:
            raw_rms, music_rms = calculate_audio_levels(data)
            now = time.time()
            
            with state_lock:
                current_state = app_state

            # --- TRUE HYSTERESIS POWER DETECTION ---
            if not turntable_on:
                power_score = min(power_score + 1, power_max_score) if raw_rms > motor_on_thresh else max(power_score - 1, 0)
                if power_score >= power_max_score:
                    turntable_on = True
                    mqtt_client.publish("vinyl_guardian/power", "ON", retain=True)
            else:
                power_score = max(power_score - 1, 0) if raw_rms < motor_off_thresh else min(power_score + 1, power_max_score)
                if power_score <= 0:
                    turntable_on = False
                    mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)
                    log("🔌 Turntable turned off. Clearing track display.")
                    mqtt_client.publish("vinyl_guardian/track", "None", retain=True)
                    mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00", retain=True)

            # --- TRUE HYSTERESIS NEEDLE DETECTION ---
            if raw_rms >= RUMBLE_THRESHOLD:
                needle_active_score = min(needle_active_score + 1, needle_max_score)
            else:
                needle_active_score = max(needle_active_score - 1, 0)
            
            needle_down = needle_active_score >= (needle_max_score * 0.5)

            # --- DYNAMIC IDLE STATUS (Handles Runout Groove organically) ---
            if current_state == "IDLE":
                idle_str = "Powered Off"
                if turntable_on:
                    idle_str = "Runout Groove" if needle_down else "Idle (Needle Up)"
                change_status(idle_str)

            # Rate-limited MQTT publishing (1 update per second)
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
                        filled = int(percent * 10) # 10 characters wide
                        bar = '█' * filled + '░' * (10 - filled)
                        prog_str = f"[{bar}] {p_m:02d}:{p_s:02d} / {d_m:02d}:{d_s:02d}"
                    else:
                        prog_str = f"▶️ {p_m:02d}:{p_s:02d} / ??:??"
                        
                    mqtt_client.publish("vinyl_guardian/progress", prog_str)
                elif current_state != "SLEEPING":
                    mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00")
                
                if DEBUG:
                    if current_state == "RECORDING": status = f"🔴 REC {int((chunks/target)*100)}%"
                    elif current_state == "SLEEPING": status = f"💤 SLEEP ({max(0, int(wake_up_time - now))}s)" if now - last_sleep_log >= 15.0 else None
                    elif current_state == "COOLDOWN": status = f"⏳ COOLDOWN ({max(0, int(cooldown_end - now))}s)"
                    elif current_state == "PROCESSING": status = "⚙️ PROC"
                    else: 
                        dbg_str = "Powered Off"
                        if turntable_on: dbg_str = "Runout Groove" if needle_down else "Idle (Needle Up)"
                        status = f"🟢 {dbg_str.upper()}"
                        
                    if status: 
                        print(f"[{time.strftime('%H:%M:%S')}] {status} | Music: {music_rms:.4f} | Rumble: {raw_rms:.4f}", flush=True)
                        if "SLEEP" in status: last_sleep_log = now
                last_pub = now

            if current_state == "IDLE":
                if not needle_down:
                    idle_silence_chunks += 1
                    if idle_silence_chunks == int(RATE / CHUNK * NEEDLE_LIFT_SECONDS):
                        log("🔇 Prolonged silence detected. Clearing track display.")
                        mqtt_client.publish("vinyl_guardian/track", "None", retain=True)
                        mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00", retain=True)
                        idle_silence_chunks = 0 # reset to avoid spam
                else:
                    idle_silence_chunks = 0

                if music_rms > MUSIC_THRESHOLD:
                    trigger_chunks += 1
                    if trigger_chunks >= TRIGGER_DEBOUNCE_CHUNKS:
                        log(f"🎵 AUDIO DETECTED (Music Spike: {music_rms:.4f})")
                        change_status("Recording")
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
                    
                    if is_true_lift:
                        mqtt_client.publish("vinyl_guardian/track", "None", retain=True)
                        mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00", retain=True)

                    with state_lock: 
                        app_state, current_track, current_attempt, consecutive_failures = "IDLE", None, 1, 0
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
                    with state_lock: app_state = "COOLDOWN"
                    
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
