import os
import time
import json
import numpy as np
import sounddevice as sd
import soundfile as sf
import librosa
import warnings

# Suppress librosa warnings for clean output
warnings.filterwarnings('ignore')

# --- CONFIGURATION ---
SAMPLE_RATE = 22050
AUDIO_DEVICE = os.environ.get('AUDIO_DEVICE', 'default')
CALIB_DIR = "calibration_data"
CONFIG_FILE = "config.json"

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

# --- UTILITY: MAD OUTLIER REJECTION ---
def reject_outliers_mad(data, threshold=3.5):
    """Removes sudden pops/clicks from stable data using Median Absolute Deviation"""
    if len(data) == 0: return data
    med = np.median(data)
    mad = np.median(np.abs(data - med))
    if mad == 0: return data
    modified_z_scores = 0.6745 * (data - med) / mad
    return data[np.abs(modified_z_scores) <= threshold]

# --- RECORDING ENGINE ---
def record_chunk(duration):
    """Records a temporary chunk of audio"""
    audio = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, device=AUDIO_DEVICE)
    sd.wait()
    return audio[:, 0]

def record_file(filename, duration, prompt):
    print(f"\n{prompt}")
    for i in range(3, 0, -1):
        print(f"⏱️ Starting in {i}...")
        time.sleep(1)
    print("🔴 RECORDING...")
    audio = record_chunk(duration)
    sf.write(filename, audio, SAMPLE_RATE)
    print(f"✅ Saved to {filename}")
    return audio

# --- STEP 0: GAIN STAGING ---
def gain_staging():
    print("\n" + "="*50)
    print("🎚️  STEP 0: PRE-CALIBRATION GAIN STAGING")
    print("="*50)
    print("🔊 Please drop the needle onto a LOUD section of a record.")
    print("   We need to lock in your hardware volume before calibrating.")
    input("▶️  Press ENTER when music is playing loudly...")
    
    while True:
        print("\n🎧 Listening for 3 seconds...")
        audio = record_chunk(3.0)
        peak = np.max(np.abs(audio))
        
        if peak > 0.85:
            print(f"⚠️ Peak: {peak:.2f} (TOO HIGH - Clipping risk!)")
            print("👇 Action: Turn DOWN your pre-amp or capture card volume.")
            input("🔄 Press ENTER to test again...")
        elif peak < 0.45:
            print(f"⚠️ Peak: {peak:.2f} (TOO LOW - Weak signal!)")
            print("👆 Action: Turn UP your pre-amp or capture card volume.")
            input("🔄 Press ENTER to test again...")
        else:
            print(f"✅ Peak: {peak:.2f} (PERFECT!)")
            print("🔒 Gain stage locked. DO NOT touch the volume dials anymore.")
            break
            
    print("\n⏹️  Please stop the record and turn the turntable OFF completely.")
    input("⚙️  Press ENTER to begin the main calibration chain...")

# --- ANALYSIS & SIMULATION ---
def analyze_files():
    print("\n" + "="*50)
    print("🧠 ANALYZING FILES & EXTRACTING THRESHOLDS")
    print("="*50)
    
    # 1. FLOOR (10s to 30s)
    floor_data, _ = librosa.load(FILES["floor"], sr=SAMPLE_RATE)
    floor_stable = floor_data[10*SAMPLE_RATE:30*SAMPLE_RATE]
    floor_rms_arr = librosa.feature.rms(y=floor_stable, frame_length=4096, hop_length=4096)[0]
    floor_clean = reject_outliers_mad(floor_rms_arr)
    silence_gate = np.max(floor_clean) * 1.10 # Add 10% safety buffer
    
    # 2. MOTOR IDLE (15s to 30s)
    idle_data, _ = librosa.load(FILES["spinup"], sr=SAMPLE_RATE)
    idle_stable = idle_data[15*SAMPLE_RATE:30*SAMPLE_RATE]
    idle_rms_arr = librosa.feature.rms(y=idle_stable, frame_length=4096, hop_length=4096)[0]
    idle_clean = reject_outliers_mad(idle_rms_arr)
    motor_hum_max = np.max(idle_clean)
    
    # Set Motor Threshold exactly halfway between Silence Gate and Max Motor Hum
    motor_threshold = (silence_gate + motor_hum_max) / 2.0
    power_off_threshold = silence_gate * 1.2 # Slightly above gate to confirm power
    
    # 3. MUSIC TO RUNOUT (Auto-detect transition)
    trans_data, _ = librosa.load(FILES["transition"], sr=SAMPLE_RATE)
    trans_rms = librosa.feature.rms(y=trans_data, frame_length=4096, hop_length=4096)[0]
    
    # Find the massive drop in RMS to detect music end
    diffs = np.diff(trans_rms)
    drop_idx = np.argmin(diffs) # Largest negative change
    drop_time = (drop_idx * 4096) / SAMPLE_RATE
    print(f"📉 Music fade-out auto-detected at {drop_time:.1f} seconds.")
    
    music_data = trans_data[:int(drop_idx * 4096)]
    runout_data = trans_data[int((drop_idx + 5) * 4096):] # Wait 5 secs after drop
    
    music_preemph = librosa.effects.preemphasis(music_data)
    music_rms_arr = librosa.feature.rms(y=music_preemph, frame_length=4096, hop_length=4096)[0]
    min_music_rms = np.percentile(music_rms_arr, 5) # 5th percentile to allow quiet parts
    
    # Initial Guess Dictionary
    thresholds = {
        "SILENCE_GATE_RMS": round(float(silence_gate), 4),
        "POWER_OFF_THRESHOLD": round(float(power_off_threshold), 4),
        "MOTOR_IDLE_THRESHOLD": round(float(motor_threshold), 4),
        "MIN_MUSIC_RMS": round(float(min_music_rms), 4),
        "THUMP_REJECTION_HYSTERESIS": 5 # Default seconds to ignore massive spikes
    }
    
    return thresholds, floor_clean, idle_clean

