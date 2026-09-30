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

from config import SHARE_DIR, RECORDING_DIR, AUTO_CALIB_FILE, RATE, CHANNELS, CHUNK
from audio_math import RUNOUT_RPM_INTERVALS
from detector import GuardianDetector, pcm16_to_mono
from calibration_quality import assess_calibration
from profile_manager import ProfileManager
from regression import (
    collect_labelled_event_clips,
    compare_profiles,
    save_regression_report,
)

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
CALIB_DIR = os.path.join(RECORDING_DIR, "calibration_data")
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

def chunked_metrics(data, chunk_size=CHUNK):
    chunks = len(data) // chunk_size
    rms_v, hfer_v, crest_v = [], [], []
    for i in range(chunks):
        c = data[i*chunk_size:(i+1)*chunk_size]
        rms_v.append(get_rms(c))
        hfer_v.append(get_hfer(c))
        crest_v.append(get_crest(c))
    return np.array(rms_v), np.array(hfer_v), np.array(crest_v)

def chunked_rms(data, chunk_size=CHUNK):
    chunks = len(data) // chunk_size
    rms_arr = np.zeros(chunks)
    for i in range(chunks):
        rms_arr[i] = get_rms(data[i*chunk_size:(i+1)*chunk_size])
    return rms_arr

def chunked_music_rms(data, chunk_size=CHUNK):
    chunks = len(data) // chunk_size
    rms_arr = np.zeros(chunks)
    for i in range(chunks):
        rms_arr[i] = get_music_rms(data[i*chunk_size:(i+1)*chunk_size])
    return rms_arr

def chunked_hfer(data, chunk_size=CHUNK):
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
    # Return mono analysis samples so calibration prompts and live detection
    # see the same signal representation.
    audio_data = pcm16_to_mono(bytes(raw_audio), CHANNELS)
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
        
    print_log("⏺️ STEADY STATE (25s): Capturing extended runout analysis...")
    chunk_b, _ = record_chunk(25.0)
    raw_bytes.extend(chunk_b)
    
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(raw_bytes)
        
    print_log(f"✅ Saved dynamic transition to {os.path.basename(filename)}")
    time.sleep(1)

