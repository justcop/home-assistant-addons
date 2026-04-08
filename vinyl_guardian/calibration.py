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
        print(f"🚨 ALSA Error: Could not open microphone -> {e}", flush=True)
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
    print(f"\n" + "-"*50, flush=True)
    print(f"{prompt}", flush=True)
    
    raw_bytes = bytearray()
    
    if action_dur > 0:
        print(f"🎬 ACTION WINDOW ({action_dur}s): Perform action NOW!", flush=True)
        chunk_b, _ = record_chunk(action_dur)
        raw_bytes.extend(chunk_b)
        
    if settle_dur > 0:
        print(f"⏳ SETTLING ({settle_dur}s): Allowing motor/reverb to stabilize...", flush=True)
        chunk_b, _ = record_chunk(settle_dur)
        raw_bytes.extend(chunk_b)
        
    if steady_dur > 0:
        print(f"⏹️  STEADY STATE ({steady_dur}s): Capturing stable background...", flush=True)
        chunk_b, _ = record_chunk(steady_dur)
        raw_bytes.extend(chunk_b)
    
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(raw_bytes)
        
    print(f"✅ Saved to {os.path.basename(filename)}", flush=True)
    time.sleep(1)

def record_dynamic_transition(filename):
    print(f"\n" + "-"*50, flush=True)
    print("[FILE 3/6: THE MASTER TRANSITION]\n🎶 ACTION: Drop needle on the LAST TRACK now.")
    print("〰️  The system will listen live for the track to end, wait for the runout groove, and capture the rumble.", flush=True)
    
    raw_bytes = bytearray()
    
    print(f"🎬 ACTION WINDOW (10s): Drop the needle NOW!", flush=True)
    chunk_b, _ = record_chunk(10.0)
    raw_bytes.extend(chunk_b)
    
    print("🎵 MUSIC PHASE: Listening for the track to naturally end...", flush=True)
    max_music_rms = 0.0
    consecutive_low = 0
    music_ended = False
    
    for i in range(360):
        chunk_b, audio = record_chunk(1.0)
        raw_bytes.extend(chunk_b)
        rms = get_rms(audio)
        
        if i < 15:
            max_music_rms = max(max_music_rms, rms)
            continue
            
        threshold = max(max_music_rms * 0.25, 0.005) 
        if rms < threshold:
            consecutive_low += 1
        else:
            consecutive_low = 0
            max_music_rms = max(max_music_rms, rms) 
            
        if consecutive_low >= 5: 
            print(f"📉 MUSIC DROP-OFF DETECTED! (Silence hit at {i+10}s)", flush=True)
            music_ended = True
            break
            
    if not music_ended:
        print("⚠️ Fail-safe reached. Max 6 minutes recorded without detecting end of song.", flush=True)
        
    print("⏳ TRANSIT PHASE (20s): Waiting for needle to firmly reach runout groove...", flush=True)
    chunk_b, _ = record_chunk(20.0)
    raw_bytes.extend(chunk_b)
    
    print("⏺️ STEADY STATE (20s): Capturing pure runout rumble and surface noise...", flush=True)
    chunk_b, _ = record_chunk(20.0)
    raw_bytes.extend(chunk_b)
    
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(raw_bytes)
        
    print(f"✅ Saved dynamic transition to {os.path.basename(filename)}", flush=True)
    time.sleep(1)

# --- STEP 0: AUTOMATED GAIN STAGING ---
def set_mic_volume(vol_pct):
    try: subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{vol_pct}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except: pass

