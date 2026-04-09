import os
import time
import json
import wave
import subprocess
import shutil
import numpy as np
import alsaaudio
import warnings
import sys

# Suppress numpy warnings for clean output
warnings.filterwarnings('ignore')

# Import standard settings from your existing config
from config import SHARE_DIR, AUTO_CALIB_FILE, RATE, CHANNELS, CHUNK
from audio_math import is_valid_pop, update_rhythm_lock, RUNOUT_RPM_INTERVALS

# --- HOME ASSISTANT OPTION LOADING ---
REUSE_CALIB_OPT = False
OPTIONS_FILE = "/data/options.json"
if os.path.exists(OPTIONS_FILE):
    try:
        with open(OPTIONS_FILE, "r") as f:
            opts = json.load(f)
            advanced_opts = opts.get("advanced", {})
            REUSE_CALIB_OPT = advanced_opts.get("reuse_calibration_audio", False)
    except Exception:
        pass

# --- CONFIGURATION ---
FORMAT = alsaaudio.PCM_FORMAT_S16_LE
CALIB_DIR = os.path.join(SHARE_DIR, "calibration_data")
REPORT_FILE = os.path.join(SHARE_DIR, "calibration_report.txt")

# Global report list for file output
report_log = []

def print_log(msg):
    """Prints to console and saves to the report log"""
    print(msg, flush=True)
    report_log.append(msg)

# --- NATIVE MATH UTILITIES ---
def reject_outliers_mad(data, threshold=3.5):
    if len(data) == 0: return data
    med = np.median(data)
    mad = np.median(np.abs(data - med))
    if mad == 0: return data
    modified_z_scores = 0.6745 * (data - med) / mad
    return data[np.abs(modified_z_scores) <= threshold]

def get_rms(audio_data):
    if len(audio_data) == 0: return 0.0
    return float(np.sqrt(np.mean(np.square(audio_data))))

def get_music_rms(audio_data):
    if len(audio_data) <= 1: return 0.0
    filtered_data = audio_data[1:] - 0.95 * audio_data[:-1]
    return float(np.sqrt(np.mean(np.square(filtered_data))))

def get_hfer(audio_data):
    """Extract High Frequency Energy Ratio directly from numpy array"""
    if len(audio_data) <= 1: return 0.0
    rms = get_rms(audio_data)
    if rms < 0.0001: return 0.0
    hf_data = audio_data[1:] - audio_data[:-1]
    hf_rms = float(np.sqrt(np.mean(np.square(hf_data))))
    return hf_rms / rms

def load_wav(filename):
    with wave.open(filename, 'rb') as wf:
        n_frames = wf.getnframes()
        audio_bytes = wf.readframes(n_frames)
        audio_data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if wf.getnchannels() == 2:
            audio_data = audio_data.reshape(-1, 2).mean(axis=1)
        return audio_data

def chunked_rms(data, chunk_size=4096):
    chunks = len(data) // chunk_size
    rms_arr = np.zeros(chunks)
    for i in range(chunks):
        rms_arr[i] = get_rms(data[i*chunk_size:(i+1)*chunk_size])
    return rms_arr

def chunked_music_rms(data, chunk_size=4096):
    chunks = len(data) // chunk_size
    rms_arr = np.zeros(chunks)
    for i in range(chunks):
        rms_arr[i] = get_music_rms(data[i*chunk_size:(i+1)*chunk_size])
    return rms_arr

def chunked_hfer(data, chunk_size=4096):
    chunks = len(data) // chunk_size
    hfer_arr = np.zeros(chunks)
    for i in range(chunks):
        hfer_arr[i] = get_hfer(data[i*chunk_size:(i+1)*chunk_size])
    return hfer_arr

# --- ALSA RECORDING ENGINE ---
def record_chunk(duration):
    try:
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NORMAL, device='default', channels=CHANNELS, rate=RATE, format=FORMAT, periodsize=CHUNK)
    except Exception as e:
        print_log(f"🚨 ALSA Error: Could not open microphone -> {e}")
        return bytearray(), np.array([])

    frames_to_record = int(RATE * duration)
    frames_recorded = 0
    raw_audio = bytearray()
    
    while frames_recorded < frames_to_record:
        length, data = inp.read()
        if length > 0:
            raw_audio.extend(data)
            frames_recorded += length
            
    inp.close()
    audio_data = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32) / 32768.0
    return raw_audio, audio_data