def set_mic_volume(vol_pct):
    target = os.environ.get("PULSE_SOURCE") or "@DEFAULT_SOURCE@"
    try:
        subprocess.run(
            ["pactl", "set-source-mute", target, "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        subprocess.run(
            ["pactl", "set-source-volume", target, f"{vol_pct}%"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except Exception:
        pass

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
def simulate_timeline(data, thresholds, state):
    """
    Replay calibration audio through the exact same GuardianDetector used live.

    The previous calibration simulator independently reimplemented the live
    rules, which meant a calibration could pass while production behaved
    differently.  Keeping the detector object in state also preserves
    hysteresis and runout phase between sequential calibration recordings.
    """
    chunk_size = 2048
    chunks = len(data) // chunk_size

    detector = state.get("_detector")
    if detector is None:
        detector = GuardianDetector(thresholds, rate=RATE, channels=1)

    current_power = state.get("current_power", "Off")
    current_status = state.get("current_status", "Powered Off")
    transitions = [{
        "time": 0.0,
        "power": current_power,
        "status": current_status,
        "log": f"   -> 0.0s : [INITIAL] Power [{current_power}] | Status [{current_status}]"
    }]

    base_time = detector.last_now if detector.last_now is not None else 0.0

    for i in range(chunks):
        chunk = data[i * chunk_size:(i + 1) * chunk_size]
        local_time = (i + 1) * chunk_size / RATE
        frame = detector.update_mono(chunk, base_time + local_time)

        p_state = "On" if frame["turntable_on"] else "Off"
        s_state = frame["status"]

        if p_state != current_power or s_state != current_status:
            rhythm = ""
            if frame["runout_locked"]:
                rhythm = (
                    f" | Rhythm [{frame['runout_rpm']} RPM, "
                    f"{frame['runout_confidence']:.0%}, "
                    f"{frame['runout_support']} hits]"
                )
            transitions.append({
                "time": local_time,
                "power": p_state,
                "status": s_state,
                "log": (
                    f"   -> {local_time:.1f}s : Power [{p_state}] | "
                    f"Status [{s_state}]{rhythm}"
                )
            })
            current_power, current_status = p_state, s_state

    next_state = {
        "_detector": detector,
        "current_power": current_power,
        "current_status": current_status,
        # Legacy keys are retained for report/debug compatibility.
        "turntable_on": detector.turntable_on,
        "power_score": int(round(detector.motor_confidence * 100.0)),
        "consecutive_music": 1 if detector.music_active else 0,
        "has_played_music": detector.has_played_music,
        "last_music_time": detector.last_music_time,
        "last_rhythm_time": detector.runout.last_candidate_time,
        "pop_history": list(detector.runout.events),
        "rhythm_locked": detector.runout.locked,
    }

    return transitions, current_power, current_status, next_state


def calculate_hardware_thresholds(files):
    print_log("\n" + "="*70)
    print_log("🧠 THE GUARDIAN ENGINE CALIBRATION (V8: SHARED DETECTOR)")
    print_log("="*70)
    
    print_log("\n[STAGE 1: BASELINE NOISE]")
    floor_data = load_wav(files["floor"])
    baseline_rms, baseline_hfer, baseline_crest = chunked_metrics(floor_data)
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
    hfer_max = p_hfer_max * 1.60
    
    crest_min = p_crest_min * 0.85
    crest_max = p_crest_max * 2.00

    safe_floor = float(baseline_median * 1.5)
    if rms_min < safe_floor:
        print_log(f"   [INFO] Floor Guard Activated: Raised volume floor from {rms_min:.6f} to {safe_floor:.6f}")
        rms_min = safe_floor

    print_log(f"   [EXTRACTED] Volume Window: {rms_min:.6f} to {rms_max:.6f}")
    print_log(f"   [EXTRACTED] Pitch Window:  {hfer_min:.4f} to {hfer_max:.4f}")
    print_log(f"   [EXTRACTED] Crest Window:  {crest_min:.2f} to {crest_max:.2f}")

    print_log("\n[STAGE 3: ROOM NOISE & DISTURBANCE]")
    disturb_data = load_wav(files["disturbance"])
    d_rms, d_hfer, d_crest = chunked_metrics(disturb_data)
    max_room_transient = float(np.max(d_rms))
    print_log(f"   [EXTRACTED] Max Ambient Transient: {max_room_transient:.6f}")

    print_log("\n[STAGE 4: THE MASTER TRANSITION]")
    trans_data = load_wav(files["transition"])
    
    music_chunk = trans_data[int(25 * RATE) : int(35 * RATE)]
    m_rms_arr = chunked_music_rms(music_chunk)
    valid_m_rms = m_rms_arr[m_rms_arr > (baseline_median * 2.0)]
    
    if len(valid_m_rms) > 0:
        raw_music_min = float(np.percentile(valid_m_rms, 5))
        music_threshold = max(raw_music_min * 0.80, baseline_median * 1.5)
    else:
        music_threshold = baseline_median * 2.0
        
    music_hold_threshold = max(baseline_median * 1.2, 0.0005)
    
    if music_hold_threshold >= (music_threshold * 0.85):
        music_hold_threshold = music_threshold * 0.60

    # Explicitly labelled missed-music clips can teach calibration about
    # unusually quiet records without globally lowering the threshold on a
    # guess. Only chunks clearly above the baseline are considered.
    missed_files = sorted(glob.glob(os.path.join(RECORDING_DIR, "missed_music_*.wav")))[-10:]
    missed_music_values = []
    for missed_file in missed_files:
        try:
            missed_data = load_wav(missed_file)
            vals = chunked_music_rms(missed_data)
            vals = vals[vals > (baseline_median * 1.20)]
            if len(vals):
                missed_music_values.extend(vals.tolist())
        except Exception:
            pass

    if missed_music_values:
        learned_music_floor = float(np.percentile(missed_music_values, 10))
        learned_music_threshold = max(
            baseline_median * 1.5,
            learned_music_floor * 0.78,
        )
        if learned_music_threshold < music_threshold:
            print_log(
                f"   [LEARNED] Missed-music examples lower trigger "
                f"{music_threshold:.6f} -> {learned_music_threshold:.6f}"
            )
            music_threshold = learned_music_threshold
            music_hold_threshold = min(
                music_hold_threshold,
                max(baseline_median * 1.2, music_threshold * 0.60),
            )

    print_log(f"   [EXTRACTED] Music Trigger Threshold: {music_threshold:.6f}")
    print_log(f"   [EXTRACTED] Music Hold Threshold:    {music_hold_threshold:.6f}")
    print_log(f"   [ANALYSIS] Threshold Gap: {(music_threshold - music_hold_threshold):.6f} (If < 0.001, room may be too noisy)")
    
    runout_chunk = trans_data[-int(20 * RATE):]
    runout_chunks_n = len(runout_chunk) // CHUNK
    runout_crests, runout_amps = [], []

    for i in range(runout_chunks_n):
        chunk = runout_chunk[i*CHUNK:(i+1)*CHUNK]
        r = get_rms(chunk)
        if r > 0:
            m_val = np.max(np.abs(chunk))
            c = m_val / r
            if c > 2.5:
                runout_crests.append(c)
                runout_amps.append(m_val)

    if len(runout_crests) > 2:
        pop_crest_threshold = max(3.0, float(np.percentile(runout_crests, 50)) * 0.85)
        pop_amplitude_threshold = max(floor_max_amp * 1.1, float(np.percentile(runout_amps, 25)) * 0.70)
        print_log(f"   [EXTRACTED] Runout Pop Sharpness (Crest): {pop_crest_threshold:.2f}")
        print_log(f"   [EXTRACTED] Runout Pop Amplitude: {pop_amplitude_threshold:.6f}")
    else:
        print_log("   [DEBUG] Runout extraction lacked clear pops. Using safe defaults.")
        pop_crest_threshold = 3.5
        pop_amplitude_threshold = floor_max_amp * 1.5

    motor_power_ceiling = motor_median_rms * 4.0

    # V8 learns broad class profiles as an additional source of evidence.
    # These do not replace the safety windows above; they help distinguish
    # motor-like audio from room noise that happens to land inside them.
    def build_profile(rms_arr, hfer_arr, crest_arr):
        def stat(arr, floor):
            arr = np.asarray(arr, dtype=float)
            arr = arr[np.isfinite(arr)]
            if len(arr) == 0:
                return {"median": 0.0, "scale": floor}
            med = float(np.median(arr))
            mad = float(np.median(np.abs(arr - med)))
            scale = max(1.4826 * mad, abs(med) * 0.05, floor)
            return {
                "median": round(med, 8),
                "scale": round(scale, 8),
            }

        return {
            "rms": stat(rms_arr, 1e-6),
            "hfer": stat(hfer_arr, 1e-4),
            "crest": stat(crest_arr, 1e-3),
        }

    negative_rms = [np.asarray(d_rms)]
    negative_hfer = [np.asarray(d_hfer)]
    negative_crest = [np.asarray(d_crest)]

    ghost_files = sorted(glob.glob(os.path.join(RECORDING_DIR, "ghost_trigger_*.wav")))[-10:]
    labelled_off_files = [
        wav_path
        for wav_path, expectation, _label
        in collect_labelled_event_clips(RECORDING_DIR)
        if expectation == "off"
    ][-10:]
    negative_example_files = list(dict.fromkeys(ghost_files + labelled_off_files))

    learned_ghost_chunks = 0
    for ghost_file in negative_example_files:
        try:
            ghost_data = load_wav(ghost_file)
            g_rms, g_hfer, g_crest = chunked_metrics(ghost_data)
            mask = g_rms >= (rms_min * 0.60)
            if np.any(mask):
                negative_rms.append(g_rms[mask])
                negative_hfer.append(g_hfer[mask])
                negative_crest.append(g_crest[mask])
                learned_ghost_chunks += int(np.sum(mask))
        except Exception:
            pass

    motor_profile = build_profile(m_rms, m_hfer, m_crest)
    negative_profile = build_profile(
        np.concatenate(negative_rms),
        np.concatenate(negative_hfer),
        np.concatenate(negative_crest),
    )

    print_log("   [LEARNED] Robust motor profile added to detector.")
    if learned_ghost_chunks:
        print_log(
            f"   [LEARNED] Included {learned_ghost_chunks} chunks from "
            f"{len(negative_example_files)} historical false-positive/"
            f"explicit-off recording(s)."
        )

    thresholds = {
        "rms_min": round(rms_min, 6), "rms_max": round(rms_max, 6),
        "hfer_min": round(hfer_min, 5), "hfer_max": round(hfer_max, 5),
        "crest_min": round(crest_min, 3), "crest_max": round(crest_max, 3),
        "motor_power_threshold": round(rms_min, 6),
        "motor_power_ceiling": round(motor_power_ceiling, 6),
        "motor_hfer_threshold": round(hfer_max, 5),
        "motor_hfer_floor": round(hfer_min, 5),
        "music_threshold": round(music_threshold, 6),
        "music_hold_threshold": round(music_hold_threshold, 6),
        "detector_version": 2,
        "motor_profile": motor_profile,
        "negative_profile": negative_profile,
        "runout_crest_threshold": round(pop_crest_threshold, 3),
        "pop_amplitude_threshold": round(pop_amplitude_threshold, 6),
        "max_room_transient": round(max_room_transient, 6)
    }
    
    def states_in_order(transitions, *expected_statuses):
        last_idx = -1
        for s in expected_statuses:
            found = False
            for i, t in enumerate(transitions):
                if i > last_idx and t['status'] == s:
                    last_idx = i
                    found = True
                    break
            if not found: return False
        return True

    def any_bad_status(transitions, *bad_statuses):
        return any(t['status'] in bad_statuses for t in transitions)

    print_log("\n" + "="*70)
    print_log("📜 THE DUAL-SENSOR ACID TEST (V7.4: PRODUCTION CORE)")
    print_log("   Running sequential physical recreation to verify logic locks...")
    print_log("="*70)

    sim_state = {
        "current_power": "Off", "current_status": "Powered Off", "turntable_on": False,
        "power_score": 0, "consecutive_music": 0, "has_played_music": False,
        "last_music_time": -10.0, "last_rhythm_time": -10.0, "pop_history": [], "rhythm_locked": False
    }

    print_log("\n🔕 [TEST 1: BASELINE NOISE]")
    print_log("   Expected Flow: Off -> Stays Off")
    trans, end_p, end_s, sim_state = simulate_timeline(floor_data, thresholds, sim_state)
    for t in trans: print_log(t['log'])
    passed = (end_p == "Off" and end_s == "Powered Off" and len(trans) == 1)
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — The silence floor is too high.")

    print_log(f"\n⚙️  [TEST 2: MOTOR HUM]")
    print_log(f"   Expected Flow: Off -> User turns motor ON -> On / Motor Idle")
    trans, end_p, end_s, sim_state = simulate_timeline(spinup_data, thresholds, sim_state)
    for t in trans: print_log(t['log'])
    passed = (end_p == "On" and end_s == "Motor Idle" and not any_bad_status(trans, "Playing", "Runout Groove", "Between Tracks"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Motor threshold misaligned or false trigger.")

    print_log(f"\n🎵 [TEST 3: THE MASTER TRANSITION]")
    print_log(f"   Expected Flow: Motor Idle -> Playing -> Between Tracks -> Motor Idle -> Runout Groove")
    trans, end_p, end_s, sim_state = simulate_timeline(trans_data, thresholds, sim_state)
    for t in trans: print_log(t['log'])
    passed = (end_p == "On" and end_s == "Runout Groove" and states_in_order(trans, "Playing", "Between Tracks", "Motor Idle", "Runout Groove"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Engine lost track of music or failed rhythm lock.")

    lift_data = load_wav(files["lift"])
    
    print_log(f"\n⬆️  [TEST 4: NEEDLE LIFT]")
    print_log(f"   Expected Flow: Runout Groove -> User lifts needle -> On / Motor Idle")
    trans, end_p, end_s, sim_state = simulate_timeline(lift_data, thresholds, sim_state)
    for t in trans: print_log(t['log'])
    passed = (end_p == "On" and end_s == "Motor Idle" and not any_bad_status(trans, "Playing", "Between Tracks"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Thump was falsely flagged as music, or state dropped prematurely.")

    powerdown_data = load_wav(files["powerdown"])
    print_log(f"\n🔌 [TEST 5: POWER DOWN]")
    print_log(f"   Expected Flow: Motor Idle -> User turns power OFF -> Off / Powered Off")
    trans, end_p, end_s, sim_state = simulate_timeline(powerdown_data, thresholds, sim_state)
    for t in trans: print_log(t['log'])
    passed = (end_p == "Off" and end_s == "Powered Off" and not any_bad_status(trans, "Playing", "Runout Groove", "Between Tracks"))
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Electrical pop triggered false states.")

    print_log("\n🗣️  [TEST 6: ROOM NOISE]")
    print_log("   Expected Flow: Off -> User talks/taps -> Stays Off")
    trans, end_p, end_s, sim_state = simulate_timeline(disturb_data, thresholds, sim_state)
    for t in trans: print_log(t['log'])
    passed = (end_p == "Off" and end_s == "Powered Off" and len(trans) == 1)
    print_log("   ✅ PASS" if passed else "   ❌ FAIL — Acoustic shield breached by transients.")

    return thresholds

def analyze_ghost_triggers(thresholds):
    print_log("\n" + "="*70)
    print_log("👻 SURGICAL GHOST ANALYSIS (The Prime Suspect Filter)")
    print_log("   Scanning chunks that passed the volume filters...")
    print_log("="*70)

    ghost_files = glob.glob(os.path.join(RECORDING_DIR, "ghost_trigger_*.wav"))
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
                      |___/   CALIBRATION SUITE v8 (Shared Detector)                      
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
        except Exception:
            pass

    print_log("\n" + "="*70)
    print_log("🧪 CALIBRATION QUALITY ASSESSMENT")
    print_log("="*70)
    try:
        quality = assess_calibration(FILES, thresholds)
    except Exception as e:
        quality = {
            "status": "warning",
            "warnings": [f"Quality assessment failed: {e}"],
            "critical": [],
        }

    print_log(f"   Quality status: {quality.get('status', 'unknown').upper()}")
    sep = quality.get("motor_off_separability_robust_z", {})
    if sep:
        print_log(
            "   Motor/off separation (robust z): "
            + ", ".join(f"{k}={v:.2f}" for k, v in sep.items())
        )
    if quality.get("music_floor_margin_db") is not None:
        print_log(
            f"   Quiet-music margin over floor: "
            f"{quality.get('music_floor_margin_db', 0.0):.1f} dB"
        )
    input_info = quality.get("input_channels", {})
    if input_info:
        print_log(
            f"   Input channel assessment: "
            f"{input_info.get('mode', 'unknown')} "
            f"(configured {input_info.get('configured_channels', CHANNELS)} ch)"
        )
    runout_info = quality.get("runout", {})
    if runout_info:
        print_log(
            f"   Runout estimate: {runout_info.get('estimated_rpm_median')} RPM, "
            f"phase jitter {runout_info.get('phase_jitter_ms_median')} ms"
        )
    for message in quality.get("warnings", []):
        print_log(f"   ⚠️ {message}")
    for message in quality.get("critical", []):
        print_log(f"   ❌ {message}")

    quality_path = os.path.join(SHARE_DIR, "calibration_quality.json")
    try:
        with open(quality_path, "w") as f:
            json.dump(quality, f, indent=2)
    except Exception:
        pass

    baseline_thresholds = {}
    if os.path.exists(AUTO_CALIB_FILE):
        try:
            with open(AUTO_CALIB_FILE, "r") as f:
                baseline_thresholds = json.load(f)
        except Exception:
            baseline_thresholds = {}

    print_log("\n" + "="*70)
    print_log("🧬 HISTORICAL REGRESSION GATE")
    print_log("="*70)
    try:
        regression = compare_profiles(
            thresholds,
            baseline_thresholds,
            RECORDING_DIR,
            calibration_files=FILES,
        )
    except Exception as e:
        regression = {
            "can_compare": False,
            "accepted": True,
            "reason": f"Regression suite could not run: {e}",
            "candidate": {},
            "baseline": None,
        }

    print_log(f"   {regression.get('reason', 'No regression result.')}")
    candidate_reg = regression.get("candidate") or {}
    if candidate_reg:
        print_log(
            f"   Candidate: {candidate_reg.get('fixture_count', 0)} fixtures, "
            f"penalty {candidate_reg.get('total_penalty', 0.0):.2f}, "
            f"severe failures {candidate_reg.get('severe_failures', 0)}"
        )
    baseline_reg = regression.get("baseline") or {}
    if baseline_reg:
        print_log(
            f"   Current:   {baseline_reg.get('fixture_count', 0)} fixtures, "
            f"penalty {baseline_reg.get('total_penalty', 0.0):.2f}, "
            f"severe failures {baseline_reg.get('severe_failures', 0)}"
        )

    regression_path = os.path.join(SHARE_DIR, "regression_report.json")
    try:
        save_regression_report(regression_path, regression)
    except Exception:
        pass

    # Never replace a working profile with a regression or critically weak
    # candidate. On a first-ever calibration there is no profile to preserve,
    # so the candidate is promoted but the quality warning remains visible.
    has_previous_profile = bool(baseline_thresholds)
    quality_safe = quality.get("status") != "weak"
    promote = bool(regression.get("accepted", True)) and (
        quality_safe or not has_previous_profile
    )

    profile_manager = ProfileManager(SHARE_DIR, AUTO_CALIB_FILE)
    metadata = {
        "calibration_files": {
            key: os.path.basename(path) for key, path in FILES.items()
        },
        "reuse_calibration_audio": bool(use_existing),
        "mic_volume": thresholds.get("mic_volume"),
        "audio_source": os.environ.get("PULSE_SOURCE") or "@DEFAULT_SOURCE@",
        "detector_version": thresholds.get("detector_version"),
    }

    try:
        saved_profile = profile_manager.save_candidate(
            thresholds,
            metadata=metadata,
            quality=quality,
            regression=regression,
            accepted=promote,
        )
        profile_id = saved_profile.get("profile_id")
    except Exception as e:
        saved_profile = None
        profile_id = None
        print_log(f"   ⚠️ Could not version detector profile: {e}")
        if promote:
            with open(AUTO_CALIB_FILE, "w") as f:
                json.dump(thresholds, f, indent=4)

    active_for_local = thresholds if promote else baseline_thresholds
    if active_for_local:
        with open("config.json", "w") as f:
            json.dump(active_for_local, f, indent=4)

    with open(REPORT_FILE, "w") as f:
        f.write("\n".join(report_log))
    
    if promote:
        print_log("\n🎉 CALIBRATION COMPLETE — PROFILE PROMOTED 🎉")
        if profile_id:
            print_log(f"   Active profile: {profile_id}")
    else:
        print_log("\n🛡️ CALIBRATION COMPLETE — EXISTING PROFILE RETAINED")
        if profile_id:
            print_log(f"   Candidate saved for analysis: {profile_id}")
        print_log(
            "   The candidate did not clear the quality/regression gate, so "
            "the previous active detector remains untouched."
        )

    for key, value in thresholds.items():
        print_log(f"   - candidate {key}: {value}")

    # Rewrite once more so the final promotion verdict is included.
    with open(REPORT_FILE, "w") as f:
        f.write("\n".join(report_log))
        
    print("\n📄 A copy of this report was saved to: " + REPORT_FILE, flush=True)
    print("🔄 Please disable CALIBRATION_MODE in your config and RESTART the Add-on.", flush=True)
    while True:
        time.sleep(3600)

if __name__ == "__main__":
    run_calibration()