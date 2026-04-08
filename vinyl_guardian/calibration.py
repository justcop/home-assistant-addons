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
REUSE_CALIB_OPT = os.environ.get('REUSE_CALIBRATION_AUDIO', 'false').lower() == 'true'

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

def record_segmented_file(filename, transition_duration, steady_duration, prompt):
    print(f"\n" + "-"*50, flush=True)
    print(f"{prompt}", flush=True)
    print(f"🎬 ACTION WINDOW STARTED ({transition_duration}s): Perform action NOW!", flush=True)
    
    trans_bytes, _ = record_chunk(transition_duration)
    print(f"⏹️  STEADY STATE ({steady_duration}s): Capturing stable background...", flush=True)
    
    steady_bytes, _ = record_chunk(steady_duration)
    full_bytes = trans_bytes + steady_bytes
    
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(full_bytes)
        
    print(f"✅ Saved to {os.path.basename(filename)}", flush=True)
    time.sleep(1)

# --- STEP 0: AUTOMATED GAIN STAGING ---
def set_mic_volume(vol_pct):
    try:
        subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{vol_pct}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception: pass

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

# --- ANALYSIS ---
def analyze_files(files):
    print("\n" + "="*50, flush=True)
    print("🧠 ANALYZING THE SEQUENTIAL CHAIN", flush=True)
    print("="*50, flush=True)
    
    # 1. Floor analysis (10s to end)
    floor_data = load_wav(files["floor"])
    floor_rms = chunked_rms(floor_data[10*RATE:])
    silence_gate = np.max(reject_outliers_mad(floor_rms)) * 1.10
    
    # 2. Motor Idle analysis (15s to end)
    idle_data = load_wav(files["spinup"])
    idle_rms = chunked_rms(idle_data[15*RATE:])
    motor_hum_max = np.max(reject_outliers_mad(idle_rms))
    motor_threshold = (silence_gate + motor_hum_max) / 2.0
    
    # 3. Transition Analysis (THE SMART BIT)
    print("📉 Scanning transition file for music-to-runout drop-off...", flush=True)
    trans_data = load_wav(files["transition"])
    trans_rms = chunked_rms(trans_data, chunk_size=8192) # Larger chunk for smoother diff
    
    diffs = np.diff(trans_rms)
    drop_idx = np.argmin(diffs)
    drop_time_sec = (drop_idx * 8192) / RATE
    
    # Sanity Check: If the drop is in the last 45 seconds or first 30, it's likely a bad capture
    if drop_time_sec > (360 - 45):
         print("⚠️ WARNING: No music end detected early enough! Did the track finish?", flush=True)
         # Fallback to last 30 seconds
         runout_window = trans_data[-30*RATE:]
    else:
        # We start the Runout Sample 20 seconds AFTER the detected drop (Definitively in the runout)
        # We take a 20 second sample window
        runout_start = int((drop_time_sec + 20) * RATE)
        runout_end = int((drop_time_sec + 40) * RATE)
        runout_window = trans_data[runout_start:runout_end]
        print(f"✅ Music end detected at {drop_time_sec:.1f}s.")
        print(f"   Waiting 20s for runout stabilization. Sampling from {drop_time_sec+20:.1f}s to {drop_time_sec+40:.1f}s.", flush=True)
    
    music_data = trans_data[:int(drop_time_sec * RATE)]
    music_rms_arr = chunked_music_rms(music_data)
    min_music_rms = np.percentile(music_rms_arr, 5) 
    
    # Use the specific Runout Window to define Rumble/Pop ceilings
    runout_rms_arr = chunked_rms(runout_window)
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
        print("\n🧹 Fresh calibration requested. Clearing old data...", flush=True)
        if os.path.exists(CALIB_DIR): shutil.rmtree(CALIB_DIR)
        os.makedirs(CALIB_DIR)
        use_existing = False
    else:
        if all(os.path.exists(f) for f in FILES.values()):
            print("\n📁 Reusing existing recordings found in calibration_data/.", flush=True)
            use_existing = True
        else:
            print("\n⚠️  Files missing. Starting fresh recordings...", flush=True)
            if not os.path.exists(CALIB_DIR): os.makedirs(CALIB_DIR)
            use_existing = False
            
    if not use_existing:
        final_mic_vol = gain_staging()
        
        record_segmented_file(FILES["floor"], 0, 30, 
            "[FILE 1/6: THE BASELINE]\n🔌 Turntable: OFF\n🤫 Action: Stay quiet.")
        
        record_segmented_file(FILES["spinup"], 10, 20, 
            "[FILE 2/6: THE MOTOR HUM]\n🟢 Action: Turn turntable power ON now.")
        
        record_segmented_file(FILES["transition"], 15, 345, 
            "[FILE 3/6: THE MASTER TRANSITION]\n🎶 Action: Drop needle on the LAST TRACK now.\n〰️  Recording 6 mins to catch the music-to-runout fade.")
        
        record_segmented_file(FILES["lift"], 10, 20, 
            "[FILE 4/6: THE PHYSICAL THUMP]\n⬆️  Action: LIFT the tonearm with the cue lever now.")
        
        record_segmented_file(FILES["powerdown"], 10, 20, 
            "[FILE 5/6: THE ELECTRICAL POP]\n🔴 Action: Turn the turntable power OFF now.")
        
        record_segmented_file(FILES["disturbance"], 30, 0, 
            "[FILE 6/6: ROOM NOISE]\n🗣️  Action: Talk and tap the cabinet for 30s.")

    thresholds = analyze_files(FILES)
    if not use_existing: thresholds["MIC_VOLUME"] = final_mic_vol
    
    with open(AUTO_CALIB_FILE, 'w') as f: json.dump(thresholds, f, indent=4)
    with open("config.json", 'w') as f: json.dump(thresholds, f, indent=4)
        
    print("\n" + "="*50, flush=True)
    print("🎉 CALIBRATION COMPLETE 🎉", flush=True)
    print(f"Volume locked at {thresholds.get('MIC_VOLUME', 'Unknown')}%", flush=True)
    print("🔄 Restart Add-on (Calibration Mode: OFF) to begin tracking.", flush=True)
    sys.exit(0)

if __name__ == "__main__":
    run_calibration()