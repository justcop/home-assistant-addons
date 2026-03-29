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
    log(f"🎶 MATCH FOUND! {title} by {artist} (Score: {score})")
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

        if DEBUG:
            print(f"[DEBUG] AudioTag response: {json.dumps(res_json, indent=2)}", flush=True)

        if res_json.get('status') == 'success' and res_json.get('result'):
            # The API returns a list of results, we take the best match
            best = res_json['result'][0]
            return {
                "title": best.get('title', 'Unknown'),
                "artist": best.get('artist', 'Unknown'),
                "album": best.get('album', 'Unknown'),
                "score": best.get('score', 0)
            }
        return None
    except Exception as e:
        log(f"🚨 AudioTag Engine Error: {e}")
        return None

# --- BACKGROUND WORKER ---
def process_audio_background(audio_data_bytes):
    global is_processing, current_attempt
    log(f"🔬 Analyzing {RECORD_SECONDS}s capture (Attempt {current_attempt}/{MAX_ATTEMPTS})...")
    
    # Trim silence to find start of music
    full_data = np.frombuffer(audio_data_bytes, dtype=np.int16)
    abs_data = np.abs(full_data)
    trigger_point = np.where(abs_data > 800)[0] 
    start_idx = trigger_point[0] if len(trigger_point) > 0 else 0
    trimmed_bytes = full_data[start_idx:].tobytes()

    wav_path = "/tmp/process.wav"
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(trimmed_bytes)
    
    try:
        with open("/share/vinyl_debug.wav", "wb") as f: f.write(trimmed_bytes)
    except: pass

    # Execute recognition
    match = recognize_audiotag(wav_path)

    if match:
        publish_track(match['title'], match['artist'], match['album'], match['score'])
        current_attempt = 1 # Reset on success
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
        inp = alsaaudio.PCM(alsaaudio.PCM_CAPTURE, alsaaudio.PCM_NORMAL, 'default', CHANNELS, RATE, FORMAT, CHUNK)
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