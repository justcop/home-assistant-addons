import sys
import json
import time
import math
import audioop
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
THRESHOLD = config.get("audio_threshold", 0.015)
DEBUG = config.get("debug_logging", True)
ONE_SHOT = config.get("debug_one_shot", False)

# Audio Settings
CHANNELS = 1
RATE = 44100
FORMAT = alsaaudio.PCM_FORMAT_S16_LE
CHUNK = 2048
RECORD_SECONDS = 15 # AcoustID needs at least 12-15 seconds for a reliable match

def log(message):
    print(f"[Vinyl Guardian] {message}", flush=True)

def debug_log(message):
    if DEBUG:
        print(f"[DEBUG] {message}", flush=True)

def calculate_rms(data):
    try:
        rms = audioop.rms(data, 2)
        return rms / 32768.0
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
                
                audio_buffer = b''
                peak_value = 0
                
                # Record the full sample block
                for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
                    l, d = inp.read()
                    if l > 0:
                        audio_buffer += d
                        # Check the loudest peak in this chunk
                        chunk_peak = audioop.max(d, 2)
                        if chunk_peak > peak_value:
                            peak_value = chunk_peak

                log("Recording complete. Analyzing audio health...")
                
                # --- AUDIO HEALTH CHECK ---
                debug_log(f"Audio Buffer Size: {len(audio_buffer)} bytes")
                debug_log(f"Maximum Volume Peak: {peak_value} / 32767")
                
                if peak_value >= 32000:
                    log("⚠️ WARNING: Audio is CLIPPING. The signal is too loud and distorted. AcoustID may fail.")
                elif peak_value < 2000:
                    log("⚠️ WARNING: Audio is VERY QUIET. AcoustID may struggle to hear the track.")
                else:
                    log("✅ Audio volume is in a healthy range.")

                # --- ACOUSTID FINGERPRINTING ---
                log("Generating Chromaprint Fingerprint...")
                try:
                    duration = len(audio_buffer) // (RATE * CHANNELS * 2)
                    fingerprint = acoustid.fingerprint(RATE, CHANNELS, acoustid.PCM16_16, audio_buffer)
                    debug_log(f"Fingerprint generated successfully. Length: {len(fingerprint)}")
                except Exception as e:
                    log(f"🚨 Failed to generate fingerprint: {e}")
                    if ONE_SHOT: sys.exit(1)
                    continue

                # --- ACOUSTID API LOOKUP ---
                log("Sending fingerprint to AcoustID API...")
                try:
                    # We use the raw lookup function to get the exact JSON payload back
                    response = acoustid.lookup(API_KEY, fingerprint, duration, meta='recordings releases artists')
                    
                    log("--- RAW API RESPONSE START ---")
                    print(json.dumps(response, indent=2), flush=True)
                    log("--- RAW API RESPONSE END ---")
                    
                    if response.get('status') == 'ok':
                        results = response.get('results', [])
                        if not results:
                            log("❌ API returned 'ok', but found ZERO matches. The audio is likely too distorted, too short, or an unrecognized pressing.")
                        else:
                            best_match = results[0]
                            log(f"✅ MATCH FOUND! Score: {best_match.get('score', 0)}")
                    else:
                        log(f"❌ API Error: {response.get('error', 'Unknown Error')}")

                except Exception as e:
                    log(f"🚨 API Request Failed: {e}")

                # --- ONE SHOT LOGIC ---
                if ONE_SHOT:
                    log("🛑 DEBUG_ONE_SHOT is enabled. Exiting container to allow log review.")
                    sys.exit(0)
                
                log("Waiting 10 seconds before resuming listening...")
                time.sleep(10)

if __name__ == "__main__":
    listen_and_identify()