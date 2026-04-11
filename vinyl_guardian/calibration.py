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
import glob

# Suppress numpy warnings for clean output
warnings.filterwarnings('ignore')

from config import SHARE_DIR, AUTO_CALIB_FILE, RATE, CHANNELS, CHUNK
from audio_math import RUNOUT_RPM_INTERVALS

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
    print(msg, flush=True)
    report_log.append(msg)

# --- NATIVE MATH UTILITIES ---
def reject_outliers_mad(data, threshold=3.5):
    data = np.array(data)
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
    if len(audio_data) <= 1: return 0.0
    rms = get_rms(audio_data)
    if rms < 0.0001: return 0.0
    hf_data = audio_data[1:] - audio_data[:-1]
    hf_rms = float(np.sqrt(np.mean(np.square(hf_data))))
    return hf_rms / rms

def get_crest(audio_data):
    rms = get_rms(audio_data)
    if rms <= 0: return 1.0
    return float(np.max(np.abs(audio_data)) / rms)

def load_wav(filename):
    with wave.open(filename, 'rb') as wf:
        n_frames = wf.getnframes()
        audio_bytes = wf.readframes(n_frames)
        audio_data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if wf.getnchannels() == 2:
            audio_data = audio_data.reshape(-1, 2).mean(axis=1)
        return audio_data

def chunked_metrics(data, chunk_size=4096):
    chunks = len(data) // chunk_size
    rms_v, hfer_v, crest_v = [], [], []
    for i in range(chunks):
        c = data[i*chunk_size:(i+1)*chunk_size]
        rms_v.append(get_rms(c))
        hfer_v.append(get_hfer(c))
        crest_v.append(get_crest(c))
    return np.array(rms_v), np.array(hfer_v), np.array(crest_v)

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
def simulate_timeline(data, thresholds, initial_power, initial_status):
    chunk_size = 4096 
    chunks = len(data) // chunk_size
    current_power, current_status = initial_power, initial_status
    transitions = [f"   -> 0.0s : Power [{initial_power}] | Status [{initial_status}]"]
    
    pop_history = [] 
    rhythm_locked = (initial_status == "Runout Groove")
    last_rhythm_time, last_music_time = -10.0, -10.0
    turntable_on = (initial_power == "On")
    
    # ---------------------------------------------------------
    # THE ETERNAL FILTER: Increased from 1.0s to 3.0s
    # ---------------------------------------------------------
    power_max_score = int(RATE / chunk_size * 3.0)
    power_score = power_max_score if turntable_on else 0
    
    consecutive_music, has_played_music = 0, (initial_status != "Powered Off")
    
    VALID_RPM_INTERVALS = [(1.20, 1.46), (1.65, 1.95), (2.45, 2.85), (3.35, 3.85)]
    
    for i in range(chunks):
        chunk = data[i*chunk_size : (i+1)*chunk_size]
        raw_rms = get_rms(chunk)
        music_rms = get_music_rms(chunk)
        hfer = get_hfer(chunk)
        crest = get_crest(chunk)
        max_val = np.max(np.abs(chunk))
        time_sec = (i * chunk_size) / RATE
        
        is_dust_pop = False
        if raw_rms > 0:
            if (crest >= thresholds["runout_crest_threshold"] and 
                max_val >= thresholds["pop_amplitude_threshold"] and 
                raw_rms <= thresholds["motor_power_ceiling"]):
                is_dust_pop = True
                
        if music_rms > thresholds["music_threshold"] and not is_dust_pop:
            last_music_time = time_sec
            consecutive_music += 1
        else:
            consecutive_music = 0
            
        is_playing = (consecutive_music >= 3)
        if is_playing: has_played_music, rhythm_locked = True, False

        if is_dust_pop:
            pop_history.append(time_sec)
            if len(pop_history) > 15: pop_history.pop(0)
            match_count = 0
            for p in pop_history[:-1]:
                delta = time_sec - p
                for lo, hi in VALID_RPM_INTERVALS:
                    if lo <= delta <= hi:
                        match_count += 1
                        break
            if match_count >= 1 and has_played_music:
                rhythm_locked = True
                last_rhythm_time = time_sec

        if rhythm_locked and (time_sec - last_rhythm_time > 6.0): rhythm_locked = False
        
        continuous_silence = time_sec - last_music_time

        in_rms_win = thresholds["rms_min"] <= raw_rms <= thresholds["rms_max"]
        in_hfer_win = thresholds["hfer_min"] <= hfer <= thresholds["hfer_max"]
        in_crest_win = thresholds["crest_min"] <= crest <= thresholds["crest_max"]
        
        motor_on_cond = (in_rms_win and in_hfer_win and in_crest_win)

        if has_played_music or rhythm_locked: motor_on_cond = True
        if thresholds["is_silent_hw"] and (has_played_music or rhythm_locked): motor_on_cond = True

        if motor_on_cond:
            power_score = min(power_score + 1, power_max_score)
            if power_score >= power_max_score: turntable_on = True
        else:
            power_score = max(power_score - 1, 0)
            if power_score <= 0:
                turntable_on, has_played_music, rhythm_locked = False, False, False

        p_state = "On" if turntable_on else "Off"
        if not turntable_on: s_state = "Powered Off"
        elif is_playing or (has_played_music and continuous_silence < 2.0): s_state = "Playing"
        elif rhythm_locked: s_state = "Runout Groove"
        elif has_played_music:
            if continuous_silence < 15.0: s_state = "Between Tracks"
            else: s_state = "Motor Idle"; has_played_music = False
        else: s_state = "Motor Idle"
        
        if p_state != current_power or s_state != current_status:
            transitions.append(f"   -> {time_sec:.1f}s : Power [{p_state}] | Status [{s_state}]")
            current_power, current_status = p_state, s_state
            
    return transitions, current_power, current_status

