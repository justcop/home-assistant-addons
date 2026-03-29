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

# --- LOAD CONFIGURATION ---
try:
    with open('/data/options.json') as f:
        config = json.load(f)
except Exception as e:
    print(f"🚨 Failed to load config: {e}")
    sys.exit(1)

ENGINE = config.get("recognition_engine", "audd").lower()
AUDD_KEY = config.get("audd_key", "")
AUDIOTAG_KEY = config.get("audiotag_key", "")

MQTT_BROKER = config.get("mqtt_broker", "core-mosquitto")
MQTT_PORT = config.get("mqtt_port", 1883)
MQTT_USER = config.get("mqtt_user", "")
MQTT_PASS = config.get("mqtt_password", "")

THRESHOLD = config.get("audio_threshold", 0.015)
DEBUG = config.get("debug_logging", True)
ONE_SHOT = config.get("debug_one_shot", False)
RECORD_SECONDS = config.get("recording_seconds", 20)
MAX_ATTEMPTS = config.get("max_attempts", 3)

# Audio Settings
CHANNELS = 2
RATE = 44100
FORMAT = alsaaudio.PCM_FORMAT_S16_LE
CHUNK = 2048

# Global State
app_state = "IDLE" # IDLE, RECORDING, PROCESSING, SLEEPING
current_attempt = 1
wake_up_time = 0

def log(message):
    print(f"[Vinyl Guardian] {message}", flush=True)

# --- MQTT SETUP & DISCOVERY ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def publish_discovery():
    log("Publishing MQTT Auto-Discovery payloads to Home Assistant...")
    device_info = {
        "identifiers": ["vinyl_guardian_01"],
        "name": "Vinyl Guardian",
        "manufacturer": "Custom Add-on"
    }

    payload_playing = {
        "name": "Vinyl Now Playing",
        "state_topic": "vinyl_guardian/state",
        "json_attributes_topic": "vinyl_guardian/attributes",
        "icon": "mdi:record-player",
        "unique_id": "vinyl_guardian_now_playing",
        "device": device_info
    }
    mqtt_client.publish("homeassistant/sensor/vinyl_guardian/now_playing/config", json.dumps(payload_playing), retain=True)
    mqtt_client.publish("vinyl_guardian/state", "Idle", retain=True)

    payload_rms = {
        "name": "Vinyl Live RMS",
        "state_topic": "vinyl_guardian/rms",
        "icon": "mdi:waveform",
        "unique_id": "vinyl_guardian_live_rms",
        "device": device_info
    }
    mqtt_client.publish("homeassistant/sensor/vinyl_guardian/live_rms/config", json.dumps(payload_rms), retain=True)
    mqtt_client.publish("vinyl_guardian/rms", "0.0000", retain=True)

def connect_mqtt():
    try:
        log(f"Connecting to MQTT...")
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        publish_discovery()
    except Exception as e:
        log(f"🚨 MQTT Failed: {e}")

def publish_track(title, artist, album, engine="AudD"):
    log(f"🎶 MATCH FOUND! {title} by {artist} (via {engine})")
    mqtt_client.publish("vinyl_guardian/state", f"{title} - {artist}", retain=True)
    attributes = {
        "title": title, 
        "artist": artist, 
        "album": album, 
        "source": engine,
        "last_updated": time.strftime("%H:%M:%S")
    }
    mqtt_client.publish("vinyl_guardian/attributes", json.dumps(attributes), retain=True)

# --- HELPER: GET TRACK DURATION ---
def get_track_duration(title, artist):
    try:
        query = urllib.parse.quote(f"{title} {artist}")
        url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=1"
        res = requests.get(url, timeout=10)
        data = res.json()
        if data.get('resultCount', 0) > 0:
            return data['results'][0].get('trackTimeMillis', 0) / 1000.0
    except Exception as e:
        if DEBUG: print(f"[DEBUG] Failed to fetch track duration: {e}")
    return 0

# --- RECOGNITION ENGINES ---
def recognize_audd(wav_path):
    log("Uploading to AudD.io API...")
    try:
        url = "https://api.audd.io/"
        data = {
            'api_token': AUDD_KEY,
            'return': 'timecode,apple_music'
        }
        with open(wav_path, 'rb') as audio_file:
            files = {'file': audio_file}
            response = requests.post(url, data=data, files=files, timeout=30)
            
        res_json = response.json()
        
        # Save raw output for debugging
        try:
            with open("/share/audd_last_match.json", "w") as f:
                json.dump(res_json, f, indent=2)
        except Exception: pass
        
        if res_json.get('status') == 'success' and res_json.get('result'):
            result = res_json['result']
            
            # Parse MM:SS timecode into total seconds
            timecode_str = result.get('timecode', '00:00')
            offset_seconds = 0
            if ':' in timecode_str:
                parts = timecode_str.split(':')
                if len(parts) == 2:
                    offset_seconds = (int(parts[0]) * 60) + int(parts[1])
            elif timecode_str.isdigit():
                offset_seconds = int(timecode_str)

            # Extract duration directly from Apple Music data if AudD found it
            duration = 0
            if 'apple_music' in result and 'durationInMillis' in result['apple_music']:
                duration = result['apple_music']['durationInMillis'] / 1000.0
                
            return {
                "title": result.get('title', 'Unknown'),
                "artist": result.get('artist', 'Unknown'),
                "album": result.get('album', 'Unknown'),
                "offset_seconds": offset_seconds,
                "duration": duration
            }
        return None
    except Exception as e:
        log(f"🚨 AudD Engine Error: {e}")
        return None

