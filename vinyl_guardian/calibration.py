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

def get_crest_factor(audio_data):
    rms = get_rms(audio_data)
    if rms == 0: return 0.0
    return float(np.max(np.abs(audio_data)) / rms)

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
def find_rhythmic_pulse(data, start_sec, noise_floor):
    """Initial generous pass just to locate the start of the runout groove."""
    chunk_size = 4096 
    chunks = len(data) // chunk_size
    pop_history = []
    
    for i in range(chunks):
        chunk = data[i*chunk_size : (i+1)*chunk_size]
        r = get_rms(chunk)
        max_val = np.max(np.abs(chunk))
        
        # Extremely generous threshold just to find the start point
        if r > 0 and (max_val / r >= 3.0) and (max_val > noise_floor * 1.5):
            time_sec = (i * chunk_size) / RATE
            for pt_time, pt_val in reversed(pop_history[-10:]):
                diff = time_sec - pt_time
                if (1.65 <= diff <= 1.95) or (1.20 <= diff <= 1.45):
                    # Check Amplitude Similarity (Must be the SAME physical scratch)
                    if (pt_val * 0.4) <= max_val <= (pt_val * 2.5):
                        return start_sec + time_sec
            # Store tuple of (Time, Amplitude)
            pop_history.append((time_sec, max_val))
    return None

def simulate_timeline(data, thresholds, initial_state):
    chunk_size = 4096 
    chunks = len(data) // chunk_size
    
    current_state = initial_state
    state_history = [initial_state] * 6 
    transitions = [f"   -> 0.0s : Started {initial_state}"]
    
    pop_history = []
    runout_active = False
    last_print = -10.0
    
    for i in range(chunks):
        chunk = data[i*chunk_size : (i+1)*chunk_size]
        r = get_rms(chunk)
        m = get_music_rms(chunk)
        max_val = np.max(np.abs(chunk))
        time_sec = (i * chunk_size) / RATE
        
        # 🌟 DYNAMIC, FUTURE-PROOF POP DETECTION 🌟
        is_pop = (r > 0) and (max_val / r >= thresholds["pop_crest_threshold"]) and (max_val >= thresholds["pop_amplitude_threshold"])
        is_music = m >= thresholds["music_threshold"]
        
        # STRICT STATE HIERARCHY
        if is_music:
            s = "MUSIC"
            pop_history.clear() # Music stops the pop detector so drums don't confuse it
            runout_active = False
        else:
            # Not music. Is it a pop?
            if is_pop:
                for pt_time, pt_val in reversed(pop_history[-10:]):
                    diff = time_sec - pt_time
                    # 1. Matches RPM Timing?
                    if (1.65 <= diff <= 1.95) or (1.20 <= diff <= 1.45):
                        # 2. Matches physical amplitude of previous pop?
                        if (pt_val * 0.4) <= max_val <= (pt_val * 2.5):
                            runout_active = True
                            if time_sec - last_print > 6.0:
                                transitions.append(f"   -> {time_sec:.1f}s : 🔄 33/45 RPM Rhythmic Pulse Detected")
                                last_print = time_sec
                            break
                # Always append the tuple (Time, Amplitude)
                pop_history.append((time_sec, max_val))
                
            # If no pop is detected for 4.0 seconds, drop the runout tracking
            if len(pop_history) > 0 and (time_sec - pop_history[-1][0] > 4.0):
                runout_active = False 
                
            # Resolve State
            if runout_active:
                s = "RUNOUT"
            elif thresholds["is_silent_hw"]:
                s = "OFF" # Motor hum is invisible, default to OFF if no runout groove detected
            elif r < thresholds["SILENCE_GATE_RMS"]:
                s = "OFF"
            elif r >= thresholds["motor_power_threshold"]:
                s = "IDLE"
            else:
                s = "OFF"
        
        state_history.append(s)
        state_history.pop(0)
        
        latest = state_history[-1]
        # Require 4 of the last 6 chunks to agree (0.54s debounce)
        if state_history.count(latest) >= 4:
            if latest != current_state:
                transitions.append(f"   -> {time_sec:.1f}s : Switched to {latest}")
                current_state = latest
                
    return transitions, current_state