def gain_staging():
    print("\n" + "="*50, flush=True)
    print("🎚️  STEP 0: AUTO-CALIBRATING SOFTWARE VOLUME", flush=True)
    print("="*50, flush=True)
    print("🔊 ACTION: Find the LOUDEST record you own and drop the needle NOW.", flush=True)
    print("   Searching for 1% precision sweet spot...", flush=True)
    
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
            print(f"   Peak {peak:.2f} (Hot) -> Vol: {current_vol}%", flush=True)
        elif peak < 0.50:
            if last_direction == -1: step = max(1, step // 2)
            last_direction = 1
            current_vol = min(100, current_vol + step)
            set_mic_volume(current_vol)
            print(f"   Peak {peak:.2f} (Low) -> Vol: {current_vol}%", flush=True)
        else:
            print(f"   Peak {peak:.2f} (Testing...) -> Verifying {current_vol}% for 10s...", flush=True)
            _, v_data = record_chunk(10.0)
            v_peak = np.max(np.abs(v_data))
            if v_peak > 0.85:
                current_vol -= 1
                set_mic_volume(current_vol)
                continue
            print(f"✅ VOLUME LOCKED at {current_vol}%", flush=True)
            break
            
    print("\n⏹️  ACTION: Stop the record and turn the turntable OFF completely.", flush=True)
    time.sleep(5)
    return current_vol

# --- SIMULATION & TIMELINE ENGINE ---
def simulate_timeline(data, thresholds, initial_state):
    """Miniature State Machine to chronologically prove the threshold logic"""
    chunk_size = 8192 # ~0.37s chunks
    chunks = len(data) // chunk_size
    
    current_state = initial_state
    state_history = [initial_state] * 4 # Require 4 chunks to agree to switch state
    transitions = [f"   -> 0.0s : Started {initial_state}"]
    
    for i in range(chunks):
        chunk = data[i*chunk_size : (i+1)*chunk_size]
        r = get_rms(chunk)
        m = get_music_rms(chunk)
        c = get_crest_factor(chunk)
        
        is_pop = c >= 3.5
        
        if r < thresholds["SILENCE_GATE_RMS"]:
            s = "OFF"
        elif m >= thresholds["music_threshold"] and not is_pop:
            s = "MUSIC"
        elif r >= thresholds["motor_power_threshold"]:
            s = "IDLE"
        else:
            s = "OFF"
            
        state_history.append(s)
        state_history.pop(0)
        
        latest = state_history[-1]
        # Debounce: 3 out of 4 chunks must agree to transition
        if state_history.count(latest) >= 3:
            if latest != current_state:
                time_sec = (i * chunk_size) / RATE
                transitions.append(f"   -> {time_sec:.1f}s : Switched to {latest}")
                current_state = latest
                
    return transitions, current_state

def analyze_and_simulate(files):
    print("\n" + "="*50, flush=True)
    print("🧠 CALCULATING HARDWARE THRESHOLDS", flush=True)
    print("="*50, flush=True)
    
    # 1. Math Extraction
    floor_1_data = load_wav(files["floor"])
    powerdown_data = load_wav(files["powerdown"])
    combined_floor = np.concatenate((chunked_rms(floor_1_data), chunked_rms(powerdown_data[20*RATE:])))
    baseline_noise_max = np.max(reject_outliers_mad(combined_floor))
    
    spinup_data = load_wav(files["spinup"])
    lift_data = load_wav(files["lift"])
    combined_idle = np.concatenate((chunked_rms(spinup_data[20*RATE:]), chunked_rms(lift_data[15*RATE:])))
    motor_hum_median = np.median(reject_outliers_mad(combined_idle))
    
    trans_data = load_wav(files["transition"])
    runout_data = trans_data[-20*RATE:] # Guaranteed by dynamic record
    music_data = trans_data[10*RATE : -40*RATE]
    
    music_rms_arr = chunked_music_rms(music_data)
    music_min = np.percentile(music_rms_arr, 5) 
    runout_rumble_max = np.max(reject_outliers_mad(chunked_rms(runout_data)))
    
    disturb_data = load_wav(files["disturbance"])

    # 2. Initial Threshold Guesses
    thresholds = {
        "SILENCE_GATE_RMS": round(float(baseline_noise_max * 1.15), 5),
        "motor_power_threshold": round(float((baseline_noise_max * 1.15 + motor_hum_median) / 2.0), 5),
        "motor_power_ceiling": round(float(runout_rumble_max * 1.3), 5),
        "music_threshold": round(float(music_min * 0.85), 5),
        "is_silent_hw": False
    }

    # 3. The Perturbation Loop (Nudge until 100% Pass)
    print("\n🧪 STARTING SIMULATION & PERTURBATION ENGINE", flush=True)
    for attempt in range(5):
        print(f"\n--- Internal Simulation Pass {attempt + 1} ---", flush=True)
        all_passed = True
        
        _, end1 = simulate_timeline(floor_1_data, thresholds, "OFF")
        if end1 != "OFF":
            print("   ⚠️ File 1 failed to hold OFF. Raising Silence Gate.", flush=True)
            thresholds["SILENCE_GATE_RMS"] *= 1.10
            all_passed = False

        _, end2 = simulate_timeline(spinup_data, thresholds, "OFF")
        if end2 != "IDLE":
            print("   ⚠️ File 2 failed to catch Motor Hum. Lowering Motor Threshold.", flush=True)
            thresholds["motor_power_threshold"] *= 0.90
            all_passed = False

        trans3, end3 = simulate_timeline(trans_data, thresholds, "IDLE")
        if not any("MUSIC" in t for t in trans3):
            print("   ⚠️ File 3 failed to trigger MUSIC. Lowering Music Threshold.", flush=True)
            thresholds["music_threshold"] *= 0.90
            all_passed = False
        elif end3 != "IDLE":
            print("   ⚠️ File 3 failed to return to IDLE in runout. Raising Music Threshold.", flush=True)
            thresholds["music_threshold"] *= 1.10
            all_passed = False
            
        trans6, end6 = simulate_timeline(disturb_data, thresholds, "OFF")
        if any("MUSIC" in t for t in trans6):
            print("   ⚠️ File 6 falsely triggered MUSIC from room noise. Raising Music Threshold.", flush=True)
            thresholds["music_threshold"] *= 1.15
            all_passed = False

        if all_passed:
            print("   ✅ All logic verified. Locking Thresholds.", flush=True)
            break

    # 4. Final Visual Report Card
    print("\n" + "="*50, flush=True)
    print("📜 FINAL TIMELINE VERIFICATION (The Report Card)", flush=True)
    print("="*50, flush=True)
    
    print("\n[FILE 1: BASELINE] Expected: Stays OFF", flush=True)
    trans, end = simulate_timeline(floor_1_data, thresholds, "OFF")
    for t in trans: print(t, flush=True)
    print("   ✅ PASS" if end == "OFF" and len(trans) == 1 else "   ❌ FAIL", flush=True)

    print("\n[FILE 2: MOTOR HUM] Expected: OFF -> Action Window -> IDLE", flush=True)
    trans, end = simulate_timeline(spinup_data, thresholds, "OFF")
    for t in trans: print(t, flush=True)
    print("   ✅ PASS" if end == "IDLE" else "   ❌ FAIL", flush=True)

    print("\n[FILE 3: MASTER TRANSITION] Expected: IDLE -> Action Window -> MUSIC -> Runout -> IDLE", flush=True)
    trans, end = simulate_timeline(trans_data, thresholds, "IDLE")
    for t in trans: print(t, flush=True)
    print("   ✅ PASS" if "MUSIC" in str(trans) and end == "IDLE" else "   ❌ FAIL", flush=True)
    
    print("\n[FILE 5: POWER DOWN] Expected: IDLE -> Action Window -> OFF", flush=True)
    trans, end = simulate_timeline(powerdown_data, thresholds, "IDLE")
    for t in trans: print(t, flush=True)
    print("   ✅ PASS" if end == "OFF" else "   ❌ FAIL", flush=True)
    
    print("\n[FILE 6: ROOM NOISE] Expected: Stays OFF (No Music Trigger)", flush=True)
    trans, end = simulate_timeline(disturb_data, thresholds, "OFF")
    for t in trans: print(t, flush=True)
    print("   ✅ PASS" if end == "OFF" and not any("MUSIC" in t for t in trans) else "   ❌ FAIL", flush=True)

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
        print("\n🧹 REUSE_CALIBRATION_AUDIO is OFF. Clearing old data...", flush=True)
        if os.path.exists(CALIB_DIR): shutil.rmtree(CALIB_DIR)
        os.makedirs(CALIB_DIR)
        use_existing = False
    else:
        if all(os.path.exists(f) for f in FILES.values()):
            print("\n📁 REUSE_CALIBRATION_AUDIO is ON. Reusing existing recordings.", flush=True)
            use_existing = True
        else:
            print("\n⚠️  REUSE_CALIBRATION_AUDIO is ON, but files are missing. Starting fresh recordings...", flush=True)
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
        
        record_segmented_file(FILES["disturbance"], 30, 0, 
            "[FILE 6/6: ROOM NOISE]\n🗣️  Action: Talk and tap the cabinet for 30s.")

    thresholds = analyze_and_simulate(FILES)
    if not use_existing: thresholds["MIC_VOLUME"] = final_mic_vol
    
    with open(AUTO_CALIB_FILE, 'w') as f: json.dump(thresholds, f, indent=4)
    with open("config.json", 'w') as f: json.dump(thresholds, f, indent=4)
        
    print("\n" + "="*50, flush=True)
    print("🎉 CALIBRATION COMPLETE 🎉", flush=True)
    print("\n🔒 Final Tuned Logic Map:", flush=True)
    for key, value in thresholds.items():
        print(f"   - {key}: {value}", flush=True)
    
    print("\n🔄 Please disable CALIBRATION_MODE in your config and RESTART the Add-on.", flush=True)
    print("💤 Calibration engine is now idling indefinitely to prevent restart loops...", flush=True)
    
    while True: time.sleep(3600)

if __name__ == "__main__":
    run_calibration()