def recognize_audiotag(wav_path):
    log("Uploading to AudioTag.info API...")
    try:
        url = "https://audiotag.info/api"
        with open(wav_path, 'rb') as audio_file:
            response = requests.post(url, files={'file': audio_file}, data={'action': 'identify', 'apikey': AUDIOTAG_KEY}, timeout=45)
            
        res_json = response.json()
        token = res_json.get('token')
        if not token: return None

        log("Upload complete. Polling for results...")
        for attempt in range(15): 
            time.sleep(3)
            poll_response = requests.post(url, data={'action': 'get_result', 'token': token, 'apikey': AUDIOTAG_KEY}, timeout=15)
            poll_json = poll_response.json()
            status = poll_json.get('result')
            
            if status == 'wait':
                continue 
            elif status in ['found', 'done'] or poll_json.get('data') or isinstance(status, list):
                try:
                    with open("/share/audiotag_last_match.json", "w") as f:
                        json.dump(poll_json, f, indent=2)
                except Exception: pass

                data_array = poll_json.get('data', [])
                if not data_array and isinstance(status, list): data_array = status
                if not data_array: return None
                    
                best = data_array[0]
                title, artist, album = "Unknown", "Unknown", "Unknown"
                
                tracks = best.get('tracks', [])
                if tracks:
                    track_info = tracks[0]
                    if isinstance(track_info, list) and len(track_info) >= 3:
                        title, artist, album = str(track_info[0]), str(track_info[1]), str(track_info[2])
                    elif isinstance(track_info, dict):
                        title = track_info.get('title', track_info.get('track', 'Unknown'))
                        artist = track_info.get('artist', 'Unknown')
                        album = track_info.get('album', 'Unknown')
                
                return {
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "offset_seconds": 0, # AudioTag doesn't provide real offset
                    "duration": 0
                }
            elif status in ['not found', 'not_found']:
                return None
            else:
                return None
        return None
    except Exception as e:
        log(f"🚨 AudioTag Engine Error: {e}")
        return None

# --- BACKGROUND WORKER ---
def process_audio_background(audio_data_bytes, capture_end_timestamp, song_start_timestamp):
    global app_state, current_attempt, wake_up_time
    log(f"🔬 Analyzing {RECORD_SECONDS}s capture via {ENGINE.upper()} (Attempt {current_attempt}/{MAX_ATTEMPTS})...")

    # --- AUDIO HEALTH CHECK ---
    full_data = np.frombuffer(audio_data_bytes, dtype=np.int16)
    peak_value = int(np.max(np.abs(full_data.astype(np.int32)))) if len(full_data) > 0 else 0
    
    if peak_value >= 32000:
        log(f"⚠️ WARNING: Audio is CLIPPING (Peak: {peak_value}/32767). Turn DOWN mic_volume!")
    elif peak_value < 2000:
        log(f"⚠️ WARNING: Audio is VERY QUIET (Peak: {peak_value}/32767). Turn UP mic_volume!")
    else:
        log(f"✅ Audio Health: Good (Peak: {peak_value}/32767).")

    wav_path = "/tmp/process.wav"
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(audio_data_bytes)
    
    try:
        with wave.open("/share/vinyl_debug.wav", "wb") as wf:
            wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(audio_data_bytes)
    except Exception: pass

    # Run chosen engine
    match = recognize_audd(wav_path) if ENGINE == "audd" else recognize_audiotag(wav_path)

    if match:
        publish_track(match['title'], match['artist'], match['album'], ENGINE.upper())
        current_attempt = 1 
        
        # Calculate Total Duration
        total_duration = match.get('duration', 0)
        if total_duration <= 0:
            log("Fetching total track duration from iTunes fallback...")
            total_duration = get_track_duration(match['title'], match['artist'])
        
        if total_duration > 0:
            if ENGINE == "audd":
                # AudD offset math (calculating actual start of the song in the real world)
                recording_start_timestamp = capture_end_timestamp - RECORD_SECONDS
                song_real_start_timestamp = recording_start_timestamp - match['offset_seconds']
                wake_up_time = song_real_start_timestamp + total_duration
                
                if DEBUG:
                    log(f"⏱️ Snippet corresponds to {match['offset_seconds']}s into the track.")
            else:
                # AudioTag fallback math (assumes the needle drop was the start of the song)
                wake_up_time = song_start_timestamp + total_duration
            
            if DEBUG:
                log(f"⏱️ Track duration: {total_duration}s. Predicted absolute end time: {time.strftime('%H:%M:%S', time.localtime(wake_up_time))}")
            
            app_state = "SLEEPING"
        else:
            log("⚠️ Could not find track length. Falling back to 60s sleep.")
            wake_up_time = time.time() + 60
            app_state = "SLEEPING"
            
    else:
        if current_attempt < MAX_ATTEMPTS:
            log(f"❌ No match. Instantly queueing Attempt {current_attempt + 1}...")
            current_attempt += 1
            app_state = "RECORDING" 
        else:
            log(f"❌ No match found after {MAX_ATTEMPTS} attempts.")
            mqtt_client.publish("vinyl_guardian/state", "Unknown Track", retain=True)
            current_attempt = 1
            log("🎧 Assuming Unknown Track is playing. Falling back to 3-minute sleep.")
            wake_up_time = time.time() + 180
            app_state = "SLEEPING"

    if os.path.exists(wav_path): os.remove(wav_path)
    if ONE_SHOT:
        log("🛑 ONE-SHOT COMPLETE."); os._exit(0)

