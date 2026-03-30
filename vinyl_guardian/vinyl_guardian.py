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
from shazamio import Shazam
import pylast

# --- LOAD CONFIGURATION ---
try:
    with open('/data/options.json') as f:
        config = json.load(f)
except Exception as e:
    print(f"🚨 Failed to load config: {e}")
    sys.exit(1)

MQTT_BROKER = config.get("mqtt_broker", "core-mosquitto")
MQTT_PORT = config.get("mqtt_port", 1883)
MQTT_USER = config.get("mqtt_user", "")
MQTT_PASS = config.get("mqtt_password", "")

LFM_USER = config.get("lastfm_username", "")
LFM_PASS = config.get("lastfm_password", "")
LFM_KEY = config.get("lastfm_api_key", "")
LFM_SECRET = config.get("lastfm_api_secret", "")

THRESHOLD = config.get("audio_threshold", 0.015)
DEBUG = config.get("debug_logging", True)
ONE_SHOT = config.get("debug_one_shot", False)
RECORD_SECONDS = config.get("recording_seconds", 15)
MAX_ATTEMPTS = config.get("max_attempts", 3)

# Audio Settings
CHANNELS = 2
RATE = 44100
FORMAT = alsaaudio.PCM_FORMAT_S16_LE
CHUNK = 2048

# Global State
app_state = "IDLE" # IDLE, RECORDING, PROCESSING, SLEEPING, COOLDOWN
current_attempt = 1
wake_up_time = 0
consecutive_failures = 0

# Track Data for Scrobbling
current_track = None
scrobble_fired = False

def log(message):
    print(f"[Vinyl Guardian] {message}", flush=True)

# --- LAST.FM SETUP ---
lastfm_network = None
if LFM_USER and LFM_PASS and LFM_KEY and LFM_SECRET:
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
    log("Publishing MQTT Auto-Discovery payloads to Home Assistant...")
    device_info = {
        "identifiers": ["vinyl_guardian_01"],
        "name": "Vinyl Guardian",
        "manufacturer": "Custom Add-on"
    }

    payload_status = {
        "name": "Vinyl Status",
        "state_topic": "vinyl_guardian/status",
        "icon": "mdi:record-player",
        "unique_id": "vinyl_guardian_status",
        "device": device_info
    }
    mqtt_client.publish("homeassistant/sensor/vinyl_guardian/status/config", json.dumps(payload_status), retain=True)

    payload_track = {
        "name": "Vinyl Current Track",
        "state_topic": "vinyl_guardian/track",
        "json_attributes_topic": "vinyl_guardian/attributes",
        "icon": "mdi:music-circle",
        "unique_id": "vinyl_guardian_track",
        "device": device_info
    }
    mqtt_client.publish("homeassistant/sensor/vinyl_guardian/track/config", json.dumps(payload_track), retain=True)

    payload_rms = {
        "name": "Vinyl Live RMS",
        "state_topic": "vinyl_guardian/rms",
        "icon": "mdi:waveform",
        "unique_id": "vinyl_guardian_live_rms",
        "device": device_info
    }
    mqtt_client.publish("homeassistant/sensor/vinyl_guardian/live_rms/config", json.dumps(payload_rms), retain=True)

    payload_scrobble = {
        "name": "Vinyl Last Scrobble",
        "state_topic": "vinyl_guardian/scrobble_state",
        "json_attributes_topic": "vinyl_guardian/scrobble",
        "icon": "mdi:lastpass",
        "unique_id": "vinyl_guardian_last_scrobble",
        "device": device_info
    }
    mqtt_client.publish("homeassistant/sensor/vinyl_guardian/scrobble/config", json.dumps(payload_scrobble), retain=True)

    mqtt_client.publish("vinyl_guardian/status", "Idle", retain=True)
    mqtt_client.publish("vinyl_guardian/track", "None", retain=True)
    mqtt_client.publish("vinyl_guardian/scrobble_state", "None", retain=True)
    mqtt_client.publish("vinyl_guardian/rms", "0.0000", retain=True)

def connect_mqtt():
    try:
        log(f"Connecting to MQTT...")
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        publish_discovery()
    except Exception as e:
        log(f"🚨 MQTT Failed: {e}")

def change_status(new_status):
    mqtt_client.publish("vinyl_guardian/status", new_status, retain=True)

# --- HELPER: GET TRACK DURATION (Fallback) ---
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

