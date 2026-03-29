import sys
import os
import json
import time
import threading
import wave
import requests
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

# Global State Flags
is_processing = False
current_attempt = 1

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

# --- RECOGNITION ENGINE ---
def recognize_audiotag(wav_path):
    log("Uploading to AudioTag.info API...")
    try:
        url = "https://audiotag.info/api"
        with open(wav_path, 'rb') as audio_file:
            files = {'file': audio_file}
            data = {'action': 'identify', 'apikey': AUDIOTAG_KEY}
            response = requests.post(url, files=files, data=data, timeout=45)
            
        try:
            res_json = response.json()
        except ValueError:
            log(f"🚨 Invalid JSON from AudioTag: {response.text[:200]}")
            return None

        if not res_json.get('success'):
            log(f"🚨 AudioTag Error: {json.dumps(res_json)}")
            return None

        token = res_json.get('token')
        if not token:
            log("🚨 AudioTag did not return a job token.")
            return None

        log(f"Upload complete! Job Queued (Token: {token}). Polling for results...")

        # POLLING LOOP: Ask the server for the result every 3 seconds
        for attempt in range(15): # Max 45 seconds of waiting
            time.sleep(3)
            poll_data = {'action': 'get_result', 'token': token, 'apikey': AUDIOTAG_KEY}
            poll_response = requests.post(url, data=poll_data, timeout=15)
            poll_json = poll_response.json()

            if DEBUG:
                print(f"[DEBUG] Poll {attempt+1} response: {json.dumps(poll_json)}", flush=True)

            status = poll_json.get('job_status')
            
            if status == 'wait':
                continue # Still processing, loop again
                
            elif status == 'done':
                data_array = poll_json.get('data', [])
                if not data_array:
                    return None
                    
                best = data_array[0]
                
                # Defensively parse the AudioTag results
                title = "Unknown"
                artist = "Unknown"
                album = "Unknown"
                
                tracks = best.get('tracks', [])
                if tracks:
                    track_info = tracks[0]
                    # Sometimes AudioTag returns lists, sometimes dicts
                    if isinstance(track_info, list) and len(track_info) >= 3:
                        title = str(track_info[0])
                        artist = str(track_info[1])
                        album = str(track_info[2])
                    elif isinstance(track_info, dict):
                        title = track_info.get('title', track_info.get('track', 'Unknown'))
                        artist = track_info.get('artist', 'Unknown')
                        album = track_info.get('album', 'Unknown')
                        
                return {
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "score": 100
                }
                
            elif status == 'not_found':
                return None
            else:
                log(f"🚨 Unexpected AudioTag status: {status}")
                return None

        log("🚨 AudioTag polling timed out.")
        return None
        
    except Exception as e:
        log(f"🚨 AudioTag Engine Error: {e}")
        return None

# --- BACKGROUND WORKER ---
def process_audio_background(audio_data_bytes):
    global is_processing, current_attempt
    log(f"🔬 Analyzing {RECORD_SECONDS}s capture (Attempt {current_attempt}/{MAX_ATTEMPTS})...")
    
    full_data = np.frombuffer(audio_data_bytes, dtype=np.int16)
    abs_data = np.abs(full_data)
    trigger_point = np.where(abs_data > 800)[0] 
    start_idx = trigger_point[0] if len(trigger_point) > 0 else 0
    trimmed_bytes = full_data[start_idx:].tobytes()

    wav_path = "/tmp/process.wav"
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(trimmed_bytes)
    
    # FIXED: Properly save the WAV file to the Share folder so it can be played!
    try:
        with wave.open("/share/vinyl_debug.wav", "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(RATE)
            wf.writeframes(trimmed_bytes)
    except Exception as e: 
        log(f"⚠️ Failed to save debug wav: {e}")

    # Execute recognition
    match = recognize_audiotag(wav_path)

    if match:
        publish_track(match['title'], match['artist'], match['album'])
        current_attempt = 1 
        log("Cooldown: Waiting 15 seconds to avoid API spam...")
        time.sleep(15)
    else:
        if current_attempt < MAX_ATTEMPTS:
            log(f"❌ No match. Instantly queueing Attempt {current_attempt + 1}...")
            current_attempt += 1
        else:
            log(f"❌ No match found after {MAX_ATTEMPTS} attempts.")
            mqtt_client.publish("vinyl_guardian/state", "Unknown Track", retain=True)
            current_attempt = 1
            log("Cooldown: Waiting 15 seconds...")
            time.sleep(15)

    if os.path.exists(wav_path): os.remove(wav_path)
    
    if ONE_SHOT:
        log("🛑 ONE-SHOT COMPLETE.")
        os._exit(0)

    mqtt_client.publish("vinyl_guardian/state", "Idle", retain=True)
    is_processing = False

# --- MAIN LOOP ---
def calculate_rms(data):
    try:
        audio_data = np.frombuffer(data, dtype=np.int16)
        return float(np.sqrt(np.mean(np.square(audio_data.astype(np.float32))))) / 32768.0
    except: return 0

def listen_and_identify():
    global is_processing
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
    is_recording = False
    chunks = 0
    target = int(RATE / CHUNK * RECORD_SECONDS)
    buffer = bytearray()

    while True:
        length, data = inp.read()
        if length > 0:
            rms = calculate_rms(data)
            now = time.time()
            if now - last_pub >= 1.0:
                mqtt_client.publish("vinyl_guardian/rms", f"{rms:.4f}")
                if DEBUG:
                    status = f"🔴 REC {int((chunks/target)*100)}%" if is_recording else "🟢 LIVE"
                    print(f"[{time.strftime('%H:%M:%S')}] {status} | RMS: {rms:.4f}", flush=True)
                last_pub = now

            if is_recording:
                buffer.extend(data)
                chunks += 1
                if chunks >= target:
                    is_recording = False
                    threading.Thread(target=process_audio_background, args=(bytes(buffer),)).start()
            elif rms > THRESHOLD and not is_processing:
                log(f"🎵 AUDIO DETECTED (RMS: {rms:.4f})")
                mqtt_client.publish("vinyl_guardian/state", "Listening...", retain=True)
                is_processing = True; is_recording = True; chunks = 0; buffer = bytearray()

if __name__ == "__main__":
    if not AUDIOTAG_KEY:
        log("🚨 ERROR: Audiotag API Key is missing in Configuration!")
        sys.exit(1)
        
    connect_mqtt()
    listen_and_identify()