import os
import sys
import time
import json
import threading
import numpy as np
import sounddevice as sd
import paho.mqtt.client as mqtt
import requests
import librosa
from collections import deque

# --- CONFIGURATION ---
MQTT_BROKER = os.environ.get('MQTT_HOST', 'core-mosquitto')
MQTT_PORT = int(os.environ.get('MQTT_PORT', 1883))
MQTT_USER = os.environ.get('MQTT_USER', 'addons')
MQTT_PASS = os.environ.get('MQTT_PASSWORD', '')

AUDIO_DEVICE = os.environ.get('AUDIO_DEVICE', 'default')
SAMPLE_RATE = 22050
CHUNK_SIZE = 4096

# --- THRESHOLDS (To be set by new calibration) ---
POWER_OFF_THRESHOLD = float(os.environ.get('POWER_OFF_THRESHOLD', 0.04))
MOTOR_IDLE_THRESHOLD = float(os.environ.get('MOTOR_IDLE_THRESHOLD', 0.06))
MIN_MUSIC_RMS = float(os.environ.get('MIN_MUSIC_RMS', 0.08))

# --- GLOBALS ---
app_state = "IDLE"
turntable_on = False
power_score = 0
MAX_POWER_SCORE = 10

audio_buffer = deque(maxlen=int(SAMPLE_RATE / CHUNK_SIZE * 15)) 
current_rms = 0.0
music_energy = 0.0
crest_factor = 0.0

rhythm_locked = False
rhythm_score = 0
MAX_RHYTHM_SCORE = 15

current_track = None
song_start_time = 0
has_played_music = False

mqtt_client = None
state_lock = threading.Lock()
DEBUG = os.environ.get('DEBUG', 'true').lower() == 'true'

# Logic Tracking Variables (For smart logging)
last_logged_state = "Unknown"
last_logged_rhythm = False

# ==========================================
# MQTT SETUP
# ==========================================
def setup_mqtt():
    global mqtt_client
    mqtt_client = mqtt.Client(client_id="vinyl_guardian_engine")
    if MQTT_USER and MQTT_PASS:
        mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
    
    configs = {
        "power": {"name": "Turntable Power", "topic": "power", "icon": "mdi:power", "domain": "binary_sensor"},
        "status": {"name": "Vinyl Status", "topic": "status", "icon": "mdi:record-player", "domain": "sensor"},
        "engine": {"name": "Guardian Engine State", "topic": "engine_state", "icon": "mdi:cpu-64-bit", "domain": "sensor"},
        "track": {"name": "Vinyl Current Track", "topic": "track", "icon": "mdi:music-circle", "attr": True, "domain": "sensor"},
        "scrobble_status": {"name": "Scrobble Status", "topic": "scrobble_status", "icon": "mdi:lastpass", "domain": "sensor"},
        "progress": {"name": "Vinyl Track Progress", "topic": "progress", "icon": "mdi:clock-outline", "domain": "sensor"}
    }

    for key, c in configs.items():
        conf_topic = f"homeassistant/{c['domain']}/vinyl_guardian_{key}/config"
        payload = {
            "name": c['name'],
            "state_topic": f"vinyl_guardian/{c['topic']}",
            "unique_id": f"vinyl_guardian_{key}",
            "icon": c['icon'],
            "device": {
                "identifiers": ["vinyl_guardian_system"],
                "name": "Vinyl Guardian",
                "manufacturer": "Custom"
            }
        }
        if c.get("attr"):
            payload["json_attributes_topic"] = "vinyl_guardian/attributes"
            
        mqtt_client.publish(conf_topic, json.dumps(payload), retain=True)
        
    mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)
    mqtt_client.publish("vinyl_guardian/status", "Powered Off", retain=True)
    mqtt_client.publish("vinyl_guardian/engine_state", "Off", retain=True)
    mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
    mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
    mqtt_client.publish("vinyl_guardian/scrobble_status", "Off", retain=True)
    mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00", retain=True)

def connect_mqtt():
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        print(f"✅ Connected to MQTT Broker at {MQTT_BROKER}")
    except Exception as e:
        print(f"❌ MQTT Connection failed: {e}")

