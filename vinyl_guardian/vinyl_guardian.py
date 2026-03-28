import sys
import os
import json
import time
import threading
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
CHANNELS = 1
RATE = 44100
FORMAT = alsaaudio.PCM_FORMAT_S16_LE
CHUNK = 2048
RECORD_SECONDS = 15

# Global State Flags
is_processing = False

def log(message):
    print(f"[Vinyl Guardian] {message}", flush=True)

# --- MQTT SETUP & DISCOVERY ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def connect_mqtt():
    try:
        log(f"Connecting to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}...")
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        log("✅ MQTT Connected!")
        publish_discovery()
    except Exception as e:
        log(f"🚨 MQTT Connection Failed: {e}")

def publish_discovery():
    payload_playing = {
        "name": "Vinyl Now Playing",
        "state_topic": "vinyl_guardian/state",
        "json_attributes_topic": "vinyl_guardian/attributes",
        "icon": "mdi:record-player",
        "unique_id": "vinyl_guardian_now_playing",
        "device": {
            "identifiers": ["vinyl_guardian_01"],
            "name": "Vinyl Guardian",
            "manufacturer": "Custom Add-on"
        }
    }
    mqtt_client.publish("homeassistant/sensor/vinyl_guardian/now_playing/config", json.dumps(payload_playing), retain=True)
    mqtt_client.publish("vinyl_guardian/state", "Idle", retain=True)

    payload_rms = {
        "name": "Vinyl Live RMS",
        "state_topic": "vinyl_guardian/rms",
        "icon": "mdi:waveform",
        "unique_id": "vinyl_guardian_live_rms",
        "device": {
            "identifiers": ["vinyl_guardian_01"],
            "name": "Vinyl Guardian"
        }
    }
    mqtt_client.publish("homeassistant/sensor/vinyl_guardian/live_rms/config", json.dumps(payload_rms), retain=True)
    mqtt_client.publish("vinyl_guardian/rms", "0.0000", retain=True)

