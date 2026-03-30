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

MQTT_BROKER = config.get("mqtt_broker", "core-mosquitto")
MQTT_PORT = config.get("mqtt_port", 1883)
MQTT_USER = config.get("mqtt_user", "")
MQTT_PASS = config.get("mqtt_password", "")

LFM_USER = config.get("lastfm_username", "")
LFM_PASS = config.get("lastfm_password", "")
LFM_KEY = config.get("lastfm_api_key", "")
LFM_SECRET = config.get("lastfm_api_secret", "")

MUSIC_THRESHOLD = config.get("music_threshold", 0.005)
RUMBLE_THRESHOLD = config.get("rumble_threshold", 0.015)
DEBUG = config.get("debug_logging", True)
TEST_CAPTURE_MODE = config.get("test_capture_mode", False)
RECORD_SECONDS = config.get("recording_seconds", 10)
MAX_ATTEMPTS = config.get("max_attempts", 3)

# Engine Tuning Parameters
MIN_AUDIO_SECONDS = 5
NEEDLE_LIFT_SECONDS = 25
MOTOR_POWER_THRESHOLD = 0.0045 

# Audio Settings
CHANNELS = 2
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

def log(message):
    print(f"[Vinyl Guardian] {message}", flush=True)

# --- GRACEFUL SHUTDOWN ---
def signal_handler(sig, frame):
    log("🛑 Shutting down gracefully...")
    try:
        if inp: inp.close()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    except Exception: pass
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

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
    log("Publishing MQTT Auto-Discovery payloads...")
    device_info = {"identifiers": ["vinyl_guardian_01"], "name": "Vinyl Guardian", "manufacturer": "Custom Add-on"}

    configs = {
        "status": {"name": "Vinyl Status", "topic": "status", "icon": "mdi:record-player", "domain": "sensor"},
        "track": {"name": "Vinyl Current Track", "topic": "track", "icon": "mdi:music-circle", "attr": True, "domain": "sensor"},
        "progress": {"name": "Vinyl Track Progress", "topic": "progress", "icon": "mdi:clock-outline", "domain": "sensor"},
        "music_rms": {"name": "Vinyl Music RMS", "topic": "music_rms", "icon": "mdi:waveform", "domain": "sensor"},
        "rumble_rms": {"name": "Vinyl Rumble RMS", "topic": "rumble_rms", "icon": "mdi:vibrate", "domain": "sensor"},
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
        
        if c["domain"] == "binary_sensor":
            payload["payload_on"] = "ON"
            payload["payload_off"] = "OFF"

        mqtt_client.publish(f"homeassistant/{c['domain']}/vinyl_guardian/{key}/config", json.dumps(payload), retain=True)

    mqtt_client.publish("vinyl_guardian/status", "Idle", retain=True)
    mqtt_client.publish("vinyl_guardian/track", "None", retain=True)
    mqtt_client.publish("vinyl_guardian/progress", "00:00 / 00:00", retain=True)
    mqtt_client.publish("vinyl_guardian/scrobble_state", "None", retain=True)
    mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)

def connect_mqtt():
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        publish_discovery()
    except Exception as e: log(f"🚨 MQTT Failed: {e}")

def change_status(new_status):
    mqtt_client.publish("vinyl_guardian/status", new_status, retain=True)

# --- HELPER: GET TRACK DURATION (Unique adamID Lookup) ---
def get_track_duration(title, artist, adamid=None):
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
    except Exception as e:
        if DEBUG: print(f"[DEBUG] Failed to fetch track duration: {e}")
    return 0

# --- RECOGNITION ENGINE (SHAZAM) ---
def recognize_shazam(wav_path):
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

        if 'track' in res_json and 'matches' in res_json and len(res_json['matches']) > 0:
            track = res_json['track']
            title, artist, album, duration, release_year = track.get('title', 'Unknown'), track.get('subtitle', 'Unknown'), "Unknown", 0, "Unknown"
            adamid = track.get('trackadamid') 
            
            for section in track.get('sections', []):
                if section.get('type') == 'SONG':
                    for meta in section.get('metadata', []):
                        if meta.get('title') == 'Album': album = meta.get('text')
                        elif meta.get('title') == 'Length':
                            p = meta.get('text').split(':')
                            duration = int(p[0])*60 + int(p[1]) if len(p)==2 else int(p[0])*3600 + int(p[1])*60 + int(p[2])
                        elif meta.get('title') == 'Released': release_year = meta.get('text')
            
            return {
                "title": title, 
                "artist": artist, 
                "album": album, 
                "release_year": release_year, 
                "offset_seconds": res_json['matches'][0].get('offset', 0), 
                "duration": duration,
                "adamid": adamid
            }
        return None
    except Exception as e:
        log(f"🚨 Shazam Engine Error: {e}")
        return None

