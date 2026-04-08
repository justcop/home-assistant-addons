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
    """3-Phase Recording: Action Window -> Mechanical Settle -> Steady State"""
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
    """Special Live-Listening recorder for File 3"""
    print(f"\n" + "-"*50, flush=True)
    print("[FILE 3/6: THE MASTER TRANSITION]\n🎶 ACTION: Drop needle on the LAST TRACK now.")
    print("〰️  The system will listen live for the track to end, automatically wait for the runout groove, and capture the rumble.", flush=True)
    
    raw_bytes = bytearray()
    
    # Phase 1: Action (10s)
    print(f"🎬 ACTION WINDOW (10s): Drop the needle NOW!", flush=True)
    chunk_b, _ = record_chunk(10.0)
    raw_bytes.extend(chunk_b)
    
    # Phase 2: Live Music Monitoring
    print("🎵 MUSIC PHASE: Listening for the track to naturally end...", flush=True)
    max_music_rms = 0.0
    consecutive_low = 0
    music_ended = False
    
    for i in range(360): # Max 6 minutes fail-safe
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
        
    # Phase 3: Wait for needle to travel
    print("⏳ TRANSIT PHASE (20s): Waiting for needle to firmly reach runout groove...", flush=True)
    chunk_b, _ = record_chunk(20.0)
    raw_bytes.extend(chunk_b)
    
    # Phase 4: Runout Capture
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
    
    time.sleep(10) # Wait for needle drop
    
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

# --- ANALYSIS ---
def analyze_files(files):
    print("\n" + "="*50, flush=True)
    print("🧠 ANALYZING THE SEQUENTIAL CHAIN", flush=True)
    print("="*50, flush=True)
    
    # 1. Floor analysis: Cross-check File 1 and File 5
    floor_1_data = load_wav(files["floor"])
    floor_1_rms = chunked_rms(floor_1_data) # All 30s is steady floor
    
    powerdown_data = load_wav(files["powerdown"])
    # Skip first 20s (10s action + 10s spindown settle)
    floor_5_rms = chunked_rms(powerdown_data[20*RATE:]) 
    
    combined_floor = np.concatenate((floor_1_rms, floor_5_rms))
    silence_gate = np.max(reject_outliers_mad(combined_floor)) * 1.10
    
    # 2. Motor Idle analysis: Cross-check File 2 and File 4
    spinup_data = load_wav(files["spinup"])
    # Skip first 20s (10s action + 10s spinup settle to reach full speed)
    idle_2_rms = chunked_rms(spinup_data[20*RATE:])
    
    lift_data = load_wav(files["lift"])
    # Skip first 15s (10s action + 5s tonearm mechanical settle)
    idle_4_rms = chunked_rms(lift_data[15*RATE:])
    
    combined_idle = np.concatenate((idle_2_rms, idle_4_rms))
    motor_hum_max = np.max(reject_outliers_mad(combined_idle))
    
    motor_threshold = (silence_gate + motor_hum_max) / 2.0
    
    # 3. Transition Analysis: Because of dynamic recording, the LAST 20s is perfectly guaranteed to be runout groove.
    trans_data = load_wav(files["transition"])
    
    runout_data = trans_data[-20*RATE:]
    music_data = trans_data[10*RATE : -40*RATE] # Skip first 10s action window and skip 40s tail
    
    music_rms_arr = chunked_music_rms(music_data)
    min_music_rms = np.percentile(music_rms_arr, 5) 
    
    runout_rms_arr = chunked_rms(runout_data)
    runout_rumble_max = np.max(reject_outliers_mad(runout_rms_arr))

    return {
        "SILENCE_GATE_RMS": round(float(silence_gate), 5),
        "motor_power_threshold": round(float(motor_threshold), 5),
        "motor_power_ceiling": round(float(runout_rumble_max * 1.3), 5),
        "music_threshold": round(float(min_music_rms), 5),
        "is_silent_hw": False
    }

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
        
        # File 1: Action (0s) -> Settle (0s) -> Steady (30s)
        record_segmented_file(FILES["floor"], 0, 0, 30, 
            "[FILE 1/6: THE BASELINE]\n🔌 Turntable: OFF\n🤫 Action: Stay quiet.")
        
        # File 2: Action (10s) -> Settle Spinup (10s) -> Steady Hum (15s)
        record_segmented_file(FILES["spinup"], 10, 10, 15, 
            "[FILE 2/6: THE MOTOR HUM]\n🟢 Action: Turn turntable power ON.")
        
        # File 3: Live Dynamic Recording
        record_dynamic_transition(FILES["transition"])
        
        # File 4: Action (10s) -> Settle Thump (5s) -> Steady Hum (15s)
        record_segmented_file(FILES["lift"], 10, 5, 15, 
            "[FILE 4/6: THE PHYSICAL THUMP]\n⬆️  Action: LIFT the tonearm with the cue lever.")
        
        # File 5: Action (10s) -> Settle Spindown (10s) -> Steady Floor (15s)
        record_segmented_file(FILES["powerdown"], 10, 10, 15, 
            "[FILE 5/6: THE ELECTRICAL POP]\n🔴 Action: Turn the turntable power OFF.")
        
        # File 6: Action (0s) -> Settle (0s) -> Steady Disturbance (30s)
        record_segmented_file(FILES["disturbance"], 0, 0, 30, 
            "[FILE 6/6: ROOM NOISE]\n🗣️  Action: Talk and tap the cabinet for 30s.")

    thresholds = analyze_files(FILES)
    if not use_existing: thresholds["MIC_VOLUME"] = final_mic_vol
    
    with open(AUTO_CALIB_FILE, 'w') as f: json.dump(thresholds, f, indent=4)
    with open("config.json", 'w') as f: json.dump(thresholds, f, indent=4)
        
    print("\n" + "="*50, flush=True)
    print("🎉 CALIBRATION COMPLETE 🎉", flush=True)
    print(f"Volume locked at {thresholds.get('MIC_VOLUME', 'Unknown')}%", flush=True)
    print("\n🔄 Please disable CALIBRATION_MODE in your config and RESTART the Add-on.", flush=True)
    print("💤 Calibration engine is now idling indefinitely to prevent restart loops...", flush=True)
    
    while True:
        time.sleep(3600)

if __name__ == "__main__":
    run_calibration()