# --- RECOGNITION ENGINE (SHAZAM) ---
def recognize_shazam(wav_path):
    log("Uploading to Shazam API...")
    try:
        async def _recognize():
            shazam = Shazam()
            return await shazam.recognize(wav_path)
            
        res_json = asyncio.run(_recognize())
        
        try:
            with open("/share/shazam_last_match.json", "w") as f:
                json.dump(res_json, f, indent=2)
        except Exception: pass

        if 'track' in res_json and 'matches' in res_json and len(res_json['matches']) > 0:
            track = res_json['track']
            title = track.get('title', 'Unknown')
            artist = track.get('subtitle', 'Unknown')
            
            album = "Unknown"
            duration = 0
            release_year = "Unknown"
            
            for section in track.get('sections', []):
                if section.get('type') == 'SONG':
                    for meta in section.get('metadata', []):
                        if meta.get('title') == 'Album':
                            album = meta.get('text')
                        elif meta.get('title') == 'Length':
                            length_str = meta.get('text')
                            if ':' in length_str:
                                parts = length_str.split(':')
                                if len(parts) == 2:
                                    duration = int(parts[0]) * 60 + int(parts[1])
                                elif len(parts) == 3:
                                    duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                        elif meta.get('title') == 'Released':
                            release_year = meta.get('text')
            
            offset_seconds = res_json['matches'][0].get('offset', 0)
            
            return {
                "title": title,
                "artist": artist,
                "album": album,
                "release_year": release_year,
                "offset_seconds": offset_seconds,
                "duration": duration
            }
        return None
    except Exception as e:
        log(f"🚨 Shazam Engine Error: {e}")
        return None