# --- BACKGROUND WORKER ---
def process_audio_background(audio_data_bytes, song_start_timestamp):
    global app_state, current_attempt, wake_up_time, consecutive_failures, current_track, scrobble_fired, last_scrobbled_track, paused_track_memory
    log(f"🔬 Analyzing {RECORD_SECONDS}s capture (Attempt {current_attempt}/{MAX_ATTEMPTS})...")

    full_data = np.frombuffer(audio_data_bytes, dtype=np.int16)
    
    peak = int(np.max(np.abs(full_data.astype(np.int32)))) if len(full_data) > 0 else 0
    if peak >= 32000: log(f"⚠️ CLIPPING (Peak: {peak}/32767). Turn DOWN mic_volume!")
    elif peak < 2000: log(f"⚠️ VERY QUIET (Peak: {peak}/32767). Turn UP mic_volume!")

    abs_data = np.abs(full_data)
    trigger = np.where(abs_data > 1000)[0]
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

            # --- LATE START COMPENSATION (CAPPED) ---
            raw_offset = match.get('offset_seconds', 0)
            late_start_offset = min(raw_offset, 30)
            if raw_offset > 2:
                log(f"📍 Late start detected (Offset: {int(raw_offset)}s). Crediting {int(late_start_offset)}s of playtime.")
                scrobble_delay = max(2, scrobble_delay - late_start_offset)

            # --- PAUSE MEMORY RECOVERY (ACCUMULATING) ---
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
                wake_up_time = time.time() + (1800 if consecutive_failures >= 10 else 60)
                if consecutive_failures >= 10: 
                    change_status("Timeout (30m)")
                    consecutive_failures = 0
                app_state = "SLEEPING"

    try:
        if os.path.exists(wav_temp): os.remove(wav_temp)
    except OSError: pass

    if TEST_CAPTURE_MODE:
        log("🛑 TEST CAPTURE COMPLETE. Shutting down."); os._exit(0)

# --- MAIN LOOP ---
def calculate_audio_levels(data):
    try:
        audio_data = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        if len(audio_data) <= 1: return 0.0, 0.0
        raw_rms = float(np.sqrt(np.mean(np.square(audio_data)))) / 32768.0
        filtered_data = audio_data[1:] - 0.95 * audio_data[:-1]
        music_rms = float(np.sqrt(np.mean(np.square(filtered_data)))) / 32768.0
        return raw_rms, music_rms
    except: 
        return 0.0, 0.0