def analyze_and_simulate(files):
    print_log("\n" + "="*50)
    print_log("🧠 CALCULATING HARDWARE THRESHOLDS")
    print_log("="*50)
    
    floor_1_data = load_wav(files["floor"])
    powerdown_data = load_wav(files["powerdown"])
    combined_floor = np.concatenate((chunked_rms(floor_1_data), chunked_rms(powerdown_data[20*RATE:])))
    baseline_noise_max = np.max(reject_outliers_mad(combined_floor))
    
    spinup_data = load_wav(files["spinup"])
    lift_data = load_wav(files["lift"])
    combined_idle = np.concatenate((chunked_rms(spinup_data[20*RATE:]), chunked_rms(lift_data[15*RATE:])))
    motor_hum_median = np.median(reject_outliers_mad(combined_idle))
    
    trans_data = load_wav(files["transition"])
    trans_duration = len(trans_data) / RATE
    print_log(f"   [DATA] File 3 Total Duration: {trans_duration:.2f} seconds")
    
    print_log("📉 Scanning transition file for true end of music...")
    
    search_start_idx = int(25 * RATE / 8192) 
    trans_m_rms = chunked_music_rms(trans_data, chunk_size=8192)
    
    if len(trans_m_rms) > search_start_idx:
        search_arr = trans_m_rms[search_start_idx:]
        peak_music = np.max(search_arr)
        threshold = max(peak_music * 0.15, 0.002)
        
        active_indices = np.where(search_arr > threshold)[0]
        if len(active_indices) > 0:
            last_active = active_indices[-1]
            drop_time_sec = 25.0 + (last_active * 8192 / RATE)
        else:
            drop_time_sec = 25.0
    else:
        drop_time_sec = trans_duration - 15.0
        
    print_log(f"   [DATA] True music end calculated at: {drop_time_sec:.2f}s")
    
    pulse_start_time = find_rhythmic_pulse(trans_data[int(drop_time_sec * RATE):], drop_time_sec, baseline_noise_max)
    
    if pulse_start_time:
        print_log(f"   [DATA] 🎯 Confirmed rhythmic 33/45 RPM pulsing begins at: {pulse_start_time:.2f}s")
        runout_start = int(pulse_start_time * RATE)
    else:
        print_log(f"   [DATA] ⚠️ Could not find exact runout pulse. Using default 5s buffer.")
        runout_start = int((drop_time_sec + 5) * RATE)
        
    runout_data = trans_data[runout_start:]
    if len(runout_data) == 0: runout_data = trans_data[-8192:] 
    
    # 🌟 NEW DYNAMIC POP EXTRACTION 🌟
    runout_chunks = len(runout_data) // 4096
    runout_crests = []
    runout_amps = []
    
    for i in range(runout_chunks):
        chunk = runout_data[i*4096:(i+1)*4096]
        r = get_rms(chunk)
        if r > 0:
            m_val = np.max(np.abs(chunk))
            runout_crests.append(m_val / r)
            runout_amps.append(m_val)
            
    if len(runout_crests) > 0:
        base_crest = np.percentile(runout_crests, 90)
        pop_crest_threshold = max(3.0, base_crest * 0.70)
        
        base_amp = np.percentile(runout_amps, 90)
        pop_amplitude_threshold = max(baseline_noise_max * 1.5, base_amp * 0.70)
        print_log(f"   [DATA] 📐 Pop Profile Extracted: Crest={base_crest:.2f} (Threshold: {pop_crest_threshold:.2f})")
    else:
        print_log("   [DEBUG] 🚨 Warning: Runout extraction failed. Falling back to default tolerances.")
        pop_crest_threshold = 3.5
        pop_amplitude_threshold = baseline_noise_max * 2.0
    
    # Extract Music Floor
    raw_music_chunk = trans_data[:int(drop_time_sec * RATE)]
    raw_music_rms_arr = chunked_music_rms(raw_music_chunk)
    valid_music_rms_arr = raw_music_rms_arr[raw_music_rms_arr > (baseline_noise_max * 2.0)]
    
    if len(valid_music_rms_arr) > 0:
        music_min = float(np.percentile(valid_music_rms_arr, 5))
    else:
        music_min = 0.005
    
    runout_rms_arr = chunked_rms(runout_data)
    runout_rumble_max = float(np.max(reject_outliers_mad(runout_rms_arr))) if len(runout_rms_arr) > 0 else 0.015
    
    disturb_data = load_wav(files["disturbance"])

    # Intelligent Hardware Profiling
    silence_gate = float(baseline_noise_max * 1.15)
    if motor_hum_median <= baseline_noise_max * 1.4:
        is_silent_hw = True
        motor_power_threshold = float(silence_gate * 1.5) 
        print_log("   [INFO] 🔕 Hardware is extremely quiet. Enabling Silent Hardware Mode.")
    else:
        is_silent_hw = False
        motor_power_threshold = float((baseline_noise_max * 1.15 + motor_hum_median) / 2.0)

    thresholds = {
        "SILENCE_GATE_RMS": round(silence_gate, 5),
        "motor_power_threshold": round(motor_power_threshold, 5),
        "motor_power_ceiling": round(float(runout_rumble_max * 1.3), 5),
        "music_threshold": round(float(music_min * 0.85), 5),
        "pop_crest_threshold": round(float(pop_crest_threshold), 3),
        "pop_amplitude_threshold": round(float(pop_amplitude_threshold), 6),
        "is_silent_hw": is_silent_hw
    }

    # Simulation & Nudging Loop
    print_log("\n🧪 STARTING SIMULATION & PERTURBATION ENGINE")
    for attempt in range(6):
        print_log(f"\n--- Internal Simulation Pass {attempt + 1} ---")
        all_passed = True
        
        _, end1 = simulate_timeline(floor_1_data, thresholds, "OFF")
        if end1 != "OFF":
            print_log("   ⚠️ File 1 failed to hold OFF. Raising Silence Gate.")
            thresholds["SILENCE_GATE_RMS"] *= 1.10
            thresholds["pop_amplitude_threshold"] *= 1.10 
            all_passed = False

        if not thresholds["is_silent_hw"]:
            _, end2 = simulate_timeline(spinup_data, thresholds, "OFF")
            if end2 != "IDLE":
                new_motor = thresholds["motor_power_threshold"] * 0.90
                if new_motor <= thresholds["SILENCE_GATE_RMS"] * 1.05:
                    print_log("   ⚠️ Motor hum hit noise floor limit. Engaging Silent Hardware Mode.")
                    thresholds["is_silent_hw"] = True
                else:
                    print_log("   ⚠️ File 2 failed to catch Motor Hum. Lowering Motor Threshold.")
                    thresholds["motor_power_threshold"] = new_motor
                all_passed = False

        expected_file3_start = "OFF" if thresholds["is_silent_hw"] else "IDLE"
        trans3, end3 = simulate_timeline(trans_data, thresholds, expected_file3_start)
        
        if not any("MUSIC" in t for t in trans3):
            print_log("   ⚠️ File 3 failed to trigger MUSIC at all. Lowering Music Threshold.")
            thresholds["music_threshold"] *= 0.85
            all_passed = False
        elif len(trans3) > 8:
            print_log(f"   ⚠️ File 3 state is bouncing! ({len(trans3)} state changes). Lowering Music Threshold to stabilize state.")
            thresholds["music_threshold"] *= 0.85
            all_passed = False
        elif end3 == "MUSIC":
            print_log("   ⚠️ File 3 failed to exit MUSIC. Raising Music Threshold.")
            thresholds["music_threshold"] *= 1.15
            all_passed = False
            
        trans6, end6 = simulate_timeline(disturb_data, thresholds, "OFF")
        if any("MUSIC" in t for t in trans6):
            print_log("   ⚠️ File 6 falsely triggered MUSIC from room noise. Raising Music Threshold.")
            thresholds["music_threshold"] *= 1.15
            all_passed = False
        elif any("RUNOUT" in t for t in trans6):
            print_log("   ⚠️ File 6 falsely triggered RUNOUT pops from room noise. Raising Pop Sensitivity.")
            thresholds["pop_crest_threshold"] *= 1.10
            thresholds["pop_amplitude_threshold"] *= 1.15
            all_passed = False

        if all_passed:
            print_log("   ✅ All logic verified. Locking Thresholds.")
            break

    # Visual Report Card
    print_log("\n" + "="*50)
    print_log("📜 FINAL TIMELINE VERIFICATION (The Report Card)")
    print_log("="*50)
    
    print_log("\n[FILE 1: BASELINE] Expected: Stays OFF")
    trans, end = simulate_timeline(floor_1_data, thresholds, "OFF")
    for t in trans: print_log(t)
    print_log("   ✅ PASS" if end == "OFF" and len(trans) == 1 else "   ❌ FAIL")

    expected_file2 = "OFF" if thresholds["is_silent_hw"] else "IDLE"
    print_log(f"\n[FILE 2: MOTOR HUM] Expected: OFF -> Action Window -> {expected_file2}")
    trans, end = simulate_timeline(spinup_data, thresholds, "OFF")
    for t in trans: print_log(t)
    print_log("   ✅ PASS" if end == expected_file2 else "   ❌ FAIL")

    expected_file3 = "OFF" if thresholds["is_silent_hw"] else "IDLE"
    print_log(f"\n[FILE 3: MASTER TRANSITION] Expected: {expected_file3} -> MUSIC -> RUNOUT")
    trans, end = simulate_timeline(trans_data, thresholds, expected_file3)
    for t in trans: print_log(t)
    print_log("   ✅ PASS" if "MUSIC" in str(trans) and "RUNOUT" in str(trans) and end == "RUNOUT" and len(trans) <= 8 else "   ❌ FAIL")
    
    print_log(f"\n[FILE 4: NEEDLE LIFT] Expected: {expected_file2} -> Action Thump -> {expected_file2}")
    trans, end = simulate_timeline(lift_data, thresholds, expected_file2)
    for t in trans: print_log(t)
    print_log("   ✅ PASS" if end == expected_file2 and not any("MUSIC" in t for t in trans) else "   ❌ FAIL")

    print_log("\n[FILE 5: POWER DOWN] Expected: IDLE -> Action Window -> OFF")
    trans, end = simulate_timeline(powerdown_data, thresholds, expected_file3)
    for t in trans: print_log(t)
    print_log("   ✅ PASS" if end == "OFF" else "   ❌ FAIL")
    
    print_log("\n[FILE 6: ROOM NOISE] Expected: Stays OFF (No Runout or Music Triggers)")
    trans, end = simulate_timeline(disturb_data, thresholds, "OFF")
    for t in trans: print_log(t)
    print_log("   ✅ PASS" if end == "OFF" and not any("MUSIC" in t for t in trans) and not any("RUNOUT" in t for t in trans) else "   ❌ FAIL")

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
                      |___/   CALIBRATION SUITE v3.0                      
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

    thresholds = analyze_and_simulate(FILES)
    if not use_existing: thresholds["MIC_VOLUME"] = final_mic_vol
    
    with open(AUTO_CALIB_FILE, 'w') as f: json.dump(thresholds, f, indent=4)
    with open("config.json", 'w') as f: json.dump(thresholds, f, indent=4)
    
    with open(REPORT_FILE, 'w') as f:
        f.write("\n".join(report_log))
        
    print_log("\n" + "="*50)
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