# ==========================================
# AUDIO PROCESSING THREAD
# ==========================================
def audio_callback(indata, frames, time_info, status):
    global current_rms, music_energy, crest_factor, rhythm_score, rhythm_locked
    
    if status:
        return
        
    audio_data = indata[:, 0]
    current_rms = np.sqrt(np.mean(audio_data**2))
    
    music_energy = np.mean(np.abs(librosa.effects.preemphasis(audio_data)))
    peak = np.max(np.abs(audio_data))
    crest_factor = peak / current_rms if current_rms > 0 else 0
    
    # Rhythm Lock Logic
    is_rhythm = False
    if current_rms > MIN_MUSIC_RMS and crest_factor > 2.0:
        onset_env = librosa.onset.onset_strength(y=audio_data, sr=SAMPLE_RATE)
        if np.max(onset_env) > 1.5:
            is_rhythm = True
            
    if is_rhythm:
        rhythm_score = min(MAX_RHYTHM_SCORE, rhythm_score + 2)
        if rhythm_score >= 8:
            rhythm_locked = True
    else:
        rhythm_score = max(0, rhythm_score - 1)
        if rhythm_score <= 0:
            rhythm_locked = False

    if app_state in ["RECORDING", "IDLE"]:
        audio_buffer.append(audio_data)

def audio_thread():
    print(f"🎤 Starting Audio Stream on device: {AUDIO_DEVICE}")
    try:
        with sd.InputStream(device=AUDIO_DEVICE, samplerate=SAMPLE_RATE, channels=1, callback=audio_callback, blocksize=CHUNK_SIZE):
            while True:
                time.sleep(1)
    except Exception as e:
        print(f"❌ Audio Stream Error: {e}")
        sys.exit(1)

# ==========================================
# MAIN STATE MACHINE
# ==========================================
def process_shazam():
    global app_state, current_track, song_start_time, has_played_music
    
    with state_lock:
        app_state = "PROCESSING"
    
    if mqtt_client.is_connected():
        mqtt_client.publish("vinyl_guardian/engine_state", "Processing Audio...", retain=True)
        mqtt_client.publish("vinyl_guardian/track", "Searching...", retain=True)
    
    time.sleep(3) # Simulated Shazam delay
    
    # Mock result
    song = {
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "album": "A Night at the Opera",
        "duration": 354,
        "image": "https://i.scdn.co/image/ab67616d0000b273e8b066f70c206551210d902b"
    }
    
    with state_lock:
        current_track = song
        current_track['start_timestamp'] = time.time()
        has_played_music = True
        app_state = "SLEEPING"
        
    if mqtt_client.is_connected():
        mqtt_client.publish("vinyl_guardian/engine_state", "Sleeping (Song Playing)", retain=True)
        mqtt_client.publish("vinyl_guardian/track", f"{song['artist']} - {song['title']}", retain=True)
        mqtt_client.publish("vinyl_guardian/attributes", json.dumps(song), retain=True)
        mqtt_client.publish("vinyl_guardian/status", "Playing Music", retain=True)

