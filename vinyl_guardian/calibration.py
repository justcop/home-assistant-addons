import os
import time
import json
import wave
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
    """Removes sudden pops/clicks from stable data using Median Absolute Deviation"""
    if len(data) == 0: return data
    med = np.median(data)
    mad = np.median(np.abs(data - med))
    if mad == 0: return data
    modified_z_scores = 0.6745 * (data - med) / mad
    return data[np.abs(modified_z_scores) <= threshold]

def get_rms(audio_data):
    """Calculates Raw RMS using native numpy"""
    return float(np.sqrt(np.mean(np.square(audio_data))))

def get_music_rms(audio_data):
    """Calculates Filtered Music RMS using native numpy"""
    if len(audio_data) <= 1: return 0.0
    filtered_data = audio_data[1:] - 0.95 * audio_data[:-1]
    return float(np.sqrt(np.mean(np.square(filtered_data))))

def load_wav(filename):
    """Loads a wav file into a numpy array"""
    with wave.open(filename, 'rb') as wf:
        n_frames = wf.getnframes()
        audio_bytes = wf.readframes(n_frames)
        audio_data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        # Average to mono if stereo
        if wf.getnchannels() == 2:
            audio_data = audio_data.reshape(-1, 2).mean(axis=1)
        return audio_data

def chunked_rms(data, chunk_size=4096):
    """Calculates RMS over small blocks of time to find drop-offs"""
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
    """Records a temporary chunk of audio using the native ALSA engine"""
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

def record_file(filename, duration, prompt):
    print(f"\n{prompt}", flush=True)
    print("⏱️  Waiting 10 seconds before recording...", flush=True)
    time.sleep(10)
    print("🔴 RECORDING...", flush=True)
    raw_bytes, _ = record_chunk(duration)
    
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(raw_bytes)
        
    print(f"✅ Saved to {filename}", flush=True)

# --- STEP 0: GAIN STAGING ---
def gain_staging():
    print("\n" + "="*50, flush=True)
    print("🎚️  STEP 0: PRE-CALIBRATION GAIN STAGING", flush=True)
    print("="*50, flush=True)
    print("🔊 Please drop the needle onto a LOUD section of a record.", flush=True)
    print("   We need to lock in your hardware volume before calibrating.", flush=True)
    print("⏱️  Waiting 15 seconds for you to drop the needle...", flush=True)
    time.sleep(15)
    
    while True:
        print("\n🎧 Listening for 3 seconds...", flush=True)
        _, audio_data = record_chunk(3.0)
        
        if len(audio_data) == 0:
            print("🚨 ERROR: No audio received from microphone.", flush=True)
            break
            
        peak = np.max(np.abs(audio_data))
        
        if peak > 0.85:
            print(f"⚠️ Peak: {peak:.2f} (TOO HIGH - Clipping risk!)", flush=True)
            print("👇 Action: Turn DOWN your pre-amp or capture card volume.", flush=True)
            print("⏱️  Retesting in 10 seconds...", flush=True)
            time.sleep(10)
        elif peak < 0.45:
            print(f"⚠️ Peak: {peak:.2f} (TOO LOW - Weak signal!)", flush=True)
            print("👆 Action: Turn UP your pre-amp or capture card volume.", flush=True)
            print("⏱️  Retesting in 10 seconds...", flush=True)
            time.sleep(10)
        else:
            print(f"✅ Peak: {peak:.2f} (PERFECT!)", flush=True)
            print("🔒 Gain stage locked. DO NOT touch the volume dials anymore.", flush=True)
            break
            
    print("\n⏹️  Please stop the record and turn the turntable OFF completely.", flush=True)
    print("⏱️  Waiting 15 seconds to begin main calibration...", flush=True)
    time.sleep(15)

# --- ANALYSIS & SIMULATION ---
def analyze_files():
    print("\n" + "="*50, flush=True)
    print("🧠 ANALYZING FILES & EXTRACTING THRESHOLDS", flush=True)
    print("="*50, flush=True)
    
    # 1. FLOOR (10s to 30s)
    floor_data = load_wav(FILES["floor"])
    floor_stable = floor_data[10*RATE:30*RATE]
    floor_rms_arr = chunked_rms(floor_stable)
    floor_clean = reject_outliers_mad(floor_rms_arr)
    silence_gate = np.max(floor_clean) * 1.10 # Add 10% safety buffer
    
    # 2. MOTOR IDLE (15s to 30s)
    idle_data = load_wav(FILES["spinup"])
    idle_stable = idle_data[15*RATE:30*RATE]
    idle_rms_arr = chunked_rms(idle_stable)
    idle_clean = reject_outliers_mad(idle_rms_arr)
    motor_hum_max = np.max(idle_clean)
    
    # Set Motor Threshold exactly halfway between Silence Gate and Max Motor Hum
    motor_threshold = (silence_gate + motor_hum_max) / 2.0
    
    # 3. MUSIC TO RUNOUT (Auto-detect transition)
    trans_data = load_wav(FILES["transition"])
    trans_rms = chunked_rms(trans_data)
    
    # Find the massive drop in RMS to detect music end
    diffs = np.diff(trans_rms)
    drop_idx = np.argmin(diffs) # Largest negative change
    drop_time = (drop_idx * 4096) / RATE
    print(f"📉 Music fade-out auto-detected at {drop_time:.1f} seconds.", flush=True)
    
    music_data = trans_data[:int(drop_idx * 4096)]
    music_rms_arr = chunked_music_rms(music_data)
    min_music_rms = np.percentile(music_rms_arr, 5) # 5th percentile to allow quiet parts
    
    # Return thresholds mapping to our standard JSON keys
    thresholds = {
        "SILENCE_GATE_RMS": round(float(silence_gate), 5),
        "motor_power_threshold": round(float(motor_threshold), 5),
        "motor_power_ceiling": round(float(motor_hum_max * 1.5), 5),
        "music_threshold": round(float(min_music_rms), 5),
        "is_silent_hw": False
    }
    
    return thresholds

