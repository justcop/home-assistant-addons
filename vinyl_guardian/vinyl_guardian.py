import sys
import os
import glob
import json
import time
import threading
import wave
import subprocess
import signal
import numpy as np
import alsaaudio
import paho.mqtt.client as mqtt

# Import local modules
from config import *
from audio_math import *
from integrations import *
from calibration import run_calibration

FORMAT = alsaaudio.PCM_FORMAT_S16_LE

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

# New 3-Tier State Tracking Variables
current_display_status = "Powered Off"
current_engine_status = "Off"

def signal_handler(sig, frame):
    log("🛑 Shutting down gracefully...")
    try:
        global inp
        if inp is not None:
            inp.close()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    except Exception as e:
        log(f"⚠️ Error during shutdown: {e}")
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# --- MQTT SETUP ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def publish_discovery():
    log("Publishing MQTT Auto-Discovery payloads...")
    device_info = {"identifiers": ["vinyl_guardian_01"], "name": "Vinyl Guardian", "manufacturer": "Custom Add-on"}
    
    deprecated_sensors = ["music_rms", "rumble_rms", "scrobble", "scrobble_countdown", "scrobble_state"]
    for old_sensor in deprecated_sensors:
        mqtt_client.publish(f"homeassistant/sensor/vinyl_guardian/{old_sensor}/config", "", retain=True)

    configs = {
        "power": {"name": "Turntable Power", "topic": "power", "icon": "mdi:power", "domain": "binary_sensor"},
        "status": {"name": "Vinyl Status", "topic": "status", "icon": "mdi:record-player", "domain": "sensor"},
        "engine": {"name": "Guardian Engine State", "topic": "engine_state", "icon": "mdi:cpu-64-bit", "domain": "sensor"},
        "track": {"name": "Vinyl Current Track", "topic": "track", "icon": "mdi:music-circle", "attr": True, "domain": "sensor"},
        "scrobble_status": {"name": "Scrobble Status", "topic": "scrobble_status", "icon": "mdi:lastpass", "domain": "sensor"},
        "progress": {"name": "Vinyl Track Progress", "topic": "progress", "icon": "mdi:clock-outline", "domain": "sensor"}
    }

    for key, c in configs.items():
        payload = {"name": c["name"], "state_topic": f"vinyl_guardian/{c['topic']}", "unique_id": f"vinyl_guardian_{key}", "device": device_info, "icon": c["icon"]}
        if c.get("attr"): payload["json_attributes_topic"] = "vinyl_guardian/attributes"
        if c["domain"] == "binary_sensor": payload["payload_on"] = "ON"; payload["payload_off"] = "OFF"
        mqtt_client.publish(f"homeassistant/{c['domain']}/vinyl_guardian/{key}/config", json.dumps(payload), retain=True)

    mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)
    mqtt_client.publish("vinyl_guardian/status", "Powered Off", retain=True)
    mqtt_client.publish("vinyl_guardian/engine_state", "Off", retain=True)
    mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
    mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
    mqtt_client.publish("vinyl_guardian/scrobble_status", "Off", retain=True)
    mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00", retain=True)

def connect_mqtt():
    if CALIBRATION_MODE: return
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
        publish_discovery()
    except Exception as e: log(f"🚨 MQTT Failed: {e}")

def change_3_tier_status(new_vinyl_status, new_engine_status):
    global current_display_status, current_engine_status
    if not CALIBRATION_MODE:
        if mqtt_client.is_connected():
            if new_vinyl_status != current_display_status:
                mqtt_client.publish("vinyl_guardian/status", new_vinyl_status, retain=True)
                current_display_status = new_vinyl_status
            if new_engine_status != current_engine_status:
                mqtt_client.publish("vinyl_guardian/engine_state", new_engine_status, retain=True)
                current_engine_status = new_engine_status