# --- BACKGROUND WORKER ---
def process_audio_background(audio_data_bytes, song_start_timestamp):
    global app_state, current_attempt, wake_up_time, consecutive_failures, current_track, scrobble_fired
    log(f"🔬 Analyzing {RECORD_SECONDS}s capture via Shazam (Attempt {current_attempt}/{MAX_ATTEMPTS})...")

    full_data = np.frombuffer(audio_data_bytes, dtype=np.int16)
    
    peak_value = int(np.max(np.abs(full_data.astype(np.int32)))) if len(full_data) > 0 else 0
    if peak_value >= 32000:
        log(f"⚠️ WARNING: Audio is CLIPPING (Peak: {peak_value}/32767). Turn DOWN mic_volume!")
    elif peak_value < 2000:
        log(f"⚠️ WARNING: Audio is VERY QUIET (Peak: {peak_value}/32767). Turn UP mic_volume!")

    abs_data = np.abs(full_data)
    trigger_point = np.where(abs_data > 1000)[0]
    start_idx = trigger_point[0] if len(trigger_point) > 0 else 0
    
    min_samples_required = RATE * 8 
    if len(full_data) - start_idx < min_samples_required:
        start_idx = max(0, len(full_data) - min_samples_required)

    trimmed_bytes = full_data[start_idx:].tobytes()
    trimmed_seconds = start_idx / RATE
    
    wav_path = "/tmp/process.wav"
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(trimmed_bytes)
    
    try:
        with wave.open("/share/vinyl_debug.wav", "wb") as wf:
            wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(trimmed_bytes)
    except Exception: pass

    match = recognize_shazam(wav_path)

    if match:
        current_attempt = 1 
        consecutive_failures = 0
        
        total_duration = match.get('duration', 0)
        if total_duration <= 0:
            log("Fetching total track duration from iTunes fallback...")
            total_duration = get_track_duration(match['title'], match['artist'])
        
        if total_duration > 0:
            trimmed_audio_start_timestamp = song_start_timestamp + trimmed_seconds
            song_real_start_timestamp = trimmed_audio_start_timestamp - match['offset_seconds']
            wake_up_time = song_real_start_timestamp + total_duration
            
            scrobble_delay = min(total_duration / 2.0, 240)
            scrobble_trigger_time = song_start_timestamp + scrobble_delay

            current_track = {
                "title": match['title'],
                "artist": match['artist'],
                "album": match['album'],
                "duration": total_duration,
                "start_timestamp": int(song_real_start_timestamp),
                "release_year": match['release_year'],
                "scrobble_trigger_time": scrobble_trigger_time,
                "source": "Shazam"
            }
            scrobble_fired = False

            log(f"🎶 MATCH FOUND! {match['title']} by {match['artist']}")
            mqtt_client.publish("vinyl_guardian/track", f"{match['title']} - {match['artist']}", retain=True)
            mqtt_client.publish("vinyl_guardian/attributes", json.dumps(current_track), retain=True)
            
            if DEBUG:
                log(f"⏱️ Track duration: {total_duration}s. Predicted absolute end time: {time.strftime('%H:%M:%S', time.localtime(wake_up_time))}")
            
            app_state = "SLEEPING"
            change_status("Playing")
        else:
            log("⚠️ Could not find track length. Falling back to 60s sleep.")
            wake_up_time = time.time() + 60
            current_track = None
            app_state = "SLEEPING"
            change_status("Playing")
            
    else:
        if current_attempt < MAX_ATTEMPTS:
            log(f"❌ No match. Instantly queueing Attempt {current_attempt + 1}...")
            current_attempt += 1
            app_state = "RECORDING"
            change_status("Recording")
        else:
            consecutive_failures += 1
            current_track = None
            if consecutive_failures >= 10:
                log(f"🚨 {consecutive_failures} consecutive unrecognized tracks! Engaging 30-MINUTE TIMEOUT.")
                change_status("Timeout (30m)")
                mqtt_client.publish("vinyl_guardian/track", "Unknown Track", retain=True)
                current_attempt = 1
                consecutive_failures = 0
                wake_up_time = time.time() + 1800 
                app_state = "SLEEPING"
            else:
                log(f"❌ No match found after {MAX_ATTEMPTS} attempts.")
                change_status("Playing")
                mqtt_client.publish("vinyl_guardian/track", "Unknown Track", retain=True)
                current_attempt = 1
                wake_up_time = time.time() + 60
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
    global app_state, current_attempt, wake_up_time, scrobble_fired, current_track
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

    log(f"Listening for audio (Engine: Shazam, Threshold: {THRESHOLD})...")
    last_pub = time.time()
    last_sleep_log = 0
    cooldown_end = 0
    
    chunks = 0
    loud_chunks = 0
    silence_during_sleep = 0
    target = int(RATE / CHUNK * RECORD_SECONDS)
    
    buffer = bytearray()
    song_start_timestamp = 0

    while True:
        length, data = inp.read()
        if length > 0:
            rms = calculate_rms(data)
            now = time.time()
            
            # --- LOGGING ---
            if now - last_pub >= 1.0:
                mqtt_client.publish("vinyl_guardian/rms", f"{rms:.4f}")
                if DEBUG:
                    if app_state == "RECORDING":
                        status = f"🔴 REC {int((chunks/target)*100)}%"
                        print(f"[{time.strftime('%H:%M:%S')}] {status} | RMS: {rms:.4f}", flush=True)
                    elif app_state == "SLEEPING":
                        if now - last_sleep_log >= 15.0:
                            status = f"💤 SLEEP ({max(0, int(wake_up_time - now))}s remaining)"
                            print(f"[{time.strftime('%H:%M:%S')}] {status} | RMS: {rms:.4f}", flush=True)
                            last_sleep_log = now
                    elif app_state == "COOLDOWN":
                        status = f"⏳ COOLDOWN ({max(0, int(cooldown_end - now))}s)"
                        print(f"[{time.strftime('%H:%M:%S')}] {status} | RMS: {rms:.4f}", flush=True)
                    elif app_state == "PROCESSING":
                        print(f"[{time.strftime('%H:%M:%S')}] ⚙️ PROC | RMS: {rms:.4f}", flush=True)
                last_pub = now

            # --- STATE MACHINE ---
            if app_state == "IDLE" and rms > THRESHOLD:
                log(f"🎵 AUDIO DETECTED (RMS: {rms:.4f})")
                change_status("Recording")
                song_start_timestamp = time.time()
                app_state = "RECORDING"
                buffer = bytearray()
                buffer.extend(data)
                chunks = 1
                loud_chunks = 1
                silence_during_sleep = 0

            elif app_state == "RECORDING":
                buffer.extend(data)
                chunks += 1
                if rms > THRESHOLD:
                    loud_chunks += 1
                    
                if chunks >= target:
                    if loud_chunks >= (target / 2.0):
                        app_state = "PROCESSING"
                        change_status("Processing")
                        threading.Thread(target=process_audio_background, args=(bytes(buffer), song_start_timestamp)).start()
                    else:
                        log(f"⚠️ Discarding sample: Only {loud_chunks}/{target} chunks met threshold. Returning to IDLE.")
                        change_status("Idle")
                        app_state = "IDLE"
                        
                    buffer = bytearray()
                    chunks = 0
                    loud_chunks = 0
            
            elif app_state == "SLEEPING":
                if rms < (THRESHOLD * 0.5):
                    silence_during_sleep += 1
                else:
                    silence_during_sleep = 0

                if silence_during_sleep >= int(RATE / CHUNK * 25):
                    log("🔇 Needle lift detected (25s of absolute silence). Aborting track!")
                    change_status("Idle")
                    mqtt_client.publish("vinyl_guardian/track", "None", retain=True)
                    app_state = "IDLE"
                    current_track = None
                    silence_during_sleep = 0
                    continue

                if current_track and not scrobble_fired:
                    if now >= current_track.get('scrobble_trigger_time', 0):
                        log(f"✅ Scrobble rules met. Emitting MQTT scrobble payload...")
                        mqtt_client.publish("vinyl_guardian/scrobble_state", f"{current_track['title']} - {current_track['artist']}", retain=True)
                        mqtt_client.publish("vinyl_guardian/scrobble", json.dumps(current_track), retain=True)
                        
                        # NATIVE SCROBBLE TO LAST.FM
                        scrobble_to_lastfm(
                            artist=current_track['artist'],
                            title=current_track['title'],
                            start_timestamp=current_track['start_timestamp'],
                            album=current_track['album']
                        )
                        
                        scrobble_fired = True

                if now >= wake_up_time:
                    log("⏰ Sleep timer finished! Entering 4-second cooldown before listening...")
                    app_state = "COOLDOWN"
                    change_status("Cooldown")
                    cooldown_end = now + 4
                    
            elif app_state == "COOLDOWN":
                if now >= cooldown_end:
                    log("🟢 Cooldown complete. Returning to IDLE to wait for next threshold trigger.")
                    change_status("Idle")
                    mqtt_client.publish("vinyl_guardian/track", "None", retain=True)
                    app_state = "IDLE"

if __name__ == "__main__":
    connect_mqtt()
    listen_and_identify()