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

# --- MQTT SETUP ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def connect_mqtt():
    try:
        log(f"Connecting to MQTT...")
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        log(f"🚨 MQTT Failed: {e}")

def publish_track(title, artist, album, score=0):
    log(f"🎶 MATCH FOUND! {title} by {artist}")
    mqtt_client.publish("vinyl_guardian/state", f"{title} - {artist}", retain=True)
    attributes = {
        "title": title, 
        "artist": artist, 
        "album": album, 
        "score": score,
        "source": "AudioTag",
        "last_updated": time.strftime("%H:%M:%S")
    }
    mqtt_client.publish("vinyl_guardian/attributes", json.dumps(attributes), retain=True)

# --- HELPER: GET TRACK DURATION ---
def get_track_duration(title, artist):
    """Fetches exact track duration from iTunes API."""
    try:
        query = urllib.parse.quote(f"{title} {artist}")
        url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=1"
        res = requests.get(url, timeout=10)
        data = res.json()
        if data.get('resultCount', 0) > 0:
            duration_ms = data['results'][0].get('trackTimeMillis', 0)
            return duration_ms / 1000.0
    except Exception as e:
        if DEBUG: print(f"[DEBUG] Failed to fetch track duration: {e}")
    return 0

# --- RECOGNITION ENGINE ---
def recognize_audiotag(wav_path):
    log("Uploading to AudioTag.info API...")
    try:
        url = "https://audiotag.info/api"
        with open(wav_path, 'rb') as audio_file:
            files = {'file': audio_file}
            data = {'action': 'identify', 'apikey': AUDIOTAG_KEY}
            response = requests.post(url, files=files, data=data, timeout=45)
            
        res_json = response.json()
        token = res_json.get('token')
        
        if not token:
            return None

        log(f"Upload complete. Polling for results...")

        for attempt in range(15): 
            time.sleep(3)
            poll_response = requests.post(url, data={'action': 'get_result', 'token': token, 'apikey': AUDIOTAG_KEY}, timeout=15)
            poll_json = poll_response.json()

            status = poll_json.get('result')
            
            if status == 'wait':
                continue 
                
            elif status == 'found' or status == 'done' or poll_json.get('data') or isinstance(status, list):
                data_array = poll_json.get('data', [])
                if not data_array and isinstance(status, list):
                    data_array = status
                if not data_array:
                    return None
                    
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
                
                # AudioTag time format is usually "start - end" (e.g. "0 - 16")
                # We want the 'end' value to know exactly where we currently are in the track
                current_position = RECORD_SECONDS # Fallback
                time_str = str(best.get('time', ''))
                if '-' in time_str:
                    try:
                        current_position = int(time_str.split('-')[1].strip())
                    except: pass
                        
                return {
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "current_position": current_position,
                    "score": 100
                }
                
            elif status in ['not found', 'not_found']:
                return None
            else:
                return None

        return None
    except Exception as e:
        log(f"🚨 Engine Error: {e}")
        return None

# --- BACKGROUND WORKER ---
def process_audio_background(audio_data_bytes, capture_end_timestamp):
    global app_state, current_attempt, wake_up_time
    log(f"🔬 Analyzing {RECORD_SECONDS}s capture (Attempt {current_attempt}/{MAX_ATTEMPTS})...")

    wav_path = "/tmp/process.wav"
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(audio_data_bytes)
    
    try:
        with wave.open("/share/vinyl_debug.wav", "wb") as wf:
            wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(audio_data_bytes)
        log("💾 Saved current capture to /share/vinyl_debug.wav")
    except Exception as e:
        log(f"⚠️ Could not save debug wav: {e}")

    match = recognize_audiotag(wav_path)

    if match:
        publish_track(match['title'], match['artist'], match['album'])
        current_attempt = 1 
        
        log("Fetching total track duration from iTunes...")
        total_duration = get_track_duration(match['title'], match['artist'])
        
        if total_duration > 0:
            # 1. We know the track position at the exact moment the recording ended
            track_position_at_capture_end = match['current_position']
            
            # 2. Calculate the absolute real-world time the song actually started
            song_real_start_time = capture_end_timestamp - track_position_at_capture_end
            
            # 3. Calculate the absolute real-world time the song will end
            predicted_end_time = song_real_start_time + total_duration
            
            # 4. Set the global wake-up time
            wake_up_time = predicted_end_time
            
            if DEBUG:
                current_time = time.time()
                processing_latency = current_time - capture_end_timestamp
                print(f"[DEBUG] Processing took {processing_latency:.1f}s", flush=True)
                print(f"[DEBUG] Track duration: {total_duration}s | API matched position: {track_position_at_capture_end}s", flush=True)
            
            log(f"⏱️ Sleeping until absolute track end timestamp...")
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
        log("🛑 ONE-SHOT COMPLETE.")
        os._exit(0)

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

    log(f"Listening for audio (Threshold: {THRESHOLD})...")
    last_pub = time.time()
    chunks = 0
    target = int(RATE / CHUNK * RECORD_SECONDS)
    buffer = bytearray()

    while True:
        length, data = inp.read()
        if length > 0:
            rms = calculate_rms(data)
            now = time.time()
            
            # 1. Live RMS Reporting
            if now - last_pub >= 1.0:
                mqtt_client.publish("vinyl_guardian/rms", f"{rms:.4f}")
                if DEBUG:
                    if app_state == "RECORDING":
                        status = f"🔴 REC {int((chunks/target)*100)}%"
                    elif app_state == "SLEEPING":
                        remaining = max(0, int(wake_up_time - now))
                        status = f"💤 SLEEP ({remaining}s)"
                    elif app_state == "PROCESSING":
                        status = "⚙️ PROC"
                    else:
                        status = "🟢 IDLE"
                    print(f"[{time.strftime('%H:%M:%S')}] {status} | RMS: {rms:.4f}", flush=True)
                last_pub = now

            # 2. State Machine Logic
            if app_state == "IDLE" and rms > THRESHOLD:
                log(f"🎵 AUDIO DETECTED (RMS: {rms:.4f})")
                mqtt_client.publish("vinyl_guardian/state", "Listening...", retain=True)
                app_state = "RECORDING"
                buffer = bytearray()
                buffer.extend(data)
                chunks = 1

            elif app_state == "RECORDING":
                buffer.extend(data)
                chunks += 1
                if chunks >= target:
                    app_state = "PROCESSING"
                    # Capture the EXACT moment the recording finished
                    capture_end_timestamp = time.time() 
                    threading.Thread(target=process_audio_background, args=(bytes(buffer), capture_end_timestamp)).start()
                    buffer = bytearray()
                    chunks = 0
            
            elif app_state == "SLEEPING":
                if now >= wake_up_time:
                    log("⏰ Absolute track timer finished! Waking up to identify the next track...")
                    app_state = "RECORDING"
                    buffer = bytearray()
                    chunks = 0
                    current_attempt = 1

if __name__ == "__main__":
    if not AUDIOTAG_KEY:
        log("🚨 ERROR: Audiotag API Key is missing in Configuration!")
        sys.exit(1)
        
    connect_mqtt()
    listen_and_identify()