# --- BACKGROUND WORKER ---
def process_audio_background(audio_data_bytes, song_start_timestamp):
    global app_state, current_attempt, wake_up_time, consecutive_failures, current_track, scrobble_fired, last_scrobbled_track, paused_track_memory
   
    local_attempt = None
    with state_lock: local_attempt = current_attempt
    log(f"🔬 Analyzing {RECORD_SECONDS}s capture (Attempt {local_attempt}/{MAX_ATTEMPTS})...")

    full_data = np.frombuffer(audio_data_bytes, dtype=np.int16)
    abs_data = np.abs(full_data)
    trigger = np.where(abs_data > AUDIO_ONSET_THRESHOLD)[0]
    start_idx = trigger[0] if len(trigger) > 0 else 0
    min_s = RATE * MIN_AUDIO_SECONDS
    if len(full_data) - start_idx < min_s: start_idx = max(0, len(full_data) - min_s)

    trimmed_bytes = full_data[start_idx:].tobytes()
    trimmed_seconds = start_idx / RATE
    wav_temp = "/tmp/process.wav"

    try:
        with wave.open(wav_temp, "wb") as wf:
            wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(trimmed_bytes)
    except Exception as e:
        log(f"⚠️ Failed to write temp wav: {e}")
        with state_lock: app_state = "IDLE"
        return
   
    match = recognize_shazam(wav_temp)

    with state_lock:
        if match:
            current_attempt = 1
            consecutive_failures = 0
            total_duration = match.get('duration', 0)
            
            if total_duration <= 0:
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
            raw_offset = match.get('offset_seconds', 0)
            
            previously_played = 0
            if paused_track_memory and paused_track_memory["id"] == track_id:
                previously_played = paused_track_memory["accumulated_playtime"]
                scrobble_delay = max(2, scrobble_delay - previously_played)
                log(f"▶️ Resuming track! Recovered {int(previously_played)}s playtime.")
            else:
                if paused_track_memory: log(f"▶️ New track detected. Starting fresh scrobble timer.")
           
            paused_track_memory = None
            start_ts = int(song_start_timestamp + trimmed_seconds - raw_offset)
            if start_ts < 0: start_ts = int(song_start_timestamp)
           
            current_track = {
                "title": match['title'], "artist": match['artist'], "album": match['album'],
                "duration": total_duration, "start_timestamp": start_ts, "session_start_time": song_start_timestamp,
                "scrobble_trigger_time": song_start_timestamp + scrobble_delay, "duration_known": duration_known,
                "previously_played": previously_played, "source": "Shazam", "image": match.get('image', '')
            }
            scrobble_fired = False

            log(f"🎶 MATCH FOUND: {match['title']} - {match['artist']}")
            mqtt_client.publish("vinyl_guardian/track", f"{match['title']} - {match['artist']}", retain=True)
            try: 
                mqtt_client.publish("vinyl_guardian/attributes", json.dumps(current_track), retain=True)
            except Exception as e: log(f"⚠️ MQTT attribute serialization failed: {e}")
           
            wake_up_time = current_track['start_timestamp'] + total_duration
            app_state = "SLEEPING"
        else:
            if current_attempt < MAX_ATTEMPTS:
                log(f"❌ No match. Retrying ({current_attempt + 1}/{MAX_ATTEMPTS})...")
                current_attempt += 1; app_state = "RECORDING"
            else:
                consecutive_failures += 1
                log(f"❌ Max attempts reached. Fallback to gap detection.")
                mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                mqtt_client.publish("vinyl_guardian/track", "Unknown Track", retain=True)
                current_attempt = 1
                wake_up_time = time.time() + (CONSECUTIVE_FAILURE_TIMEOUT if consecutive_failures >= 10 else FALLBACK_SLEEP_SECS)
                if consecutive_failures >= 10: consecutive_failures = 0
                app_state = "SLEEPING"

    try:
        if os.path.exists(wav_temp): os.remove(wav_temp)
    except: pass
    if TEST_CAPTURE_MODE: log("🛑 TEST CAPTURE COMPLETE."); os._exit(0)