def calculate_hardware_thresholds(files):
    print_log("\n" + "="*70)
    print_log("🧠 THE GUARDIAN ENGINE CALIBRATION (V6.2: ETERNAL FILTER)")
    print_log("="*70)
    
    print_log("\n[STAGE 1: BASELINE NOISE]")
    floor_data = load_wav(files["floor"])
    baseline_rms, _, _ = chunked_metrics(floor_data)
    baseline_median = float(np.median(baseline_rms))
    floor_max_amp = float(np.max(np.abs(floor_data)))
    print_log(f"   [EXTRACTED] Baseline Silence Median: {baseline_median:.6f}")

    print_log("\n[STAGE 2: MECHANICAL STABILITY PROFILING]")
    spinup_data = load_wav(files["spinup"])
    m_rms_raw, m_hfer_raw, m_crest_raw = chunked_metrics(spinup_data[20*RATE:])
    
    m_rms = reject_outliers_mad(m_rms_raw)
    m_hfer = reject_outliers_mad(m_hfer_raw)
    m_crest = reject_outliers_mad(m_crest_raw)
    
    motor_median_rms = float(np.median(m_rms))
    
    def get_percentile_bounds(arr):
        if len(arr) == 0: return 0.0, 1.0
        return float(np.percentile(arr, 5)), float(np.percentile(arr, 95))

    p_rms_min, p_rms_max = get_percentile_bounds(m_rms)
    p_hfer_min, p_hfer_max = get_percentile_bounds(m_hfer)
    p_crest_min, p_crest_max = get_percentile_bounds(m_crest)

    rms_min = p_rms_min * 0.90
    rms_max = p_rms_max * 4.0
    
    hfer_min = p_hfer_min * 0.85
    hfer_max = p_hfer_max * 1.25
    
    crest_min = p_crest_min * 0.85
    crest_max = p_crest_max * 1.50

    safe_floor = float(baseline_median * 1.5)
    if rms_min < safe_floor:
        print_log(f"   [INFO] Floor Guard Activated: Raised volume floor from {rms_min:.6f} to {safe_floor:.6f}")
        rms_min = safe_floor

    print_log(f"   [EXTRACTED] Volume Window: {rms_min:.6f} to {rms_max:.6f}")
    print_log(f"   [EXTRACTED] Pitch Window:  {hfer_min:.4f} to {hfer_max:.4f}")
    print_log(f"   [EXTRACTED] Crest Window:  {crest_min:.2f} to {crest_max:.2f}")

    is_silent_hw = (motor_median_rms <= baseline_median * 1.3)

    print_log("\n[STAGE 3: ROOM NOISE & DISTURBANCE]")
    disturb_data = load_wav(files["disturbance"])
    d_rms, _, _ = chunked_metrics(disturb_data)
    max_room_transient = float(np.max(d_rms))
    print_log(f"   [EXTRACTED] Max Ambient Transient: {max_room_transient:.6f}")

    print_log("\n[STAGE 4: THE MASTER TRANSITION]")
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
        else: drop_time_sec = 25.0
    else: drop_time_sec = trans_duration - 15.0
        
    print_log(f"   [STEP A] Detected music end: {drop_time_sec:.2f}s mark.")
    
    raw_music_chunk = trans_data[int(25*RATE) : int(drop_time_sec * RATE)]
    raw_music_rms_arr = chunked_music_rms(raw_music_chunk)
    valid_music_rms_arr = raw_music_rms_arr[raw_music_rms_arr > (baseline_median * 2.0)]
    
    raw_music_min = float(np.percentile(valid_music_rms_arr, 5)) if len(valid_music_rms_arr) > 0 else 0.005
    music_threshold = max(raw_music_min * 0.85, baseline_median * 1.5)
    print_log(f"   [EXTRACTED] Music Threshold: {music_threshold:.6f}")
    
    runout_chunks_data = trans_data[int(drop_time_sec * RATE):]
    if len(runout_chunks_data) == 0: runout_chunks_data = trans_data[-8192:]

    runout_chunks_n = len(runout_chunks_data) // 4096
    runout_crests, runout_amps = [], []

    for i in range(runout_chunks_n):
        chunk = runout_chunks_data[i*4096:(i+1)*4096]
        r = get_rms(chunk)
        if r > 0:
            m_val = np.max(np.abs(chunk))
            if m_val / r > 2.5:
                runout_crests.append(m_val / r); runout_amps.append(m_val)

    if len(runout_crests) > 0:
        base_crest = np.percentile(runout_crests, 75)
        pop_crest_threshold = max(3.5, base_crest * 0.80)
        base_amp = np.percentile(runout_amps, 75)
        pop_amplitude_threshold = max(floor_max_amp * 1.25, base_amp * 0.70)
        print_log(f"   [EXTRACTED] Runout Pop Sharpness (Crest): {pop_crest_threshold:.2f}")
    else:
        print_log("   [DEBUG] Runout extraction failed. Falling back to default tolerances.")
        pop_crest_threshold, pop_amplitude_threshold = 4.0, floor_max_amp * 1.5

    motor_power_ceiling = motor_median_rms * 4.0 

    thresholds = {
        "rms_min": round(rms_min, 6), "rms_max": round(rms_max, 6),
        "hfer_min": round(hfer_min, 5), "hfer_max": round(hfer_max, 5),
        "crest_min": round(crest_min, 3), "crest_max": round(crest_max, 3),
        "motor_power_threshold": round(rms_min, 6),
        "motor_power_ceiling": round(motor_power_ceiling, 6),
        "motor_hfer_threshold": round(hfer_max, 5),
        "motor_hfer_floor": round(hfer_min, 5),
        "music_threshold": round(music_threshold, 6),
        "runout_crest_threshold": round(pop_crest_threshold, 3),
        "pop_amplitude_threshold": round(pop_amplitude_threshold, 6),
        "max_room_transient": round(max_room_transient, 6),
        "is_silent_hw": bool(is_silent_hw)
    }
    
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

    print_log("\n" + "="*70)
    print_log("📜 THE DUAL-SENSOR ACID TEST (V6.2: ASYMMETRIC WINDOWS)")
    print_log("   Running 6-stage physical recreation to verify logic locks...")
    print_log("="*70)

    print_log("\n🔕 [TEST 1: BASELINE NOISE]")
    print_log("   Expected Flow: Off -> Stays Off")
    trans, end_p, end_s = simulate_timeline(floor_data, thresholds, "Off", "Powered Off")
    for t in trans: print_log(t)
    passed = (end_p == "Off" and end_s == "Powered Off" and len(trans) == 1)
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — The silence floor is too high.")

    expected_p = "Off" if thresholds["is_silent_hw"] else "On"
    expected_s = "Powered Off" if thresholds["is_silent_hw"] else "Motor Idle"
    
    print_log(f"\n⚙️  [TEST 2: MOTOR HUM]")
    print_log(f"   Expected Flow: Off -> User turns motor ON -> {expected_p} / {expected_s}")
    trans, end_p, end_s = simulate_timeline(spinup_data, thresholds, "Off", "Powered Off")
    for t in trans: print_log(t)
    passed = (end_p == expected_p and end_s == expected_s and not any_bad_status(trans, "Playing", "Runout Groove"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Motor threshold misaligned or false trigger.")

    print_log(f"\n🎵 [TEST 3: THE MASTER TRANSITION]")
    print_log(f"   Expected Flow: Needle Drops -> Playing -> Between Tracks -> Runout Groove")
    trans, end_p, end_s = simulate_timeline(trans_data, thresholds, expected_p, expected_s)
    for t in trans: print_log(t)
    passed = (end_p == "On" and end_s == "Runout Groove" and states_in_order(trans, "Playing", "Between Tracks", "Runout Groove"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Engine lost track of music or failed rhythm lock.")

    lift_data = load_wav(files["lift"])
    print_log(f"\n⬆️  [TEST 4: NEEDLE LIFT]")
    print_log(f"   Expected Flow: Runout Groove -> User lifts needle -> {expected_p} / {expected_s}")
    trans, end_p, end_s = simulate_timeline(lift_data, thresholds, "On", "Runout Groove")
    for t in trans: print_log(t)
    passed = (end_p == expected_p and end_s == expected_s and not any_bad_status(trans, "Playing"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Thump was falsely flagged as music.")

    powerdown_data = load_wav(files["powerdown"])
    print_log(f"\n🔌 [TEST 5: POWER DOWN]")
    print_log(f"   Expected Flow: {expected_s} -> User turns power OFF -> Off / Powered Off")
    trans, end_p, end_s = simulate_timeline(powerdown_data, thresholds, expected_p, expected_s)
    for t in trans: print_log(t)
    passed = (end_p == "Off" and end_s == "Powered Off" and not any_bad_status(trans, "Playing", "Runout Groove"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Electrical pop triggered false states.")

    print_log("\n🗣️  [TEST 6: ROOM NOISE]")
    print_log("   Expected Flow: Off -> User talks/taps -> Stays Off")
    trans, end_p, end_s = simulate_timeline(disturb_data, thresholds, "Off", "Powered Off")
    for t in trans: print_log(t)
    passed = (end_p == "Off" and end_s == "Powered Off" and not any_bad_status(trans, "Playing", "Runout Groove", "Motor Idle"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Acoustic shield breached by transients.")

    return thresholds

def analyze_ghost_triggers(thresholds):
    print_log("\n" + "="*70)
    print_log("👻 SURGICAL GHOST ANALYSIS (The Prime Suspect Filter)")
    print_log("   Scanning chunks that passed the volume filters...")
    print_log("="*70)

    ghost_files = glob.glob(os.path.join(SHARE_DIR, "ghost_trigger_*.wav"))
    if not ghost_files:
        print_log("   [INFO] No ghost trigger files found.")
        return

    for gf in sorted(ghost_files)[-5:]: 
        filename = os.path.basename(gf)
        try:
            data = load_wav(gf)
            rms_arr, hfer_arr, crest_arr = chunked_metrics(data)
            
            prime_suspects = np.where((rms_arr >= thresholds["rms_min"]) & (rms_arr <= thresholds["rms_max"]))[0]
            
            if len(prime_suspects) == 0:
                print_log(f"\n🔍 {filename} -> [VERDICT] 🟢 SAFE (Legacy Ghost)")
                print_log("   None of the audio fits your current motor volume window.")
                continue

            sus_hfer = hfer_arr[prime_suspects]
            sus_crest = crest_arr[prime_suspects]
            
            h_fail = np.logical_or(sus_hfer < thresholds["hfer_min"], sus_hfer > thresholds["hfer_max"])
            c_fail = np.logical_or(sus_crest < thresholds["crest_min"], sus_crest > thresholds["crest_max"])
            
            print_log(f"\n🔍 {filename} -> Analyzing {len(prime_suspects)} suspect chunks...")
            if np.any(h_fail) and np.any(c_fail):
                print_log("   [VERDICT] 🛑 SHIELD BREACH: Both Pitch and Texture failed.")
            elif np.any(h_fail):
                print_log(f"   [VERDICT] 🛑 PITCH BREACH: Noise pitch ({np.median(sus_hfer):.4f}) outside motor window.")
            elif np.any(c_fail):
                print_log(f"   [VERDICT] 🛑 TEXTURE BREACH: Noise texture ({np.median(sus_crest):.2f}) too wobbly.")
            else:
                print_log("   [VERDICT] 👻 PERFECT CLONE")
                print_log("   This sound successfully mimics your motor's volume, pitch, AND texture.")
        except Exception: pass

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
                      |___/   CALIBRATION SUITE v6.2 (Asymmetric Breathing Room)                      
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
        record_segmented_file(FILES["floor"], 0, 0, 30, "[FILE 1/6: THE BASELINE]")
        record_segmented_file(FILES["spinup"], 10, 10, 15, "[FILE 2/6: THE MOTOR HUM]")
        record_dynamic_transition(FILES["transition"])
        record_segmented_file(FILES["lift"], 10, 5, 15, "[FILE 4/6: THE PHYSICAL THUMP]")
        record_segmented_file(FILES["powerdown"], 10, 10, 15, "[FILE 5/6: THE ELECTRICAL POP]")
        record_segmented_file(FILES["disturbance"], 0, 0, 30, "[FILE 6/6: ROOM NOISE]")

    thresholds = calculate_hardware_thresholds(FILES)
    analyze_ghost_triggers(thresholds)
    
    if not use_existing:
        thresholds["mic_volume"] = final_mic_vol
    else:
        try:
            with open(AUTO_CALIB_FILE, "r") as f:
                existing = json.load(f)
            if "mic_volume" in existing:
                thresholds["mic_volume"] = existing["mic_volume"]
        except Exception: pass
    
    with open(AUTO_CALIB_FILE, 'w') as f: json.dump(thresholds, f, indent=4)
    with open("config.json", 'w') as f: json.dump(thresholds, f, indent=4)
    with open(REPORT_FILE, 'w') as f: f.write("\n".join(report_log))
    
    print_log("\n🎉 CALIBRATION COMPLETE 🎉")
    for key, value in thresholds.items():
        print_log(f"   - {key}: {value}")
        
    print("\n📄 A copy of this report was saved to: " + REPORT_FILE, flush=True)
    print("🔄 Please disable CALIBRATION_MODE in your config and RESTART the Add-on.", flush=True)
    while True: time.sleep(3600)

if __name__ == "__main__":
    connect_mqtt()
    if CALIBRATION_MODE: run_calibration()
    else:
        files_to_clean = [os.path.join(SHARE_DIR, "vinyl_debug.wav"), "/tmp/process.wav"]
        for f in files_to_clean:
            try:
                if os.path.exists(f): os.remove(f)
            except: pass
        listen_and_identify()