def record_segmented_file(filename, action_dur, settle_dur, steady_dur, prompt):
    print_log(f"\n" + "-"*50)
    print_log(f"{prompt}")
    
    raw_bytes = bytearray()
    
    if action_dur > 0:
        print_log(f"🎬 ACTION WINDOW ({action_dur}s): Perform action NOW!")
        chunk_b, _ = record_chunk(action_dur)
        raw_bytes.extend(chunk_b)
        
    if settle_dur > 0:
        print_log(f"⏳ SETTLING ({settle_dur}s): Allowing motor/reverb to stabilize...")
        chunk_b, _ = record_chunk(settle_dur)
        raw_bytes.extend(chunk_b)
        
    if steady_dur > 0:
        print_log(f"⏹️  STEADY STATE ({steady_dur}s): Capturing stable background...")
        chunk_b, _ = record_chunk(steady_dur)
        raw_bytes.extend(chunk_b)
    
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(raw_bytes)
        
    print_log(f"✅ Saved to {os.path.basename(filename)}")
    time.sleep(1)

def record_dynamic_transition(filename):
    print_log(f"\n" + "-"*50)
    print_log("[FILE 3/6: THE MASTER TRANSITION]\n🎶 ACTION: Drop needle on the LAST TRACK now.")
    print_log("〰️  The system will listen live for the track to end, wait for the runout groove, and capture the rumble.")
    
    raw_bytes = bytearray()
    
    print_log(f"🎬 ACTION WINDOW (25s): Drop the needle NOW!")
    chunk_b, _ = record_chunk(25.0)
    raw_bytes.extend(chunk_b)
    
    print_log("🎵 MUSIC PHASE: Listening for the track to naturally end...")
    max_music_rms = 0.0
    consecutive_low = 0
    music_ended = False
    
    for i in range(360):
        chunk_b, audio = record_chunk(1.0)
        raw_bytes.extend(chunk_b)
        
        m_rms = get_music_rms(audio)
        
        if i < 15:
            max_music_rms = max(max_music_rms, m_rms)
            continue
            
        threshold = max(max_music_rms * 0.15, 0.002) 
        if m_rms < threshold:
            consecutive_low += 1
        else:
            consecutive_low = 0
            max_music_rms = max(max_music_rms, m_rms) 
            
        if consecutive_low >= 12: 
            print_log(f"📉 MUSIC DROP-OFF DETECTED! (Track ended ~12s ago)")
            music_ended = True
            break
            
    if not music_ended:
        print_log("⚠️ Fail-safe reached. Max 6 minutes recorded without detecting end of song.")
        
    print_log("⏺️ STEADY STATE (15s): Capturing remaining pure runout rumble...")
    chunk_b, _ = record_chunk(15.0)
    raw_bytes.extend(chunk_b)
    
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(raw_bytes)
        
    print_log(f"✅ Saved dynamic transition to {os.path.basename(filename)}")
    time.sleep(1)

# --- STEP 0: AUTOMATED GAIN STAGING ---
def set_mic_volume(vol_pct):
    try: subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{vol_pct}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except: pass