# --- THE GHOST AUDITOR ---
def audit_ghost_files():
    ghost_files = glob.glob(os.path.join(SHARE_DIR, "ghost_trigger_*.wav"))
    if not ghost_files: return
    log(f"🕵️ Automated Regression Test: Sweeping {len(ghost_files)} ghost files against the new Ceiling...")
    power_max_score = int(RATE / CHUNK * MOTOR_HYSTERESIS_SEC)
    
    for gf in ghost_files:
        try:
            with wave.open(gf, 'rb') as wf: raw_bytes = wf.readframes(wf.getnframes())
            chunk_bytes = CHUNK * CHANNELS * 2
            power_score, triggered = 0, False
            for i in range(0, len(raw_bytes), chunk_bytes):
                data = raw_bytes[i:i+chunk_bytes]
                if len(data) == chunk_bytes:
                    raw_rms, music_rms, crest = calculate_audio_levels(data)
                    metrics = calculate_deep_metrics(data)
                    hfer = metrics["hfer"] if metrics else 0.0
                    motor_on_cond = False
                    if music_rms > MUSIC_THRESHOLD and not (crest >= RUNOUT_CREST_THRESHOLD): motor_on_cond = True
                    elif raw_rms > MOTOR_POWER_THRESHOLD:
                        if raw_rms < MOTOR_POWER_CEILING:
                            motor_on_cond = True
                            if MOTOR_HFER_THRESHOLD > 0.0 and hfer > MOTOR_HFER_THRESHOLD: motor_on_cond = False
                    if motor_on_cond:
                        power_score = min(power_score + 1, power_max_score)
                        if power_score >= power_max_score: triggered = True; break
                    else: power_score = max(power_score - 1, 0)
            if not triggered:
                log(f"   ✅ New ceiling SUCCESS! Blocked {os.path.basename(gf)}. Deleting file.")
                os.remove(gf)
            else: log(f"   ❌ New ceiling FAILED to block {os.path.basename(gf)}. Keeping file for review.")
        except Exception as e: log(f"   ⚠️ Error auditing {gf}: {e}")
    log("🏁 Ghost sweep complete.")

