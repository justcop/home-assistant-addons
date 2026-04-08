import os
import time
import json
import wave
import subprocess
import numpy as np
import alsaaudio
import warnings
import sys

# Suppress numpy warnings for clean output
warnings.filterwarnings('ignore')

# Import settings from your existing config
from config import SHARE_DIR, AUTO_CALIB_FILE, RATE, CHANNELS, CHUNK

# --- CONFIGURATION ---
FORMAT = alsaaudio.PCM_FORMAT_S16_LE
CALIB_DIR = os.path.join(SHARE_DIR, "calibration_data")

if not os.path.exists(CALIB_DIR):
    os.makedirs(CALIB_DIR)

FILES = {
    "floor": os.path.join(CALIB_DIR, "calib_off_floor.wav"),
    "spinup": os.path.join(CALIB_DIR, "calib_spin_up.wav"),
    "transition": os.path.join(CALIB_DIR, "calib_music_to_runout.wav"),
    "lift": os.path.join(CALIB_DIR, "calib_needle_lift.wav"),
    "powerdown": os.path.join(CALIB_DIR, "calib_power_down.wav"),
    "disturbance": os.path.join(CALIB_DIR, "calib_disturbance.wav")
}

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
    """Records a chunk and returns raw bytes + float array"""
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
    """
    Starts recording IMMEDIATELY when prompt is shown.
    Records the transition and the steady state into ONE file.
    """
    print(f"\n" + "-"*50, flush=True)
    print(f"{prompt}", flush=True)
    print(f"🎬 ACTION WINDOW STARTED ({transition_duration}s): Perform action NOW!", flush=True)
    
    # Record the transition part
    trans_bytes, _ = record_chunk(transition_duration)
    
    print(f"⏹️  STEADY STATE ({steady_duration}s): Capturing background...", flush=True)
    
    # Record the steady part
    steady_bytes, _ = record_chunk(steady_duration)
    
    # Combine
    full_bytes = trans_bytes + steady_bytes
    
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(full_bytes)
        
    print(f"✅ Captured {transition_duration + steady_duration}s to {os.path.basename(filename)}", flush=True)
    time.sleep(2) # Short breather for the user to read the next step

