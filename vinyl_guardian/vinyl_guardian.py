import sys
import os
import json
import time
import threading
import wave
import subprocess
import numpy as np
import alsaaudio
import acoustid
import paho.mqtt.client as mqtt

# --- LOAD CONFIGURATION ---
try:
    with open('/data/options.json') as f:
        config = json.load(f)
except Exception as e:
    print(f"🚨 Failed to load config: {e}")
    sys.exit(1)

API_KEY = config.get("acoustid_key")
MQTT_BROKER = config.get("mqtt_broker", "core-mosquitto")
MQTT_PORT = config.get("mqtt_port", 1883)
MQTT_USER = config.get("mqtt_user", "")
MQTT_PASS = config.get("mqtt_password", "")
THRESHOLD = config.get("audio_threshold", 0.015)
DEBUG = config.get("debug_logging", True)
ONE_SHOT = config.get("debug_one_shot", False)

# Audio Settings
CHANNELS = 2
RATE = 44100
FORMAT = alsaaudio.PCM_FORMAT_S16_LE
CHUNK = 2048
RECORD_SECONDS = 20 # Increased slightly to give the algorithm more data

# Global State Flags
is_processing = False

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

def publish_track(title, artist, album):
    log(f"🎶 MATCH FOUND! {title} by {artist}")
    mqtt_client.publish("vinyl_guardian/state", f"{title} - {artist}", retain=True)
    attributes = {
        "title": title, 
        "artist": artist, 
        "album": album, 
        "last_updated": time.strftime("%H:%M:%S"),
        "source": "AcoustID"
    }
    mqtt_client.publish("vinyl_guardian/attributes", json.dumps(attributes), retain=True)

# --- BACKGROUND WORKER ---
def process_audio_background(audio_data_bytes):
    global is_processing
    log("🔬 Analyzing capture...")
    
    # 1. Trim leading silence (AcoustID is sensitive to leading 'dead air')
    full_data = np.frombuffer(audio_data_bytes, dtype=np.int16)
    # Find the first index where volume crosses a tiny threshold
    abs_data = np.abs(full_data)
    trigger_point = np.where(abs_data > 500)[0]
    
    start_idx = trigger_point[0] if len(trigger_point) > 0 else 0
    # Ensure we don't trim too much, just enough to start on a beat
    trimmed_data = full_data[start_idx:]
    trimmed_bytes = trimmed_data.tobytes()

    # 2. Save for local inspection
    debug_wav = "/share/vinyl_debug.wav"
    try:
        with wave.open(debug_wav, "wb") as wf:
            wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(trimmed_bytes)
    except: pass

    # 3. Generate Fingerprint using fpcalc (The most reliable method)
    wav_path = "/tmp/process.wav"
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(trimmed_bytes)
    
    try:
        log("Generating Fingerprint...")
        result = subprocess.run(['fpcalc', '-json', wav_path], stdout=subprocess.PIPE, text=True)
        fp_json = json.loads(result.stdout)
        fp = fp_json.get('fingerprint')
        dur = fp_json.get('duration')

        if not fp:
            log("🚨 fpcalc returned no fingerprint.")
            is_processing = False
            return

        if DEBUG:
            print(f"[DEBUG] FP Length: {len(fp)} | Duration: {dur}s", flush=True)

        log("Sending to AcoustID API...")
        # Requesting max metadata for better matching
        response = acoustid.lookup(API_KEY, fp, dur, meta='recordings releases artists releasegroups tracks compress')
        
        if DEBUG or ONE_SHOT:
            log("--- API RESPONSE ---")
            print(json.dumps(response, indent=2), flush=True)

        if response.get('status') == 'ok' and response.get('results'):
            # Filter results by score
            results = [r for r in response['results'] if r.get('score', 0) > 0.4]
            if results:
                # Sort by score descending
                results.sort(key=lambda x: x.get('score', 0), reverse=True)
                best = results[0]
                
                # Extract metadata carefully
                rec = best.get('recordings', [{}])[0]
                title = rec.get('title', 'Unknown Title')
                artist = "Unknown Artist"
                if rec.get('artists'):
                    artist = rec['artists'][0].get('name', 'Unknown Artist')
                album = "Unknown Album"
                if rec.get('releasegroups'):
                    album = rec['releasegroups'][0].get('title', 'Unknown Album')
                
                publish_track(title, artist, album)
            else:
                log("❌ Results found, but scores too low (below 0.4).")
                mqtt_client.publish("vinyl_guardian/state", "Unknown Track", retain=True)
        else:
            log(f"❌ No match found in AcoustID database (Status: {response.get('status')})")
            mqtt_client.publish("vinyl_guardian/state", "Unknown Track", retain=True)

    except Exception as e:
        log(f"🚨 API Processing Error: {e}")

    if os.path.exists(wav_path): os.remove(wav_path)

    if ONE_SHOT:
        log("🛑 ONE-SHOT COMPLETE.")
        os._exit(0)

    time.sleep(15)
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
        log(f"🚨 ALSA Failed: {e}"); sys.exit(1)

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
                log(f"🎵 NEEDLE DROP! (RMS: {rms:.4f})")
                is_processing = True; is_recording = True; chunks = 0; buffer = bytearray()

if __name__ == "__main__":
    connect_mqtt()
    listen_and_identify()