def run_simulation_loop(thresholds):
    print("\n" + "="*50, flush=True)
    print("🧪 RUNNING PERTURBATION & SIMULATION ENGINE", flush=True)
    print("="*50, flush=True)
    
    delta = thresholds["motor_power_threshold"] - thresholds["SILENCE_GATE_RMS"]
    
    print(f"   Silence Gate: {thresholds['SILENCE_GATE_RMS']}", flush=True)
    print(f"   Motor Idle:   {thresholds['motor_power_threshold']}", flush=True)
    print(f"   Delta:        {delta:.5f}", flush=True)
    
    if delta < 0.002:
        print("\n⚠️ HEALTH WARNING: Fidelity Delta is extremely tight!", flush=True)
        print("   -> Your setup is an 'Ultra-Silent Setup' (Motor hum is barely audible).", flush=True)
        print("   -> Action taken: Nudging MOTOR_IDLE_THRESHOLD down to ensure spin-up is caught.", flush=True)
        thresholds["motor_power_threshold"] -= 0.001
        thresholds["is_silent_hw"] = True
        
    print("\n✅ Simulation Passed. Thresholds locked.", flush=True)
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
    
    use_existing = False
    if all(os.path.exists(f) for f in FILES.values()):
        print("\n📁 Found existing 6-file calibration chain.", flush=True)
        print("   -> REUSING existing files.", flush=True)
        print("   -> To record a fresh calibration, delete the 'calibration_data' folder.", flush=True)
        time.sleep(3)
        use_existing = True
            
    if not use_existing:
        gain_staging()
        print("\n" + "="*50, flush=True)
        print("🎙️  RECORDING SEQUENTIAL CALIBRATION CHAIN", flush=True)
        print("="*50, flush=True)
        
        # File 1
        record_file(FILES["floor"], 30, 
            "[FILE 1/6: THE BASELINE]\n"
            "🔌 Ensure turntable is completely OFF.\n"
            "🤫 Remain quiet for 30 seconds.")
        
        # File 2
        record_file(FILES["spinup"], 30, 
            "[FILE 2/6: THE MOTOR HUM]\n"
            "🟢 When recording starts, turn turntable power ON.\n"
            "⚙️  Wait 30 seconds for the motor hum.")
        
        # File 3
        record_file(FILES["transition"], 70, 
            "[FILE 3/6: THE MASTER TRANSITION]\n"
            "🎶 Drop needle ~30 secs before a song ends.\n"
            "〰️  Let it play, end, and fade naturally into the runout groove.")
        
        # File 4
        record_file(FILES["lift"], 30, 
            "[FILE 4/6: THE PHYSICAL THUMP]\n"
            "⬆️  When recording starts, LIFT the tonearm using the cue lever.\n"
            "⚙️  Leave the turntable motor ON and spinning.")
        
        # File 5
        record_file(FILES["powerdown"], 30, 
            "[FILE 5/6: THE ELECTRICAL POP]\n"
            "🔴 When recording starts, turn the turntable power OFF.\n"
            "🔌 Let the motor spin down.")
        
        # File 6
        record_file(FILES["disturbance"], 30, 
            "[FILE 6/6: THE FALSE POSITIVE CHECK]\n"
            "🗣️  Ensure turntable is OFF.\n"
            "🖐️  Talk loudly and tap the cabinet for 30 seconds to simulate room noise.")

    # Process Data
    initial_thresholds = analyze_files()
    final_thresholds = run_simulation_loop(initial_thresholds)
    
    # Save Config to both known locations to ensure main script grabs it
    with open(AUTO_CALIB_FILE, 'w') as f:
        json.dump(final_thresholds, f, indent=4)
        
    with open("config.json", 'w') as f:
        json.dump(final_thresholds, f, indent=4)
        
    print("\n" + "="*50, flush=True)
    print("🎉 CALIBRATION COMPLETE 🎉", flush=True)
    print("Saved Configuration:", flush=True)
    for k, v in final_thresholds.items():
        print(f"  {k}: {v}", flush=True)
    print("="*50, flush=True)
    print("\n🔄 Please disable CALIBRATION_MODE in your config and restart the Add-on.", flush=True)
    
    # Exit gracefully to prevent the add-on from continuing into the main loop
    sys.exit(0)

if __name__ == "__main__":
    run_calibration()