# --- STEP 0: AUTOMATED GAIN STAGING ---
def set_mic_volume(vol_pct):
    try:
        subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{vol_pct}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def gain_staging():
    print("\n" + "="*50, flush=True)
    print("🎚️  STEP 0: AUTO-CALIBRATING SOFTWARE VOLUME", flush=True)
    print("="*50, flush=True)
    print("🔊 ACTION: Find the LOUDEST record you own and drop the needle NOW.", flush=True)
    print("   We will spend 30 seconds finding the 1% precision sweet spot.", flush=True)
    
    current_vol = 50
    step = 16 
    last_direction = 0 
    set_mic_volume(current_vol)
    
    # Initial wait for needle drop
    time.sleep(10)
    
    while True:
        # Phase 1: Adaptive targeting
        _, audio_data = record_chunk(3.0)
        if len(audio_data) == 0: return current_vol
        peak = np.max(np.abs(audio_data))
        
        if peak > 0.80:
            if last_direction == 1: step = max(1, step // 2)
            last_direction = -1
            current_vol = max(1, current_vol - step)
            set_mic_volume(current_vol)
            print(f"   Peak {peak:.2f} (Hot) -> Vol: {current_vol}% (Step: {step}%)", flush=True)
        elif peak < 0.50:
            if last_direction == -1: step = max(1, step // 2)
            last_direction = 1
            current_vol = min(100, current_vol + step)
            set_mic_volume(current_vol)
            print(f"   Peak {peak:.2f} (Low) -> Vol: {current_vol}% (Step: {step}%)", flush=True)
        else:
            # Verification Phase
            print(f"   Peak {peak:.2f} (Testing...) -> Verifying {current_vol}% for 10s...", flush=True)
            _, v_data = record_chunk(10.0)
            v_peak = np.max(np.abs(v_data))
            if v_peak > 0.85:
                current_vol -= 1
                set_mic_volume(current_vol)
                print(f"   Verification failed (Peak {v_peak:.2f}). Nudging to {current_vol}%...", flush=True)
                continue
            print(f"✅ VOLUME LOCKED at {current_vol}%", flush=True)
            break
            
    print("\n⏹️  ACTION: Stop the record and turn the turntable OFF completely.", flush=True)
    time.sleep(10)
    return current_vol

# --- ANALYSIS ---
def analyze_files():
    print("\n" + "="*50, flush=True)
    print("🧠 ANALYZING THE SEQUENTIAL CHAIN", flush=True)
    print("="*50, flush=True)
    
    floor_data = load_wav(FILES["floor"])
    # 10s to end is stable floor
    floor_stable = floor_data[10*RATE:]
    floor_rms = chunked_rms(floor_stable)
    silence_gate = np.max(reject_outliers_mad(floor_rms)) * 1.10
    
    idle_data = load_wav(FILES["spinup"])
    # 15s to end is stable hum
    idle_stable = idle_data[15*RATE:]
    idle_rms = chunked_rms(idle_stable)
    motor_hum_max = np.max(reject_outliers_mad(idle_rms))
    
    motor_threshold = (silence_gate + motor_hum_max) / 2.0
    
    trans_data = load_wav(FILES["transition"])
    trans_rms = chunked_rms(trans_data)
    diffs = np.diff(trans_rms)
    drop_idx = np.argmin(diffs)
    drop_time = (drop_idx * 4096) / RATE
    print(f"📉 Music fade-out auto-detected at {drop_time:.1f} seconds.", flush=True)
    
    music_data = trans_data[:int(drop_idx * 4096)]
    music_rms_arr = chunked_music_rms(music_data)
    min_music_rms = np.percentile(music_rms_arr, 5) 
    
    return {
        "SILENCE_GATE_RMS": round(float(silence_gate), 5),
        "motor_power_threshold": round(float(motor_threshold), 5),
        "motor_power_ceiling": round(float(motor_hum_max * 1.5), 5),
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
    
    if all(os.path.exists(f) for f in FILES.values()):
        print("\n📁 Reusing existing calibration recordings.", flush=True)
        use_existing = True
    else:
        use_existing = False
            
    if not use_existing:
        final_mic_vol = gain_staging()
        
        # File 1: Pure Floor (No Action)
        record_segmented_file(FILES["floor"], 0, 30, 
            "[FILE 1/6: THE BASELINE]\n🔌 Turntable should be OFF.\n🤫 No action required. Stay quiet.")
        
        # File 2: Power ON
        record_segmented_file(FILES["spinup"], 10, 20, 
            "[FILE 2/6: THE MOTOR HUM]\n🟢 ACTION: Turn turntable power ON now.\n⚙️  The first 10s will capture the spin-up.")
        
        # File 3: Music -> Runout (LAST TRACK)
        record_segmented_file(FILES["transition"], 15, 345, 
            "[FILE 3/6: THE MASTER TRANSITION]\n🎶 ACTION: Drop needle on the LAST TRACK of a record side now.\n〰️  We will capture 6 minutes to catch the natural fade into the runout.")
        
        # File 4: Lift Arm
        record_segmented_file(FILES["lift"], 10, 20, 
            "[FILE 4/6: THE PHYSICAL THUMP]\n⬆️  ACTION: LIFT the tonearm with the cue lever now.\n⚙️  Keep the motor ON.")
        
        # File 5: Power OFF
        record_segmented_file(FILES["powerdown"], 10, 20, 
            "[FILE 5/6: THE ELECTRICAL POP]\n🔴 ACTION: Turn the turntable power OFF now.")
        
        # File 6: Room Noise
        record_segmented_file(FILES["disturbance"], 30, 0, 
            "[FILE 6/6: FALSE POSITIVE CHECK]\n🗣️  ACTION: Talk loudly and tap your turntable cabinet for the next 30s.")

    thresholds = analyze_files()
    if not use_existing: thresholds["MIC_VOLUME"] = final_mic_vol
    
    with open(AUTO_CALIB_FILE, 'w') as f: json.dump(thresholds, f, indent=4)
    with open("config.json", 'w') as f: json.dump(thresholds, f, indent=4)
        
    print("\n" + "="*50, flush=True)
    print("🎉 CALIBRATION COMPLETE 🎉", flush=True)
    print(f"Saved to {AUTO_CALIB_FILE}", flush=True)
    print("🔄 Please disable CALIBRATION_MODE and restart the Add-on.", flush=True)
    sys.exit(0)

if __name__ == "__main__":
    run_calibration()