def publish_track(title, artist, album):
    log(f"🎶 Publishing to HA: {title} by {artist}")
    mqtt_client.publish("vinyl_guardian/state", f"{title} - {artist}", retain=True)
    attributes = {"title": title, "artist": artist, "album": album, "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    mqtt_client.publish("vinyl_guardian/attributes", json.dumps(attributes), retain=True)

# --- BACKGROUND WORKER THREAD ---
def process_audio_background(audio_data_bytes):
    global is_processing
    log("Analyzing 15-second audio health...")
    
    peak_value = 0
    full_data = np.frombuffer(audio_data_bytes, dtype=np.int16)
    if len(full_data) > 0:
        peak_value = int(np.max(np.abs(full_data.astype(np.int32))))

    if DEBUG:
        print(f"[DEBUG] Buffer Size: {len(audio_data_bytes)} bytes | Max Peak: {peak_value} / 32767", flush=True)
    
    if peak_value >= 32000:
        log("⚠️ WARNING: Audio is CLIPPING. Signal is distorted. AcoustID may fail.")
    elif peak_value < 2000:
        log("⚠️ WARNING: Audio is VERY QUIET. AcoustID may struggle to hear the track.")
    else:
        log("✅ Audio volume is in a healthy range.")

    log("Generating Chromaprint Fingerprint...")
    try:
        duration = len(audio_data_bytes) // (RATE * CHANNELS * 2)
        
        # BUG FIXED HERE: Swapped the arguments into the correct order!
        fingerprint = acoustid.fingerprint(audio_data_bytes, RATE, CHANNELS)
        
        log("Sending fingerprint to AcoustID API...")
        response = acoustid.lookup(API_KEY, fingerprint, duration, meta='recordings releases artists')
        
        if DEBUG or ONE_SHOT:
            log("--- RAW API RESPONSE START ---")
            print(json.dumps(response, indent=2), flush=True)
            log("--- RAW API RESPONSE END ---")
        
        if response.get('status') == 'ok':
            results = response.get('results', [])
            if not results:
                log("❌ API returned 'ok', but found ZERO matches. Unrecognized track or distorted audio.")
                mqtt_client.publish("vinyl_guardian/state", "Unknown Track", retain=True)
            else:
                best_match = results[0]
                score = best_match.get('score', 0)
                
                if score > 0.4:
                    try:
                        recording = best_match['recordings'][0]
                        title = recording.get('title', 'Unknown Title')
                        artist = recording['artists'][0].get('name', 'Unknown') if 'artists' in recording else 'Unknown'
                        album = recording.get('releasegroups', [{}])[0].get('title', 'Unknown Album')
                        
                        log(f"✅ MATCH FOUND! Score: {score}")
                        publish_track(title, artist, album)
                    except (KeyError, IndexError):
                        log("⚠️ Matched audio, but metadata was incomplete.")
                else:
                    log(f"⚠️ Low confidence match (Score: {score}). Ignoring.")
                    mqtt_client.publish("vinyl_guardian/state", "Unknown Track", retain=True)
        else:
            log(f"❌ API Error: {response.get('error', 'Unknown Error')}")

    except Exception as e:
        log(f"🚨 Processing Failed: {e}")
        mqtt_client.publish("vinyl_guardian/state", "Error", retain=True)

    if ONE_SHOT:
        log("🛑 ONE-SHOT COMPLETE. Exiting container so you can read the logs without them scrolling away.")
        os._exit(0) 
    
    log("Cooldown: Waiting 15 seconds to avoid API spam...")
    time.sleep(15)
    mqtt_client.publish("vinyl_guardian/state", "Idle", retain=True)
    is_processing = False 


# --- MAIN AUDIO LOOP ---
def calculate_rms(data):
    try:
        audio_data = np.frombuffer(data, dtype=np.int16)
        if len(audio_data) == 0: return 0
        rms = np.sqrt(np.mean(np.square(audio_data.astype(np.float32))))
        return float(rms) / 32768.0
    except:
        return 0

def listen_and_identify():
    global is_processing
    log("Initializing ALSA Audio Device...")
    try:
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NORMAL, device='default', channels=CHANNELS, rate=RATE, format=FORMAT, periodsize=CHUNK)
    except Exception as e:
        log(f"🚨 Failed to open ALSA device: {e}")
        sys.exit(1)

    log(f"Listening for needle drop... (Threshold: {THRESHOLD})")
    
    last_publish_time = time.time()
    is_recording = False
    recording_chunks = 0
    target_chunks = int(RATE / CHUNK * RECORD_SECONDS)
    audio_buffer = bytearray()

    while True:
        length, data = inp.read()
        if length > 0:
            rms = calculate_rms(data)
            current_time = time.time()
            
            if current_time - last_publish_time >= 1.0:
                formatted_rms = f"{rms:.4f}"
                mqtt_client.publish("vinyl_guardian/rms", formatted_rms) 
                
                if DEBUG: 
                    if is_recording:
                        progress = int((recording_chunks / target_chunks) * 100)
                        print(f"[{time.strftime('%H:%M:%S')}] 🔴 RECORDING ({progress}%) - Live RMS: {formatted_rms}", flush=True)
                    else:
                        print(f"[{time.strftime('%H:%M:%S')}] 🟢 LISTENING - Live RMS: {formatted_rms}", flush=True)
                
                last_publish_time = current_time

            if is_recording:
                audio_buffer.extend(data)
                recording_chunks += 1
                
                if recording_chunks >= target_chunks:
                    log("✅ 15-Second capture complete! Handing off to background processor...")
                    is_recording = False
                    worker = threading.Thread(target=process_audio_background, args=(bytes(audio_buffer),))
                    worker.start()
            
            elif rms > THRESHOLD and not is_processing:
                log(f"🎵 NEEDLE DROP DETECTED! (RMS: {rms:.4f})")
                mqtt_client.publish("vinyl_guardian/state", "Listening...", retain=True)
                
                is_processing = True
                is_recording = True
                recording_chunks = 0
                audio_buffer = bytearray()

if __name__ == "__main__":
    connect_mqtt()
    listen_and_identify()