def listen_and_identify():
    global app_state, current_attempt, wake_up_time, scrobble_fired, current_track, last_scrobbled_track, paused_track_memory, inp
    try:
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NORMAL, device='default', channels=CHANNELS, rate=RATE, format=FORMAT, periodsize=CHUNK)
    except Exception as e: log(f"🚨 ALSA Error: {e}"); sys.exit(1)

    log(f"Listening (Music Threshold: {MUSIC_THRESHOLD} | Rumble Threshold: {RUMBLE_THRESHOLD})...")
    
    last_pub, last_sleep_log, cooldown_end, chunks, loud_chunks, silence_sleep, song_start = time.time(), 0, 0, 0, 0, 0, 0
    target = int(RATE / CHUNK * RECORD_SECONDS)
    buffer = bytearray()
    
    turntable_on = False
    trigger_chunks = 0  
    
    power_score = 0
    power_max_score = int(RATE / CHUNK * 2) 

    while True:
        length, data = inp.read()
        if length > 0:
            raw_rms, music_rms = calculate_audio_levels(data)
            now = time.time()
            
            with state_lock:
                current_state = app_state

            # --- DEBOUNCED POWER DETECTION ---
            if raw_rms > MOTOR_POWER_THRESHOLD:
                power_score = min(power_score + 1, power_max_score)
            else:
                power_score = max(power_score - 1, 0)

            if not turntable_on and power_score > (power_max_score * 0.75):
                turntable_on = True
                mqtt_client.publish("vinyl_guardian/power", "ON", retain=True)
            elif turntable_on and power_score < (power_max_score * 0.25):
                turntable_on = False
                mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)

            if now - last_pub >= 1.0:
                mqtt_client.publish("vinyl_guardian/music_rms", f"{music_rms:.4f}")
                mqtt_client.publish("vinyl_guardian/rumble_rms", f"{raw_rms:.4f}")
                
                if current_state == "SLEEPING" and current_track:
                    pos_sec = max(0, int(now - current_track['start_timestamp']))
                    dur_sec = int(current_track['duration'])
                    if pos_sec > dur_sec > 0: pos_sec = dur_sec 
                    p_m, p_s = divmod(pos_sec, 60)
                    d_m, d_s = divmod(dur_sec, 60)
                    prog_str = f"{p_m:02d}:{p_s:02d} / {d_m:02d}:{d_s:02d}" if current_track.get('duration_known', True) else f"{p_m:02d}:{p_s:02d} / ??:??"
                    mqtt_client.publish("vinyl_guardian/progress", prog_str)
                elif current_state != "SLEEPING":
                    mqtt_client.publish("vinyl_guardian/progress", "00:00 / 00:00")
                
                if DEBUG:
                    if current_state == "RECORDING": status = f"🔴 REC {int((chunks/target)*100)}%"
                    elif current_state == "SLEEPING": status = f"💤 SLEEP ({max(0, int(wake_up_time - now))}s)" if now - last_sleep_log >= 15.0 else None
                    elif current_state == "COOLDOWN": status = f"⏳ COOLDOWN ({max(0, int(cooldown_end - now))}s)"
                    elif current_state == "PROCESSING": status = "⚙️ PROC"
                    else: status = "🟢 IDLE"
                    if status: 
                        print(f"[{time.strftime('%H:%M:%S')}] {status} | Music: {music_rms:.4f} | Rumble: {raw_rms:.4f}", flush=True)
                        if "SLEEP" in status: last_sleep_log = now
                last_pub = now

            if current_state == "IDLE":
                if music_rms > MUSIC_THRESHOLD:
                    trigger_chunks += 1
                    if trigger_chunks >= 3:
                        log(f"🎵 AUDIO DETECTED (Music Spike: {music_rms:.4f})")
                        change_status("Recording")
                        song_start, buffer, chunks, loud_chunks, silence_sleep = now, bytearray(data), 1, 1, 0
                        trigger_chunks = 0
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
                        change_status("Idle")
                        with state_lock: app_state = "IDLE"
                    buffer, chunks, loud_chunks = bytearray(), 0, 0
            
            elif current_state == "SLEEPING":
                silence_sleep = silence_sleep + 1 if raw_rms < RUMBLE_THRESHOLD else 0
                if current_track is None or not current_track.get('duration_known', False):
                    required_silence_chunks = int(RATE / CHUNK * 4)
                else:
                    required_silence_chunks = int(RATE / CHUNK * NEEDLE_LIFT_SECONDS)

                if silence_sleep >= required_silence_chunks:
                    if current_track is None:
                        log("⏱️ Track gap detected during fallback!")
                    elif not current_track.get('duration_known', False):
                        log("⏱️ Physical track gap detected (API duration missing).")
                    else:
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
                        
                    change_status("Idle")
                    mqtt_client.publish("vinyl_guardian/track", "None", retain=True)
                    with state_lock: 
                        app_state, current_track, current_attempt, consecutive_failures = "IDLE", None, 1, 0
                    continue

                if current_track and not scrobble_fired and now >= current_track.get('scrobble_trigger_time', 0):
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
                log("🟢 IDLE"); change_status("Idle"); mqtt_client.publish("vinyl_guardian/track", "None", retain=True)
                with state_lock: app_state = "IDLE"

if __name__ == "__main__":
    print("\033[2J\033[H", end="", flush=True)
    print("========================================================")
    log("🚀 BOOTING VINYL GUARDIAN...")
    print("========================================================")
    files_to_clean = [os.path.join(SHARE_DIR, "vinyl_debug.wav"), os.path.join(SHARE_DIR, "shazam_last_match.json"), "/tmp/process.wav"]
    for f in files_to_clean:
        try:
            if os.path.exists(f): os.remove(f); log(f"🧹 Cleaned up: {f}")
        except: pass
    connect_mqtt(); listen_and_identify()