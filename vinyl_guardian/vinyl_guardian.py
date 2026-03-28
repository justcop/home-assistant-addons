import sys
import json
import time
import math
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
RECORD_SECONDS = 15 # Increased to 15s to give AcoustID a better chance

def log(message):
    print(f"[Vinyl Guardian] {message}", flush=True)

def debug_log(message):
    if DEBUG:
        print(f"[DEBUG] {message}", flush=True)

# --- MQTT SETUP & DISCOVERY ---
mqtt_client = mqtt.Client()

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
    discovery_topic = "homeassistant/sensor/vinyl_guardian/now_playing/config"
    payload = {
        "name": "Vinyl Now Playing",
        "state_topic": "vinyl_guardian/state",
        "json_attributes_topic": "vinyl_guardian/attributes",
        "icon": "mdi:record-player",
        "unique_id": "vinyl_guardian_now_playing",
        "device": {
            "identifiers": ["vinyl_guardian_01"],
            "name": "Vinyl Guardian",
            "manufacturer": "Custom Add-on",
            "model": "Audio Fingerprinter"
        }
    }
    mqtt_client.publish(discovery_topic, json.dumps(payload), retain=True)
    mqtt_client.publish("vinyl_guardian/state", "Idle", retain=True)

def publish_track(title, artist, album):
    log(f"🎶 Publishing to HA: {title} by {artist}")
    mqtt_client.publish("vinyl_guardian/state", f"{title} - {artist}", retain=True)
    
    attributes = {
        "title": title,
        "artist": artist,
        "album": album,
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    mqtt_client.publish("vinyl_guardian/attributes", json.dumps(attributes), retain=True)

# --- AUDIO PROCESSING ---
def calculate_rms(data):
    try:
        audio_data = np.frombuffer(data, dtype=np.int16)
        if len(audio_data) == 0:
            return 0
        # Calculate RMS using numpy to avoid audioop deprecation
        rms = np.sqrt(np.mean(np.square(audio_data.astype(np.float32))))
        return float(rms) / 32768.0
    except:
        return 0

def listen_and_identify():
    log("Initializing Audio Device (default)...")
    try:
        inp = alsaaudio.PCM(alsaaudio.PCM_CAPTURE, alsaaudio.PCM_NORMAL, device='default')
        inp.setchannels(CHANNELS)
        inp.setrate(RATE)
        inp.setformat(FORMAT)
        inp.setperiodsize(CHUNK)
    except Exception as e:
        log(f"🚨 Failed to open ALSA device: {e}")
        sys.exit(1)

    log(f"Listening for needle drop... (Threshold: {THRESHOLD})")
    
    while True:
        length, data = inp.read()
        if length > 0:
            rms = calculate_rms(data)
            
            if rms > THRESHOLD:
                log(f"🎵 NEEDLE DROP DETECTED! (RMS: {rms:.4f})")
                log(f"Recording {RECORD_SECONDS} seconds for AcoustID fingerprinting...")
                mqtt_client.publish("vinyl_guardian/state", "Listening...", retain=True)
                
                audio_buffer = b''
                peak_value = 0
                
                # Record the full sample block
                for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
                    l, d = inp.read()
                    if l > 0:
                        audio_buffer += d
                        # Track the absolute loudest peak for clipping detection
                        chunk_data = np.frombuffer(d, dtype=np.int16)
                        if len(chunk_data) > 0:
                            chunk_peak = int(np.max(np.abs(chunk_data.astype(np.int32))))
                            if chunk_peak > peak_value:
                                peak_value = chunk_peak

                log("Recording complete. Analyzing audio health...")
                
                # --- DIAGNOSTIC: AUDIO HEALTH CHECK ---
                debug_log(f"Audio Buffer Size: {len(audio_buffer)} bytes")
                debug_log(f"Maximum Volume Peak: {peak_value} / 32767")
                
                if peak_value >= 32000:
                    log("⚠️ WARNING: Audio is CLIPPING. Signal is distorted. AcoustID may fail.")
                elif peak_value < 2000:
                    log("⚠️ WARNING: Audio is VERY QUIET. AcoustID may struggle to hear the track.")
                else:
                    log("✅ Audio volume is in a healthy range.")

                # --- ACOUSTID FINGERPRINTING ---
                log("Generating Chromaprint Fingerprint...")
                try:
                    duration = len(audio_buffer) // (RATE * CHANNELS * 2)
                    fingerprint = acoustid.fingerprint(RATE, CHANNELS, acoustid.PCM16_16, audio_buffer)
                    debug_log(f"Fingerprint generated successfully.")
                except Exception as e:
                    log(f"🚨 Failed to generate fingerprint: {e}")
                    mqtt_client.publish("vinyl_guardian/state", "Error", retain=True)
                    if ONE_SHOT: sys.exit(1)
                    time.sleep(5)
                    continue

                # --- ACOUSTID API LOOKUP ---
                log("Sending fingerprint to AcoustID API...")
                try:
                    # Get raw response for debugging
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
                            
                            if score > 0.4: # Only publish if reasonably confident
                                try:
                                    recording = best_match['recordings'][0]
                                    title = recording.get('title', 'Unknown Title')
                                    artist = recording['artists'][0].get('name', 'Unknown Artist') if 'artists' in recording else 'Unknown Artist'
                                    
                                    album = "Unknown Album"
                                    if 'releasegroups' in recording:
                                        album = recording['releasegroups'][0].get('title', 'Unknown Album')
                                        
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
                    log(f"🚨 API Request Failed: {e}")

                # --- ONE SHOT LOGIC ---
                if ONE_SHOT:
                    log("🛑 DEBUG_ONE_SHOT is enabled. Exiting container so you can read the logs.")
                    sys.exit(0)
                
                log("Waiting 15 seconds to avoid spamming the API on the same track...")
                time.sleep(15)
                mqtt_client.publish("vinyl_guardian/state", "Idle", retain=True)

if __name__ == "__main__":
    connect_mqtt()
    listen_and_identify()