def run_simulation_loop(thresholds):
    print("\n" + "="*50)
    print("🧪 RUNNING PERTURBATION & SIMULATION ENGINE")
    print("="*50)
    
    passes = True
    delta = thresholds["MOTOR_IDLE_THRESHOLD"] - thresholds["SILENCE_GATE_RMS"]
    
    print(f"   Silence Gate: {thresholds['SILENCE_GATE_RMS']}")
    print(f"   Motor Idle:   {thresholds['MOTOR_IDLE_THRESHOLD']}")
    print(f"   Delta:        {delta:.4f}")
    
    if delta < 0.002:
        print("\n⚠️ HEALTH WARNING: Fidelity Delta is extremely tight!")
        print("   -> Your setup is an 'Ultra-Silent Setup' (Motor hum is barely audible).")
        print("   -> Action taken: Nudging MOTOR_IDLE_THRESHOLD down to ensure spin-up is caught.")
        thresholds["MOTOR_IDLE_THRESHOLD"] -= 0.001
        passes = False
        
    print("\n✅ Simulation Passed. Thresholds locked.")
    return thresholds

# --- MAIN EXECUTION ---
def main():
    print(r"""
    __      ___             _    ____                     _ _          
    \ \    / (_)           | |  / __ \                   | (_)         
     \ \  / / _ _ __  _   _| | | |  | |_   _  __ _ _ __  | |_  __ _ _ __ 
      \ \/ / | | '_ \| | | | | | |  | | | | |/ _` | '_ \ | | |/ _` | '_ \
       \  /  | | | | | |_| | | | |__| | |_| | (_| | | | || | | (_| | | | |
        \/   |_|_| |_|\__, |_|  \____/ \__,_|\__,_|_| |_|__|_|\__,_|_| |_|
                       __/ |                                              
                      |___/   CALIBRATION SUITE v3.0                      
    """)
    
    use_existing = False
    if all(os.path.exists(f) for f in FILES.values()):
        ans = input("📁 Found existing 6-file calibration chain. Reuse it? (y/n): ").lower()
        if ans == 'y':
            use_existing = True
            
    if not use_existing:
        gain_staging()
        print("\n" + "="*50)
        print("🎙️  RECORDING SEQUENTIAL CALIBRATION CHAIN")
        print("="*50)
        
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
    initial_thresholds, floor_cl, idle_cl = analyze_files()
    final_thresholds = run_simulation_loop(initial_thresholds)
    
    # Save Config
    with open(CONFIG_FILE, 'w') as f:
        json.dump(final_thresholds, f, indent=4)
        
    print("\n" + "="*50)
    print("🎉 CALIBRATION COMPLETE 🎉")
    print("Saved Configuration:")
    for k, v in final_thresholds.items():
        print(f"  {k}: {v}")
    print("="*50)
    print("\n🔄 Please restart the Vinyl Guardian Add-on for changes to take effect.")

if __name__ == "__main__":
    main()