def main_loop():
    global power_score, turntable_on, app_state, has_played_music, rhythm_locked
    global last_logged_state, last_logged_rhythm
    
    print("🚀 Vinyl Guardian Engine Started. Awaiting State Changes...")
    
    while True:
        time.sleep(1)
        now = time.time()
        
        # Power Scoring Logic
        if current_rms > POWER_OFF_THRESHOLD:
            power_score = min(MAX_POWER_SCORE, power_score + 1)
        else:
            power_score = max(0, power_score - 1)

        # Turntable Power State
        if power_score >= 4 and not turntable_on:
            turntable_on = True
            if mqtt_client.is_connected():
                mqtt_client.publish("vinyl_guardian/power", "ON", retain=True)
        elif power_score <= 0 and turntable_on:
            turntable_on, has_played_music, rhythm_locked = False, False, False
            with state_lock:
                if app_state in ["RECORDING", "PROCESSING", "SLEEPING", "COOLDOWN"]:
                    app_state = "IDLE"
            if mqtt_client.is_connected(): 
                mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)
                mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00", retain=True)
                mqtt_client.publish("vinyl_guardian/scrobble_status", "Off", retain=True)

        # Status Logic
        current_guardian_state = "Powered Off"
        reason_str = ""
        
        if turntable_on:
            if current_rms > MIN_MUSIC_RMS and rhythm_locked:
                current_guardian_state = "Playing Music"
                reason_str = f"RMS ({current_rms:.4f}) > MIN_MUSIC ({MIN_MUSIC_RMS}) AND Rhythm Locked"
            elif current_rms > MOTOR_IDLE_THRESHOLD:
                current_guardian_state = "Runout Groove" if has_played_music else "Needle Drop / Crackle"
                reason_str = f"RMS ({current_rms:.4f}) > IDLE_THRESHOLD ({MOTOR_IDLE_THRESHOLD}) but No Rhythm"
            else:
                current_guardian_state = "Motor Idle"
                reason_str = f"RMS ({current_rms:.4f}) is between POWER_OFF ({POWER_OFF_THRESHOLD}) and IDLE_THRESHOLD ({MOTOR_IDLE_THRESHOLD})"
        else:
            reason_str = f"RMS ({current_rms:.4f}) remained below POWER_OFF ({POWER_OFF_THRESHOLD})"
            
        # ==========================================
        # SMART DEBUG LOGGING
        # ==========================================
        if DEBUG:
            state_changed = (current_guardian_state != last_logged_state)
            rhythm_changed = (rhythm_locked != last_logged_rhythm)
            
            if state_changed or rhythm_changed:
                timestamp = time.strftime('%H:%M:%S')
                r_icon = "🥁 RHYTHM ACQUIRED" if rhythm_locked else "🛑 RHYTHM LOST"
                
                print(f"\n[{timestamp}] 🔄 STATE CHANGE: {last_logged_state} -> {current_guardian_state}")
                print(f"   ↳ Reason: {reason_str}")
                if rhythm_changed:
                    print(f"   ↳ {r_icon} (Score: {rhythm_score}/{MAX_RHYTHM_SCORE}, Crest: {crest_factor:.2f})")
                
                last_logged_state = current_guardian_state
                last_logged_rhythm = rhythm_locked
        
        if mqtt_client.is_connected():
            mqtt_client.publish("vinyl_guardian/status", current_guardian_state, retain=True)
            
            with state_lock:
                current_state = app_state
                
            if current_state == "SLEEPING" and current_track:
                pos_sec = max(0, int(now - current_track['start_timestamp']))
                dur_sec = int(current_track['duration'])
                if pos_sec > dur_sec > 0: pos_sec = dur_sec
                p_m, p_s = divmod(pos_sec, 60); d_m, d_s = divmod(dur_sec, 60)
                if dur_sec > 0:
                    filled = int((pos_sec / dur_sec) * 10)
                    prog_str = f"[{'█' * filled}{'░' * (10 - filled)}] {p_m:02d}:{p_s:02d} / {d_m:02d}:{d_s:02d}"
                else: 
                    prog_str = f"▶️ {p_m:02d}:{p_s:02d} / ??:??"
                mqtt_client.publish("vinyl_guardian/progress", prog_str, retain=True)
            elif current_state in ["RECORDING", "PROCESSING"]:
                mqtt_client.publish("vinyl_guardian/progress", "▶️ 00:00 / ??:??", retain=True)
            elif current_state in ["IDLE", "COOLDOWN"]:
                mqtt_client.publish("vinyl_guardian/progress", "▶️ 00:00 / ??:??" if turntable_on else "[░░░░░░░░░░] 00:00 / 00:00", retain=True)

        # State Machine Transitions
        with state_lock:
            if app_state == "IDLE" and turntable_on and rhythm_locked:
                app_state = "RECORDING"
                song_start_time = now
                audio_buffer.clear()
                if mqtt_client.is_connected():
                    mqtt_client.publish("vinyl_guardian/engine_state", "Recording Sample...", retain=True)
                    mqtt_client.publish("vinyl_guardian/scrobble_status", "Waiting...", retain=True)
            
            elif app_state == "RECORDING":
                if now - song_start_time >= 12:
                    threading.Thread(target=process_shazam, daemon=True).start()

            elif app_state == "SLEEPING":
                if current_track and now - current_track['start_timestamp'] >= current_track['duration']:
                    app_state = "COOLDOWN"
                    if mqtt_client.is_connected():
                        mqtt_client.publish("vinyl_guardian/engine_state", "Cooldown (Awaiting Next Track)", retain=True)
                        mqtt_client.publish("vinyl_guardian/scrobble_status", f"Scrobbled: {current_track['title']}", retain=True)
                elif current_guardian_state == "Runout Groove":
                    app_state = "COOLDOWN"
                    if mqtt_client.is_connected():
                        mqtt_client.publish("vinyl_guardian/engine_state", "Premature End (Runout Detected)", retain=True)

            elif app_state == "COOLDOWN":
                if not rhythm_locked and current_guardian_state in ["Runout Groove", "Motor Idle"]:
                    app_state = "IDLE"
                    if mqtt_client.is_connected():
                        mqtt_client.publish("vinyl_guardian/engine_state", "Idle (Ready)", retain=True)

if __name__ == "__main__":
    setup_mqtt()
    connect_mqtt()
    threading.Thread(target=audio_thread, daemon=True).start()
    main_loop()