def gain_staging():
    print_log("\n" + "="*50)
    print_log("🎚️  STEP 0: AUTO-CALIBRATING SOFTWARE VOLUME")
    print_log("="*50)
    print_log("🔊 ACTION: Find the LOUDEST record you own and drop the needle NOW.")
    print_log("   Searching for 1% precision sweet spot...")
    
    current_vol = 50
    step = 16 
    last_direction = 0 
    set_mic_volume(current_vol)
    
    time.sleep(10) 
    
    while True:
        _, audio_data = record_chunk(3.0)
        if len(audio_data) == 0: return current_vol
        peak = np.max(np.abs(audio_data))
        
        if peak > 0.80:
            if last_direction == 1: step = max(1, step // 2)
            last_direction = -1
            current_vol = max(1, current_vol - step)
            set_mic_volume(current_vol)
            print_log(f"   Peak {peak:.2f} (Hot) -> Vol: {current_vol}%")
        elif peak < 0.50:
            if last_direction == -1: step = max(1, step // 2)
            last_direction = 1
            current_vol = min(100, current_vol + step)
            set_mic_volume(current_vol)
            print_log(f"   Peak {peak:.2f} (Low) -> Vol: {current_vol}%")
        else:
            print_log(f"   Peak {peak:.2f} (Testing...) -> Verifying {current_vol}% for 10s...")
            _, v_data = record_chunk(10.0)
            v_peak = np.max(np.abs(v_data))
            if v_peak > 0.85:
                current_vol -= 1
                set_mic_volume(current_vol)
                continue
            print_log(f"✅ VOLUME LOCKED at {current_vol}%")
            break
            
    print_log("\n⏹️  ACTION: Stop the record and turn the turntable OFF completely.")
    time.sleep(5)
    return current_vol

# --- SIMULATION & TIMELINE ENGINE ---
def find_rhythmic_pulse(data, start_sec, search_thresholds):
    chunk_size = 4096
    chunks = len(data) // chunk_size
    pop_history = []
    rhythm_locked = False
    last_rhythm_time = -10.0

    for i in range(chunks):
        chunk = data[i*chunk_size : (i+1)*chunk_size]
        r = get_rms(chunk)
        max_val = np.max(np.abs(chunk))
        time_sec = (i * chunk_size) / RATE

        if is_valid_pop(r, max_val, search_thresholds):
            pop_history, rhythm_locked, last_rhythm_time = update_rhythm_lock(
                pop_history, time_sec, max_val, rhythm_locked, last_rhythm_time
            )
            if rhythm_locked:
                return start_sec + time_sec
        else:
            pop_history = [item for item in pop_history if time_sec - item[0] <= 4.0]

    return None

def simulate_timeline(data, thresholds, initial_power, initial_status):
    """
    Chronological Dual-Sensor Engine.
    Restores yesterday's logic using HFER and Upper Transient Limits 
    instead of a blunt silence gate.
    """
    chunk_size = 4096 
    chunks = len(data) // chunk_size
    
    current_power = initial_power
    current_status = initial_status
    transitions = [f"   -> 0.0s : Power [{initial_power}] | Status [{initial_status}]"]
    
    pop_history = [] 
    rhythm_locked = False
    last_rhythm_time = -10.0
    
    turntable_on = (initial_power == "On")
    power_max_score = int(RATE / chunk_size * 6.0) 
    power_score = power_max_score if turntable_on else 0
    consecutive_music = 0
    
    power_history = [initial_power] * 6
    status_history = [initial_status] * 6
    
    for i in range(chunks):
        chunk = data[i*chunk_size : (i+1)*chunk_size]
        raw_rms = get_rms(chunk)
        music_rms = get_music_rms(chunk)
        hfer = get_hfer(chunk)
        max_val = np.max(np.abs(chunk))
        time_sec = (i * chunk_size) / RATE
        
        # 1. Pop Detection
        is_dust_pop = is_valid_pop(raw_rms, max_val, thresholds)
            
        # 2. Music tracker
        if music_rms > thresholds["music_threshold"] and not is_dust_pop:
            consecutive_music += 1
        else:
            consecutive_music = 0
            
        is_playing = (consecutive_music >= 3)

        # 3. Rhythm Tracking
        if is_dust_pop:
            pop_history, rhythm_locked, last_rhythm_time = update_rhythm_lock(
                pop_history, time_sec, max_val, rhythm_locked, last_rhythm_time
            )
        else:
            pop_history = [item for item in pop_history if time_sec - item[0] <= 4.0]

        if is_playing:
            pop_history.clear()
            rhythm_locked = False
            
        if rhythm_locked and (time_sec - last_rhythm_time > 6.0):
            rhythm_locked = False
            
        if len(pop_history) == 0:
            rhythm_locked = False

        # 4. POWER HYSTERESIS (Yesterday's logic restored!)
        upper_limit = max(thresholds["motor_power_threshold"] * 4.5, thresholds["max_room_transient"] * 1.2)
        motor_on_cond = raw_rms > thresholds["motor_power_threshold"]
        
        # HFER Rejection: If it's a high-frequency sound (talking), kill the motor flag
        if thresholds["motor_hfer_threshold"] > 0.0 and raw_rms < upper_limit:
            if hfer > thresholds["motor_hfer_threshold"]:
                motor_on_cond = False

        # Transient Rejection: If the system is currently asleep, ignore sudden loud thumps
        if not turntable_on and not is_playing and not rhythm_locked:
            if raw_rms > upper_limit:
                motor_on_cond = False

        # Physical Vinyl Drag naturally keeps the motor on (Dead wax bridge)
        if is_playing or rhythm_locked:
            motor_on_cond = True

        if motor_on_cond:
            power_score = min(power_score + 1, power_max_score)
            if power_score >= power_max_score:
                turntable_on = True
        else:
            power_score = max(power_score - 1, 0)
            if power_score <= 0:
                turntable_on = False

        # 5. STRICT SENSOR RESOLUTION
        p_state = "On" if turntable_on else "Off"
        
        if p_state == "Off":
            s_state = "Powered Off"
        elif is_playing:
            s_state = "Playing"
        elif rhythm_locked:
            s_state = "Runout Groove"
        else:
            s_state = "Motor Idle"
        
        # 6. SMOOTHED OUTPUT LOGGING
        power_history.append(p_state)
        power_history.pop(0)
        status_history.append(s_state)
        status_history.pop(0)
        
        latest_p = max(set(power_history), key=power_history.count)
        latest_s = max(set(status_history), key=status_history.count)
        
        if latest_p != current_power or latest_s != current_status:
            transitions.append(f"   -> {time_sec:.1f}s : Power [{latest_p}] | Status [{latest_s}]")
            current_power = latest_p
            current_status = latest_s
                
    return transitions, current_power, current_status

def calculate_hardware_thresholds(files):
    print_log("\n" + "="*70)
    print_log("🧠 THE GUARDIAN ENGINE CALIBRATION (RESTORING ORIGINAL LOGIC)")
    print_log("="*70)
    
    # --- 1. FILE 1: BASELINE NOISE (Silence Gate) ---
    print_log("\n[STAGE 1: FILE 1 - BASELINE NOISE]")
    print_log("   Goal: Measure absolute room and microphone silence (Turntable OFF).")
    floor_1_data = load_wav(files["floor"])
    baseline_rms_arr = reject_outliers_mad(chunked_rms(floor_1_data))
    baseline_median = float(np.median(baseline_rms_arr))
    floor_max_amp = float(np.max(np.abs(floor_1_data))) 
    
    print_log(f"   [EXTRACTED] Baseline Silence Median: {baseline_median:.6f}")
    print_log(f"   [EXTRACTED] Max Static Amplitude: {floor_max_amp:.6f}")

    # --- 2. FILE 2: MOTOR HUM (Idle Threshold) ---
    print_log("\n[STAGE 2: FILE 2 - MOTOR HUM]")
    print_log("   Goal: Identify the vibration signature of your platter spinning (Turntable ON, Needle UP).")
    spinup_data = load_wav(files["spinup"])
    motor_rms_arr = reject_outliers_mad(chunked_rms(spinup_data[20*RATE:]))
    motor_median = float(np.median(motor_rms_arr))
    
    if motor_median <= baseline_median * 1.3:
        is_silent_hw = True
        motor_power_threshold = baseline_median * 1.5 
        print_log(f"   [INFO] Turntable motor is extremely quiet. (Hum: {motor_median:.6f})")
        print_log("          System will remain 'Off' until needle drop.")
    else:
        is_silent_hw = False
        # Calculate exactly in the middle of silence and motor hum
        motor_power_threshold = float((baseline_median + motor_median) / 2.0)
        print_log(f"   [EXTRACTED] Motor Power Threshold: {motor_power_threshold:.6f}")

    # --- 3. FILE 6: DISTURBANCE (Maximum false-positive music floor) ---
    print_log("\n[STAGE 3: FILE 6 - ROOM NOISE & DISTURBANCE]")
    print_log("   Goal: Measure how much acoustic room noise (talking, tapping) bleeds into the needle.")
    disturb_data = load_wav(files["disturbance"])
    
    # Extract Max Transient
    disturb_rms_arr = chunked_rms(disturb_data)
    max_room_transient = float(np.max(disturb_rms_arr)) if len(disturb_rms_arr) > 0 else 0.01
    
    # Extract HFER Signature
    motor_hfer_arr = chunked_hfer(spinup_data[20*RATE:])
    disturb_hfer_arr = chunked_hfer(disturb_data)
    
    motor_hfer_95 = float(np.percentile(motor_hfer_arr, 95)) if len(motor_hfer_arr) > 0 else 0.0
    noise_hfer_05 = float(np.percentile(disturb_hfer_arr, 5)) if len(disturb_hfer_arr) > 0 else 0.0
    
    if motor_hfer_95 < noise_hfer_05 and motor_hfer_95 > 0:
        motor_hfer_threshold = float((motor_hfer_95 + noise_hfer_05) / 2.0)
        print_log(f"   [EXTRACTED] HFER Acoustic Signature: {motor_hfer_threshold:.4f} (Separates motor hum from talking)")
    else:
        motor_hfer_threshold = 0.0
        print_log("   [INFO] HFER Acoustic overlap detected. Relying strictly on RMS transients.")
        
    disturb_music_arr = chunked_music_rms(disturb_data)
    disturb_music_max = float(np.max(disturb_music_arr)) if len(disturb_music_arr) > 0 else 0.0
    
    print_log(f"   [EXTRACTED] Max Ambient Transient: {max_room_transient:.6f}")
    print_log(f"   [EXTRACTED] Max Ambient Music-Bleed: {disturb_music_max:.6f}")

    # --- 4. FILE 3: MASTER TRANSITION (Music and Runout Profiling) ---
    print_log("\n[STAGE 4: FILE 3 - THE MASTER TRANSITION]")
    print_log("   Goal: Map the complex transition from the end of a song into the rhythmic runout groove.")
    trans_data = load_wav(files["transition"])
    trans_duration = len(trans_data) / RATE
    
    search_start_idx = int(25 * RATE / 8192) 
    trans_m_rms = chunked_music_rms(trans_data, chunk_size=8192)
    
    if len(trans_m_rms) > search_start_idx:
        search_arr = trans_m_rms[search_start_idx:]
        peak_music = np.max(search_arr)
        threshold = max(peak_music * 0.15, 0.002)
        active_indices = np.where(search_arr > threshold)[0]
        if len(active_indices) > 0:
            last_active = active_indices[-1]
            drop_time_sec = 25.0 + ((last_active + 1) * 8192 / RATE)
        else:
            drop_time_sec = 25.0
    else:
        drop_time_sec = trans_duration - 15.0
        
    print_log(f"   [STEP A] Detected the exact moment the music ended: {drop_time_sec:.2f}s mark.")
    
    raw_music_chunk = trans_data[int(25*RATE) : int(drop_time_sec * RATE)]
    raw_music_rms_arr = chunked_music_rms(raw_music_chunk)
    valid_music_rms_arr = raw_music_rms_arr[raw_music_rms_arr > (baseline_median * 2.0)]
    
    raw_music_min = float(np.percentile(valid_music_rms_arr, 5)) if len(valid_music_rms_arr) > 0 else 0.005
    music_threshold = max(raw_music_min * 0.85, disturb_music_max * 1.15)
    music_threshold = max(music_threshold, baseline_median * 1.5)
    print_log(f"   [EXTRACTED] Music Threshold: {music_threshold:.6f}")
    
    runout_chunks_data = trans_data[int(drop_time_sec * RATE):]
    if len(runout_chunks_data) == 0:
        runout_chunks_data = trans_data[-8192:]

    runout_chunks_n = len(runout_chunks_data) // 4096
    runout_crests = []
    runout_amps = []

    for i in range(runout_chunks_n):
        chunk = runout_chunks_data[i*4096:(i+1)*4096]
        r = get_rms(chunk)
        if r > 0:
            m_val = np.max(np.abs(chunk))
            if m_val / r > 2.5:
                runout_crests.append(m_val / r)
                runout_amps.append(m_val)

    if len(runout_crests) > 0:
        base_crest = np.percentile(runout_crests, 75)
        pop_crest_threshold = max(3.5, base_crest * 0.80)
        base_amp = np.percentile(runout_amps, 75)
        pop_amplitude_threshold = max(floor_max_amp * 1.25, base_amp * 0.70)
        print_log(f"   [EXTRACTED] Runout Pop Sharpness (Crest): {pop_crest_threshold:.2f}")
        print_log(f"   [EXTRACTED] Runout Pop Minimum Amplitude: {pop_amplitude_threshold:.6f}")
    else:
        print_log("   [DEBUG] Warning: Runout extraction failed. Falling back to default tolerances.")
        pop_crest_threshold = 4.0
        pop_amplitude_threshold = floor_max_amp * 1.5

    provisional_thresholds = {
        "runout_crest_threshold": pop_crest_threshold,
        "pop_amplitude_threshold": pop_amplitude_threshold,
        "motor_power_ceiling": 1.0, 
    }
    pulse_start_time = find_rhythmic_pulse(
        trans_data[int(drop_time_sec * RATE):], drop_time_sec, provisional_thresholds
    )

    if pulse_start_time:
        print_log(f"   [STEP D] Success! Found a verified 33/45 RPM rhythmic lock beginning at {pulse_start_time:.2f}s.")
        runout_start = int(pulse_start_time * RATE)
    else:
        print_log(f"   [STEP D] Warning: Could not find rhythmic lock. Using a default 5-second buffer.")
        runout_start = int((drop_time_sec + 5) * RATE)

    runout_data = trans_data[runout_start:]
    if len(runout_data) == 0:
        runout_data = trans_data[-8192:]

    runout_rms_arr_raw = chunked_rms(runout_data)
    runout_rumble_max_raw = float(np.max(runout_rms_arr_raw)) if len(runout_rms_arr_raw) > 0 else 0.015

    motor_power_ceiling = max(runout_rumble_max_raw * 1.5, motor_power_threshold * 2.0)
    print_log(f"   [EXTRACTED] Motor Power Ceiling: {motor_power_ceiling:.6f}")

    # Set rumble threshold properly for vinyl_guardian.py
    rumble_threshold = round(float((baseline_median + motor_power_threshold) / 2.0), 5)

    thresholds = {
        "SILENCE_GATE_RMS": round(baseline_median * 1.5, 5), # Kept purely for the JSON config so vinyl_guardian old.py doesn't crash, but completely removed from Power logic.
        "rumble_threshold": rumble_threshold,
        "motor_power_threshold": round(motor_power_threshold, 5),
        "motor_power_ceiling": round(motor_power_ceiling, 5),
        "music_threshold": round(music_threshold, 5),
        "runout_crest_threshold": round(pop_crest_threshold, 3),
        "pop_amplitude_threshold": round(pop_amplitude_threshold, 6),
        "max_room_transient": round(max_room_transient, 5),
        "motor_hfer_threshold": round(motor_hfer_threshold, 5),
        "is_silent_hw": is_silent_hw
    }
    
    # Visual Report Card
    print_log("\n" + "="*70)
    print_log("📜 THE DUAL-SENSOR ACID TEST (Timeline Simulation)")
    print_log("   The simulator will now run all files to ensure both the Power")
    print_log("   and Vinyl Status sensors resolve to the correct state simultaneously.")
    print_log("="*70)

    def states_in_order(trans, *expected_statuses):
        last_idx = -1
        for s in expected_statuses:
            found = False
            for i, t in enumerate(trans):
                if i > last_idx and f"Status [{s}]" in t:
                    last_idx = i
                    found = True
                    break
            if not found:
                return False
        return True

    def any_bad_status(trans, *bad_statuses):
        return any(f"Status [{s}]" in t for t in trans for s in bad_statuses)

    print_log("\n[TEST 1: FILE 1 - BASELINE NOISE]")
    print_log("   Expected Flow: Off -> Stays Off")
    trans, end_p, end_s = simulate_timeline(floor_1_data, thresholds, "Off", "Powered Off")
    for t in trans: print_log(t)
    passed = (end_p == "Off" and end_s == "Powered Off" and len(trans) == 1)
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — The silence floor is too high.")

    expected_p = "Off" if thresholds["is_silent_hw"] else "On"
    expected_s = "Powered Off" if thresholds["is_silent_hw"] else "Motor Idle"
    
    print_log(f"\n[TEST 2: FILE 2 - MOTOR HUM]")
    print_log(f"   Expected Flow: Off -> User turns motor ON -> {expected_p} / {expected_s}")
    trans, end_p, end_s = simulate_timeline(spinup_data, thresholds, "Off", "Powered Off")
    for t in trans: print_log(t)
    passed = (end_p == expected_p and end_s == expected_s and not any_bad_status(trans, "Playing", "Runout Groove"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Motor threshold misaligned or false trigger.")

    print_log(f"\n[TEST 3: FILE 3 - THE MASTER TRANSITION]")
    print_log(f"   Expected Flow: Needle Drops -> Playing -> Dead Wax Bridge -> Runout Groove")
    trans, end_p, end_s = simulate_timeline(trans_data, thresholds, expected_p, expected_s)
    for t in trans: print_log(t)
    passed = (end_p == "On" and end_s == "Runout Groove" and states_in_order(trans, "Playing", "Runout Groove"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Engine lost track of music or failed rhythm lock.")

    lift_data = load_wav(files["lift"])
    print_log(f"\n[TEST 4: FILE 4 - NEEDLE LIFT]")
    print_log(f"   Expected Flow: Runout Groove -> User lifts needle -> {expected_p} / {expected_s}")
    trans, end_p, end_s = simulate_timeline(lift_data, thresholds, "On", "Runout Groove")
    for t in trans: print_log(t)
    passed = (end_p == expected_p and end_s == expected_s and not any_bad_status(trans, "Playing"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Thump was falsely flagged as music.")

    powerdown_data = load_wav(files["powerdown"])
    print_log(f"\n[TEST 5: FILE 5 - POWER DOWN]")
    print_log(f"   Expected Flow: {expected_s} -> User turns power OFF -> Off / Powered Off")
    trans, end_p, end_s = simulate_timeline(powerdown_data, thresholds, expected_p, expected_s)
    for t in trans: print_log(t)
    passed = (end_p == "Off" and end_s == "Powered Off" and not any_bad_status(trans, "Playing", "Runout Groove"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Electrical pop triggered false states.")

    print_log("\n[TEST 6: FILE 6 - ROOM NOISE]")
    print_log("   Expected Flow: Off -> User talks/taps -> Stays Off")
    trans, end_p, end_s = simulate_timeline(disturb_data, thresholds, "Off", "Powered Off")
    for t in trans: print_log(t)
    passed = (end_p == "Off" and end_s == "Powered Off" and not any_bad_status(trans, "Playing", "Runout Groove", "Motor Idle"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Acoustic shield breached by transients.")

    return thresholds

# --- MAIN EXECUTION ---
def run_calibration():
    print(r"""
    __      ___             _    ____                     _ _          
    \ \    / (_)           | |  / __ \                   | (_)         
     \ \  / / _ _ __  _   _| | | |  | |_   _  __ _ _ __  | |_  __ _ _ __ 
      \ \/ / | | '_ \| | | | | | |  | | | | |/ _` | '_ \ | | |/ _` | '_ \
       \  /  | | | | | |_| | | | |__| | |_| | (_| | | | || | | (_| | | | |
        \/   |_|_| |_|\__, |_|  \____/ \__,_|\__,_|_| |_|__|_|\__,_|_| |_|
                       __/ |                                              
                      |___/   CALIBRATION SUITE v4.0 (Acoustic Shield)                      
    """, flush=True)
    
    FILES = {
        "floor": os.path.join(CALIB_DIR, "calib_off_floor.wav"),
        "spinup": os.path.join(CALIB_DIR, "calib_spin_up.wav"),
        "transition": os.path.join(CALIB_DIR, "calib_music_to_runout.wav"),
        "lift": os.path.join(CALIB_DIR, "calib_needle_lift.wav"),
        "powerdown": os.path.join(CALIB_DIR, "calib_power_down.wav"),
        "disturbance": os.path.join(CALIB_DIR, "calib_disturbance.wav")
    }

    if not REUSE_CALIB_OPT:
        print_log("\n🧹 REUSE_CALIBRATION_AUDIO is OFF. Clearing old data...")
        if os.path.exists(CALIB_DIR): shutil.rmtree(CALIB_DIR)
        os.makedirs(CALIB_DIR)
        use_existing = False
    else:
        if all(os.path.exists(f) for f in FILES.values()):
            print_log("\n📁 REUSE_CALIBRATION_AUDIO is ON. Reusing existing recordings.")
            use_existing = True
        else:
            print_log("\n⚠️  REUSE_CALIBRATION_AUDIO is ON, but files are missing. Starting fresh recordings...")
            if not os.path.exists(CALIB_DIR): os.makedirs(CALIB_DIR)
            use_existing = False
            
    if not use_existing:
        final_mic_vol = gain_staging()
        
        record_segmented_file(FILES["floor"], 0, 0, 30, 
            "[FILE 1/6: THE BASELINE]\n🔌 Turntable: OFF\n🤫 Action: Stay quiet.")
        
        record_segmented_file(FILES["spinup"], 10, 10, 15, 
            "[FILE 2/6: THE MOTOR HUM]\n🟢 Action: Turn turntable power ON.")
        
        record_dynamic_transition(FILES["transition"])
        
        record_segmented_file(FILES["lift"], 10, 5, 15, 
            "[FILE 4/6: THE PHYSICAL THUMP]\n⬆️  Action: LIFT the tonearm with the cue lever.")
        
        record_segmented_file(FILES["powerdown"], 10, 10, 15, 
            "[FILE 5/6: THE ELECTRICAL POP]\n🔴 Action: Turn the turntable power OFF.")
        
        record_segmented_file(FILES["disturbance"], 0, 0, 30, 
            "[FILE 6/6: ROOM NOISE]\n🗣️  Action: Talk and tap the cabinet for 30s.")

    thresholds = calculate_hardware_thresholds(FILES)
    if not use_existing:
        thresholds["mic_volume"] = final_mic_vol
    else:
        try:
            with open(AUTO_CALIB_FILE, "r") as f:
                existing = json.load(f)
            if "mic_volume" in existing:
                thresholds["mic_volume"] = existing["mic_volume"]
        except Exception:
            pass
    
    with open(AUTO_CALIB_FILE, 'w') as f: json.dump(thresholds, f, indent=4)
    with open("config.json", 'w') as f: json.dump(thresholds, f, indent=4)
    
    with open(REPORT_FILE, 'w') as f:
        f.write("\n".join(report_log))
        
    print_log("\n" + "="*70)
    print_log("🎉 CALIBRATION COMPLETE 🎉")
    print_log("\n🔒 Final Tuned Logic Map:")
    for key, value in thresholds.items():
        print_log(f"   - {key}: {value}")
    
    print("\n📄 A copy of this report was saved to: " + REPORT_FILE, flush=True)
    print("🔄 Please disable CALIBRATION_MODE in your config and RESTART the Add-on.", flush=True)
    print("💤 Calibration engine is now idling indefinitely to prevent restart loops...", flush=True)
    
    while True: time.sleep(3600)

if __name__ == "__main__":
    run_calibration()