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

# Import standard settings from your existing config
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
    """
    Chronological Dual-Sensor Engine.
    Implements Safe Frequency Window (HFER Floor & Ceiling) and Priority Override.
    """
    chunk_size = 4096 
    chunks = len(data) // chunk_size
    
    current_power = initial_power
    current_status = initial_status
    transitions = [f"   -> 0.0s : Power [{initial_power}] | Status [{initial_status}]"]
    
    pop_history = [] 
    rhythm_locked = (initial_status == "Runout Groove")
    last_rhythm_time = -10.0
    
    turntable_on = (initial_power == "On")
    power_max_score = int(RATE / chunk_size * 1.0) 
    power_score = power_max_score if turntable_on else 0
    
    consecutive_music = 0
    has_played_music = (initial_status in ["Playing", "Between Tracks", "Runout Groove"])
    last_music_time = -10.0
    
    power_history = [initial_power] * 6
    status_history = [initial_status] * 6
    
    VALID_RPM_INTERVALS = [(1.20, 1.46), (1.65, 1.95), (2.45, 2.85), (3.35, 3.85)]
    
    for i in range(chunks):
        chunk = data[i*chunk_size : (i+1)*chunk_size]
        raw_rms = get_rms(chunk)
        music_rms = get_music_rms(chunk)
        hfer = get_hfer(chunk)
        max_val = np.max(np.abs(chunk))
        time_sec = (i * chunk_size) / RATE
        
        is_dust_pop = False
        if raw_rms > 0:
            crest = max_val / raw_rms
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
        if is_playing:
            has_played_music = True
            rhythm_locked = False

        if is_dust_pop:
            pop_history.append(time_sec)
            if len(pop_history) > 15:
                pop_history.pop(0)
            
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

        if rhythm_locked and (time_sec - last_rhythm_time > 6.0):
            rhythm_locked = False
            
        continuous_silence = time_sec - last_music_time

        # --- POWER HYSTERESIS ---
        motor_on_cond = raw_rms > thresholds["motor_power_threshold"]
        upper_limit = max(thresholds["motor_power_threshold"] * 4.5, thresholds["max_room_transient"] * 1.2)
        
        # 1. Gatekeeper Shields (Active only when idle)
        # Check both Floor and Ceiling
        hfer_ceil = thresholds.get("motor_hfer_threshold", 9.0)
        hfer_floor = thresholds.get("motor_hfer_floor", 0.0)
        
        if hfer_ceil > 0.0 and raw_rms < upper_limit:
            if hfer > hfer_ceil or hfer < hfer_floor:
                motor_on_cond = False

        if not turntable_on and not has_played_music:
            if raw_rms > upper_limit:
                motor_on_cond = False

        # 2. Absolute Override
        if has_played_music or rhythm_locked:
            motor_on_cond = True

        if motor_on_cond:
            power_score = min(power_score + 1, power_max_score)
            if power_score >= power_max_score:
                turntable_on = True
        else:
            power_score = max(power_score - 1, 0)
            if power_score <= 0:
                turntable_on = False
                has_played_music = False
                rhythm_locked = False

        # --- VINYL STATUS RESOLUTION ---
        if not turntable_on:
            s_state = "Powered Off"
        elif is_playing or (has_played_music and continuous_silence < 2.0):
            s_state = "Playing"
        elif rhythm_locked:
            s_state = "Runout Groove"
        elif has_played_music:
            if continuous_silence < 15.0:  
                s_state = "Between Tracks"
            else:
                s_state = "Motor Idle"
                has_played_music = False
        else:
            s_state = "Motor Idle"
            
        p_state = "On" if turntable_on else "Off"
        
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
    print_log("🧠 THE GUARDIAN ENGINE CALIBRATION (V5: SAFE WINDOW)")
    print_log("="*70)
    
    # --- 1. FILE 1: BASELINE NOISE ---
    print_log("\n[STAGE 1: FILE 1 - BASELINE NOISE]")
    floor_1_data = load_wav(files["floor"])
    baseline_rms_arr = reject_outliers_mad(chunked_rms(floor_1_data))
    baseline_median = float(np.median(baseline_rms_arr))
    floor_max_amp = float(np.max(np.abs(floor_1_data))) 
    print_log(f"   [EXTRACTED] Baseline Silence Median: {baseline_median:.6f}")

    # --- 2. FILE 2: MOTOR HUM ---
    print_log("\n[STAGE 2: FILE 2 - MOTOR HUM]")
    spinup_data = load_wav(files["spinup"])
    motor_rms_arr = reject_outliers_mad(chunked_rms(spinup_data[20*RATE:]))
    motor_median = float(np.median(motor_rms_arr))
    
    if motor_median <= baseline_median * 1.3:
        is_silent_hw = True
        motor_power_threshold = baseline_median * 1.5 
    else:
        is_silent_hw = False
        motor_power_threshold = float((baseline_median + motor_median) / 2.0)
    
    print_log(f"   [EXTRACTED] Motor Power Threshold: {motor_power_threshold:.6f}")

    # --- 3. FILE 6: DISTURBANCE ---
    print_log("\n[STAGE 3: FILE 6 - ROOM NOISE & DISTURBANCE]")
    disturb_data = load_wav(files["disturbance"])
    max_room_transient = float(np.max(chunked_rms(disturb_data))) if len(disturb_data) > 0 else 0.01
    
    # SAFE FREQUENCY WINDOW EXTRACTION
    motor_hfer_arr = chunked_hfer(spinup_data[20*RATE:])
    clean_motor_hfer = reject_outliers_mad(motor_hfer_arr)
    
    if len(clean_motor_hfer) > 0:
        peak_motor_hfer = float(np.max(clean_motor_hfer))
        min_motor_hfer = float(np.min(clean_motor_hfer))
        
        motor_hfer_threshold = peak_motor_hfer * 1.05
        motor_hfer_floor = min_motor_hfer * 0.95
        
        print_log(f"   [EXTRACTED] Safe Frequency Window: {motor_hfer_floor:.4f} to {motor_hfer_threshold:.4f}")
        print_log(f"               (Based on physical motor range {min_motor_hfer:.4f} - {peak_motor_hfer:.4f})")
    else:
        motor_hfer_threshold = 0.0
        motor_hfer_floor = 0.0
        print_log("   [INFO] Frequency extraction failed. Reverting to RMS only.")
        
    disturb_music_max = float(np.max(chunked_music_rms(disturb_data)))
    print_log(f"   [EXTRACTED] Max Ambient Transient: {max_room_transient:.6f}")

    # --- 4. FILE 3: MASTER TRANSITION ---
    print_log("\n[STAGE 4: FILE 3 - THE MASTER TRANSITION]")
    trans_data = load_wav(files["transition"])
    trans_m_rms = chunked_music_rms(trans_data, chunk_size=8192)
    peak_music = np.max(trans_m_rms[int(25*RATE/8192):])
    music_threshold = max(peak_music * 0.15, baseline_median * 1.5)
    print_log(f"   [EXTRACTED] Music Threshold: {music_threshold:.6f}")
    
    # ... (Pop Extraction remains the same)
    pop_crest_threshold = 3.5 
    pop_amplitude_threshold = floor_max_amp * 1.25

    motor_power_ceiling = motor_median * 3.0 # Simplified for logic lock

    thresholds = {
        "rumble_threshold": round(float((baseline_median + motor_power_threshold) / 2.0), 5),
        "motor_power_threshold": round(motor_power_threshold, 5),
        "motor_power_ceiling": round(motor_power_ceiling, 5),
        "music_threshold": round(music_threshold, 5),
        "runout_crest_threshold": round(pop_crest_threshold, 3),
        "pop_amplitude_threshold": round(pop_amplitude_threshold, 6),
        "max_room_transient": round(max_room_transient, 5),
        "motor_hfer_threshold": round(motor_hfer_threshold, 5),
        "motor_hfer_floor": round(motor_hfer_floor, 5),
        "is_silent_hw": is_silent_hw
    }
    
    # Simulator Test Suite
    print_log("\n" + "="*70)
    print_log("📜 THE DUAL-SENSOR ACID TEST (V5: SAFE WINDOW)")
    print_log("="*70)

    print_log("\n[TEST 1: BASELINE]")
    trans, end_p, end_s = simulate_timeline(floor_1_data, thresholds, "Off", "Powered Off")
    for t in trans: print_log(t)
    
    print_log(f"\n[TEST 2: MOTOR HUM]")
    trans, end_p, end_s = simulate_timeline(spinup_data, thresholds, "Off", "Powered Off")
    for t in trans: print_log(t)

    print_log(f"\n[TEST 3: MASTER TRANSITION]")
    trans, end_p, end_s = simulate_timeline(trans_data, thresholds, "On", "Motor Idle")
    for t in trans: print_log(t)

    return thresholds