# --- MAIN LOOP ---
def listen_and_identify():
    global app_state, current_attempt, wake_up_time, scrobble_fired, current_track, last_scrobbled_track, paused_track_memory, inp
    audit_ghost_files()
    
    try:
        if DEBUG: log(f"🔊 Applying tuned mic volume: {MIC_VOLUME}%")
        subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{MIC_VOLUME}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError: pass
       
    try:
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NORMAL, device='default', channels=CHANNELS, rate=RATE, format=FORMAT, periodsize=CHUNK)
    except Exception as e:
        log(f"🚨 ALSA Error: {e}"); sys.exit(1)

    log("Listening for needle drop...")
    if DEBUG:
        log(f"[DEBUG] Settings: Mus: {MUSIC_THRESHOLD:.4f} | Rum: {RUMBLE_THRESHOLD:.4f} | Mot: {MOTOR_POWER_THRESHOLD:.4f} | HFER: {MOTOR_HFER_THRESHOLD:.4f}")
        log(f"[DEBUG] Buffers: Mot: {MOTOR_HYSTERESIS_SEC}s | Nee: {NEEDLE_HYSTERESIS_SEC}s | Crest: {RUNOUT_CREST_THRESHOLD} | Deb: {DYNAMIC_DEBOUNCE_CHUNKS}")
        log(f"[DEBUG] Hardware Profile: {'Silent (Rhythm Tracker)' if IS_SILENT_HW else 'Standard (Rumble)'}")
   
    last_pub, last_sleep_log, cooldown_end, chunks, loud_chunks, silence_sleep, song_start = time.time(), 0, 0, 0, 0, 0, 0
    idle_silence_chunks, target = 0, int(RATE / CHUNK * RECORD_SECONDS)
    buffer = bytearray()
    
    ghost_buffer, ghost_max_chunks = [], int(RATE / CHUNK * 6.0) 
    turntable_on, has_played_music, trigger_chunks = False, False, 0
    power_score, power_max_score = 0, int(RATE / CHUNK * MOTOR_HYSTERESIS_SEC)
    motor_on_thresh, motor_ceiling = MOTOR_POWER_THRESHOLD, MOTOR_POWER_CEILING
    needle_active_score, needle_max_score = 0, int(RATE / CHUNK * NEEDLE_HYSTERESIS_SEC)
    
    avg_pop_interval = (1.8 + 1.33) / 2.0
    pop_score_boost = int(RATE / CHUNK * (avg_pop_interval * 0.6))
    pop_score_boost = max(int(RATE / CHUNK * 1.0), min(int(RATE / CHUNK * 3.0), pop_score_boost))
   
    needle_down, last_music_time = False, 0
    pop_history, rhythm_locked, last_rhythm_time = [], False, 0
    VALID_RPM_INTERVALS = [(1.20, 1.46), (1.65, 1.95), (2.45, 2.85), (3.35, 3.85)]
    engine_state_map = {"IDLE": "Listening", "RECORDING": "Recording", "PROCESSING": "Processing", "SLEEPING": "Tracking", "COOLDOWN": "Cooldown"}

    while True:
        length, data = inp.read()
        if length > 0:
            if DEBUG_GHOST_CATCHER:
                ghost_buffer.append(data)
                if len(ghost_buffer) > ghost_max_chunks: ghost_buffer.pop(0)

            raw_rms, music_rms, crest = calculate_audio_levels(data)
            metrics = calculate_deep_metrics(data)
            hfer = metrics["hfer"] if metrics else 0.0
            now = time.time()
            is_dust_pop = crest >= RUNOUT_CREST_THRESHOLD
           
            with state_lock: current_state = app_state
            current_guardian_state = engine_state_map.get(current_state, "Listening")

            if current_state in ["RECORDING", "PROCESSING", "SLEEPING"]: has_played_music = True
            if music_rms > MUSIC_THRESHOLD: last_music_time = now

            motor_on_cond = False
            if music_rms > MUSIC_THRESHOLD and not is_dust_pop: motor_on_cond = True
            elif raw_rms > motor_on_thresh:
                if raw_rms < motor_ceiling:
                    motor_on_cond = True
                    if MOTOR_HFER_THRESHOLD > 0.0 and hfer > MOTOR_HFER_THRESHOLD and not has_played_music: motor_on_cond = False
                else:
                    if turntable_on: motor_on_cond = True
                    else: motor_on_cond = False
            
            if motor_on_cond:
                power_score = min(power_score + 1, power_max_score)
                if power_score >= power_max_score:
                    if not turntable_on:
                        turntable_on = True
                        if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/power", "ON", retain=True)
                        if DEBUG_GHOST_CATCHER:
                            ts = int(time.time()); wav_name = os.path.join(SHARE_DIR, f"ghost_trigger_{ts}.wav")
                            try:
                                with wave.open(wav_name, "wb") as wf:
                                    wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(b"".join(ghost_buffer))
                            except Exception: pass
            else:
                if has_played_music or rhythm_locked: power_score = power_max_score
                else: power_score = max(power_score - 1, 0)
                    
                if turntable_on and power_score <= 0:
                    turntable_on, has_played_music, rhythm_locked = False, False, False
                    with state_lock:
                        if app_state in ["RECORDING", "PROCESSING", "SLEEPING", "COOLDOWN"]:
                            if app_state == "SLEEPING" and current_track and not scrobble_fired:
                                current_silence_sec = silence_sleep * (CHUNK / RATE)
                                time_played = (now - current_track['session_start_time']) - current_silence_sec + current_track.get('previously_played', 0)
                                if time_played > 5:
                                    track_id = f"{current_track['title']} - {current_track['artist']}"
                                    paused_track_memory = {"id": track_id, "accumulated_playtime": time_played}
                            app_state, current_track, scrobble_fired, current_attempt, consecutive_failures = "IDLE", None, False, 1, 0
                    if mqtt_client.is_connected(): 
                        mqtt_client.publish("vinyl_guardian/power", "OFF", retain=True)
                        mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                        mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                        mqtt_client.publish("vinyl_guardian/progress", "[░░░░░░░░░░] 00:00 / 00:00", retain=True)
                        mqtt_client.publish("vinyl_guardian/scrobble_status", "Off", retain=True)

            if not turntable_on: current_guardian_state = "Off"

            if is_dust_pop:
                needle_active_score = min(needle_active_score + pop_score_boost, needle_max_score)
                pop_history.append(now)
                if len(pop_history) > 15: pop_history.pop(0)
                match_count = 0
                for p in pop_history[:-1]:
                    delta = now - p
                    for lo, hi in VALID_RPM_INTERVALS:
                        if lo <= delta <= hi: match_count += 1; break
                if match_count >= 2: rhythm_locked, last_rhythm_time = True, now
            elif raw_rms >= RUMBLE_THRESHOLD: needle_active_score = min(needle_active_score + 1, needle_max_score)
            else: needle_active_score = max(needle_active_score - 1, 0)
                
            if rhythm_locked and (now - last_rhythm_time > 6.0): rhythm_locked = False
            needle_down = needle_active_score > (needle_max_score * 0.5)
            continuous_silence = now - last_music_time

            new_vinyl_status = "Motor Idle"
            if not turntable_on: new_vinyl_status = "Powered Off"
            elif rhythm_locked: new_vinyl_status = "Runout Groove"
            elif current_state in ["RECORDING", "PROCESSING"]: new_vinyl_status = "Playing"
            elif has_played_music:
                if continuous_silence < 2.0: new_vinyl_status = "Playing"
                else: new_vinyl_status = "Between Tracks"

            if continuous_silence >= int(RATE / CHUNK * NEEDLE_LIFT_SECONDS) * (CHUNK / RATE) and not rhythm_locked:
                has_played_music = False
                
            change_3_tier_status(new_vinyl_status, current_guardian_state)

            if now - last_pub >= 1.0:
                if mqtt_client.is_connected():
                    if not turntable_on: scrob_str = "Off"
                    elif current_state == "SLEEPING" and current_track:
                        if scrobble_fired: scrob_str = f"Scrobbled: {last_scrobbled_track.split(' - ')[0]} ✅"
                        else:
                            current_silence_sec = silence_sleep * (CHUNK / RATE)
                            time_left = max(0, int(current_track.get('scrobble_trigger_time', 0) - (now - current_silence_sec)))
                            m, s = divmod(time_left, 60); scrob_str = f"In {m:02d}:{s:02d} ⏳" if time_left > 0 else "Scrobbling... 🚀"
                    else: scrob_str = f"Scrobbled: {last_scrobbled_track.split(' - ')[0]} ✅" if last_scrobbled_track else "Waiting ⏸️"
                    mqtt_client.publish("vinyl_guardian/scrobble_status", scrob_str, retain=True)

                    if current_state == "SLEEPING" and current_track:
                        pos_sec, dur_sec = max(0, int(now - current_track['start_timestamp'])), int(current_track['duration'])
                        if pos_sec > dur_sec > 0: pos_sec = dur_sec
                        p_m, p_s = divmod(pos_sec, 60); d_m, d_s = divmod(dur_sec, 60)
                        if current_track.get('duration_known', True) and dur_sec > 0:
                            filled = int((pos_sec / dur_sec) * 10)
                            prog_str = f"[{'█' * filled}{'░' * (10 - filled)}] {p_m:02d}:{p_s:02d} / {d_m:02d}:{d_s:02d}"
                        else: prog_str = f"▶️ {p_m:02d}:{p_s:02d} / ??:??"
                        mqtt_client.publish("vinyl_guardian/progress", prog_str)
                    elif current_state in ["RECORDING", "PROCESSING"]:
                        p_m, p_s = divmod(max(0, int(now - song_start)), 60)
                        mqtt_client.publish("vinyl_guardian/progress", f"▶️ {p_m:02d}:{p_s:02d} / ??:??")
                    elif current_state in ["IDLE", "COOLDOWN"]:
                        mqtt_client.publish("vinyl_guardian/progress", "▶️ 00:00 / ??:??" if turntable_on else "[░░░░░░░░░░] 00:00 / 00:00")
               
                if DEBUG:
                    if current_state == "RECORDING": pct = int((chunks/target)*100); status = f"🔴 REC {pct}%"
                    elif current_state == "SLEEPING": status = f"💤 SLEEP ({max(0, int(wake_up_time - now))}s)" if now - last_sleep_log >= 15.0 else None
                    elif current_state == "COOLDOWN": status = f"⏳ COOLDOWN ({max(0, int(cooldown_end - now))}s)"
                    elif current_state == "PROCESSING": status = "⚙️ PROC"
                    else: status = f"🟢 {new_vinyl_status.upper()}"
                        
                    if status:
                        rhythm_flag = " | 🥁 RHYTHM LOCK" if rhythm_locked else ""
                        print(f"[{time.strftime('%H:%M:%S')}] {status} | RMS: {raw_rms:.4f} | Music: {music_rms:.4f} | Crest: {crest:.2f}{rhythm_flag}", flush=True)
                        if "SLEEP" in status: last_sleep_log = now
                last_pub = now

            if current_state == "IDLE":
                if not needle_down and not has_played_music and not rhythm_locked:
                    idle_silence_chunks += 1
                    if idle_silence_chunks == int(RATE / CHUNK * NEEDLE_LIFT_SECONDS):
                        if mqtt_client.is_connected():
                            mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                            mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                        idle_silence_chunks = 0
                else: idle_silence_chunks = 0
                    
                if music_rms > MUSIC_THRESHOLD and not is_dust_pop:
                    trigger_chunks += 1
                    if trigger_chunks >= DYNAMIC_DEBOUNCE_CHUNKS:
                        if not turntable_on:
                            turntable_on, power_score = True, power_max_score
                            if mqtt_client.is_connected(): mqtt_client.publish("vinyl_guardian/power", "ON", retain=True)
                        if mqtt_client.is_connected():
                            mqtt_client.publish("vinyl_guardian/track", "Searching...", retain=True)
                            mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                        song_start, buffer, chunks, loud_chunks, silence_sleep, trigger_chunks = now, bytearray(data), 1, 1, 0, 0
                        with state_lock: app_state = "RECORDING"
                else: trigger_chunks = 0  
            elif current_state == "RECORDING":
                buffer.extend(data); chunks += 1
                if raw_rms > RUMBLE_THRESHOLD: loud_chunks += 1
                if len(buffer) > MAX_BUFFER_SIZE:
                    buffer.clear(); chunks, loud_chunks = 0, 0
                    if mqtt_client.is_connected():
                        mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                        mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                    with state_lock: app_state = "IDLE"
                    continue
                if chunks >= target:
                    if loud_chunks >= (target / 2.0):
                        with state_lock: app_state = "PROCESSING"
                        threading.Thread(target=process_audio_background, args=(bytes(buffer), song_start)).start()
                    else:
                        if mqtt_client.is_connected():
                            mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                            mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                        with state_lock: app_state = "IDLE"
                    buffer, chunks, loud_chunks = bytearray(), 0, 0
            elif current_state == "SLEEPING":
                if music_rms > MUSIC_THRESHOLD: silence_sleep = 0
                else: silence_sleep += 1
                required_silence_chunks = int(RATE / CHUNK * NEEDLE_LIFT_SECONDS)
                if silence_sleep >= required_silence_chunks:
                    if not rhythm_locked:
                        if current_track and not scrobble_fired:
                            time_played = (now - current_track['session_start_time']) - (required_silence_chunks * (CHUNK / RATE)) + current_track.get('previously_played', 0)
                            if time_played > 5:
                                track_id = f"{current_track['title']} - {current_track['artist']}"
                                with state_lock: paused_track_memory = {"id": track_id, "accumulated_playtime": time_played}
                        if mqtt_client.is_connected():
                            mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                            mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                        with state_lock: app_state, current_track, current_attempt, consecutive_failures, has_played_music = "IDLE", None, 1, 0, False
                        continue
                physical_now = now - (silence_sleep * (CHUNK / RATE))
                if current_track and not scrobble_fired and physical_now >= current_track.get('scrobble_trigger_time', 0):
                    track_id = f"{current_track['title']} - {current_track['artist']}"
                    if track_id != last_scrobbled_track:
                        scrobble_to_lastfm(current_track['artist'], current_track['title'], current_track['start_timestamp'], current_track['album'])
                        if mqtt_client.is_connected():
                            mqtt_client.publish("vinyl_guardian/scrobble_state", track_id, retain=True)
                            try: mqtt_client.publish("vinyl_guardian/scrobble", json.dumps(current_track), retain=True)
                            except Exception: pass
                        with state_lock: scrobble_fired, last_scrobbled_track, paused_track_memory = True, track_id, None
                    else:
                        with state_lock: scrobble_fired = True
                if now >= wake_up_time:
                    cooldown_end = now + 4
                    if mqtt_client.is_connected():
                        mqtt_client.publish("vinyl_guardian/track", "Not Playing", retain=True)
                        mqtt_client.publish("vinyl_guardian/attributes", "{}", retain=True)
                    with state_lock: app_state, current_track = "COOLDOWN", None
            elif current_state == "COOLDOWN" and now >= cooldown_end:
                with state_lock: app_state = "IDLE"

if __name__ == "__main__":
    if CALIBRATION_MODE: run_calibration()
    else:
        files_to_clean = [os.path.join(SHARE_DIR, "vinyl_debug.wav"), "/tmp/process.wav"]
        for f in files_to_clean:
            try:
                if os.path.exists(f): os.remove(f)
            except Exception: pass
        connect_mqtt()
        listen_and_identify()