# --- MAIN LOOP ---
def calculate_rms(data):
    try:
        audio_data = np.frombuffer(data, dtype=np.int16)
        return float(np.sqrt(np.mean(np.square(audio_data.astype(np.float32))))) / 32768.0
    except: return 0

def listen_and_identify():
    global app_state, current_attempt, wake_up_time
    try:
        inp = alsaaudio.PCM(
            type=alsaaudio.PCM_CAPTURE,
            mode=alsaaudio.PCM_NORMAL,
            device='default',
            channels=CHANNELS,
            rate=RATE,
            format=FORMAT,
            periodsize=CHUNK
        )
    except Exception as e:
        log(f"🚨 ALSA initialization failed: {e}"); sys.exit(1)

    log(f"Listening for audio (Engine: {ENGINE.upper()}, Threshold: {THRESHOLD})...")
    last_pub = time.time()
    chunks = 0
    target = int(RATE / CHUNK * RECORD_SECONDS)
    buffer = bytearray()
    song_start_timestamp = 0

    while True:
        length, data = inp.read()
        if length > 0:
            rms = calculate_rms(data)
            now = time.time()
            
            if now - last_pub >= 1.0:
                mqtt_client.publish("vinyl_guardian/rms", f"{rms:.4f}")
                if DEBUG:
                    if app_state == "RECORDING":
                        status = f"🔴 REC {int((chunks/target)*100)}%"
                    elif app_state == "SLEEPING":
                        status = f"💤 SLEEP ({max(0, int(wake_up_time - now))}s)"
                    elif app_state == "PROCESSING":
                        status = "⚙️ PROC"
                    else:
                        status = "🟢 IDLE"
                    print(f"[{time.strftime('%H:%M:%S')}] {status} | RMS: {rms:.4f}", flush=True)
                last_pub = now

            if app_state == "IDLE" and rms > THRESHOLD:
                log(f"🎵 AUDIO DETECTED (RMS: {rms:.4f})")
                mqtt_client.publish("vinyl_guardian/state", "Listening...", retain=True)
                song_start_timestamp = time.time()
                app_state = "RECORDING"
                buffer = bytearray()
                buffer.extend(data)
                chunks = 1

            elif app_state == "RECORDING":
                buffer.extend(data)
                chunks += 1
                if chunks >= target:
                    app_state = "PROCESSING"
                    capture_end_timestamp = time.time() 
                    threading.Thread(target=process_audio_background, args=(bytes(buffer), capture_end_timestamp, song_start_timestamp)).start()
                    buffer = bytearray()
                    chunks = 0
            
            elif app_state == "SLEEPING":
                if now >= wake_up_time:
                    log("⏰ Absolute track timer finished! Waking up to identify the next track...")
                    song_start_timestamp = time.time()
                    app_state = "RECORDING"
                    buffer = bytearray()
                    chunks = 0
                    current_attempt = 1

if __name__ == "__main__":
    if ENGINE == "audd" and not AUDD_KEY:
        log("🚨 ERROR: AudD API Key is missing in Configuration!")
        sys.exit(1)
    elif ENGINE == "audiotag" and not AUDIOTAG_KEY:
        log("🚨 ERROR: Audiotag API Key is missing in Configuration!")
        sys.exit(1)
        
    connect_mqtt()
    listen_and_identify()