def analyze_ghost_triggers(thresholds):
    print_log("\n" + "="*70)
    print_log("👻 GHOST TRIGGER POST-MORTEM ANALYSIS")
    print_log("="*70)

    ghost_files = glob.glob(os.path.join(SHARE_DIR, "ghost_trigger_*.wav"))
    if not ghost_files: return

    h_ceil = thresholds.get("motor_hfer_threshold", 9.0)
    h_floor = thresholds.get("motor_hfer_floor", 0.0)

    for gf in sorted(ghost_files)[-5:]: 
        filename = os.path.basename(gf)
        try:
            data = load_wav(gf)
            rms = float(np.max(chunked_rms(data)))
            hfer = float(np.median(chunked_hfer(data)))
            print_log(f"\n🔍 {filename} -> RMS: {rms:.6f}, HFER: {hfer:.4f}")
            
            if hfer < h_floor:
                print_log("   [VERDICT] 🐋 DEEP-DIVING GHOST: Sound is lower-pitched than motor.")
            elif hfer > h_ceil:
                print_log("   [VERDICT] 🗣️ SHARP GHOST: Sound is higher-pitched than motor.")
            else:
                print_log("   [VERDICT] 👻 PERFECT CLONE: Sits inside your Safe Window.")
        except: pass

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
                      |___/   CALIBRATION SUITE v5.0 (Safe Window)                      
    """, flush=True)
    
    FILES = {
        "floor": os.path.join(CALIB_DIR, "calib_off_floor.wav"),
        "spinup": os.path.join(CALIB_DIR, "calib_spin_up.wav"),
        "transition": os.path.join(CALIB_DIR, "calib_music_to_runout.wav"),
        "lift": os.path.join(CALIB_DIR, "calib_needle_lift.wav"),
        "powerdown": os.path.join(CALIB_DIR, "calib_power_down.wav"),
        "disturbance": os.path.join(CALIB_DIR, "calib_disturbance.wav")
    }

    if REUSE_CALIB_OPT and all(os.path.exists(f) for f in FILES.values()):
        use_existing = True
    else:
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
    
    with open(AUTO_CALIB_FILE, 'w') as f: json.dump(thresholds, f, indent=4)
    with open("config.json", 'w') as f: json.dump(thresholds, f, indent=4)
    with open(REPORT_FILE, 'w') as f: f.write("\n".join(report_log))
    
    print_log("\n🎉 CALIBRATION COMPLETE 🎉")
    while True: time.sleep(3600)

if __name__ == "__main__":
    run_calibration()