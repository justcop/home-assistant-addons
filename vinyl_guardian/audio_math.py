import numpy as np

def calculate_audio_levels(data):
    try:
        audio_data = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        if len(audio_data) <= 1:
            return 0.0, 0.0, 1.0
            
        raw_rms = float(np.sqrt(np.mean(np.square(audio_data)))) / 32768.0
        
        filtered_data = audio_data[1:] - 0.95 * audio_data[:-1]
        music_rms = float(np.sqrt(np.mean(np.square(filtered_data)))) / 32768.0
        
        peak = np.max(np.abs(audio_data)) / 32768.0
        crest = peak / raw_rms if raw_rms > 0 else 1.0
        
        return raw_rms, music_rms, crest
    except Exception:
        return 0.0, 0.0, 1.0

def calculate_deep_metrics(data):
    audio_data = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    if len(audio_data) <= 1:
        return None
   
    rms = float(np.sqrt(np.mean(np.square(audio_data)))) / 32768.0
    
    filtered_data = audio_data[1:] - 0.95 * audio_data[:-1]
    music_rms = float(np.sqrt(np.mean(np.square(filtered_data)))) / 32768.0
    
    peak = np.max(np.abs(audio_data)) / 32768.0
    crest = peak / rms if rms > 0 else 1.0
    
    if rms < 0.0001:
        hfer = 0.0
    else:
        hf_data = audio_data[1:] - audio_data[:-1] 
        hf_rms = float(np.sqrt(np.mean(np.square(hf_data)))) / 32768.0
        hfer = hf_rms / rms
   
    return {"rms": rms, "music_rms": music_rms, "crest": crest, "hfer": float(hfer)}

# ---------------------------------------------------------------------------
# SHARED POP & RHYTHM-LOCK LOGIC
# ---------------------------------------------------------------------------

# Only the two physical turntable speeds (33⅓ and 45 RPM).
RUNOUT_RPM_INTERVALS = [(1.20, 1.46), (1.65, 1.95)]

def is_valid_pop(raw_rms, peak, thresholds):
    """
    Return True if the current chunk contains a genuine runout-groove pop.
    """
    if raw_rms <= 0:
        return False
    crest = peak / raw_rms
    return (
        crest >= thresholds["runout_crest_threshold"]
        and peak >= thresholds["pop_amplitude_threshold"]
        and raw_rms <= thresholds["motor_power_ceiling"]
    )

def update_rhythm_lock(pop_history, now, peak, rhythm_locked, last_rhythm_time):
    """
    Update pop_history with a new confirmed pop and re-evaluate rhythm lock.
    Requires a chain of at least 3 pops (2 consecutive intervals) to lock, 
    virtually eliminating false positives from room noise or random tapping.
    """
    # Expire stale entries and gracefully handle tuple length upgrades
    clean_history = []
    for item in pop_history:
        if len(item) == 2:
            t, v = item
            c = 1
        else:
            t, v, c = item
            
        if now - t <= 4.0:
            clean_history.append((t, v, c))
            
    pop_history = clean_history
    current_chain = 1

    # Check for a rhythmic match against history
    for pt_time, pt_val, pt_chain in reversed(pop_history):
        diff = now - pt_time
        for lo, hi in RUNOUT_RPM_INTERVALS:
            if lo <= diff <= hi:
                # Amplitude consistency: must be within ±50% of the prior pop
                if pt_val * 0.5 <= peak <= pt_val * 2.0:
                    current_chain = max(current_chain, pt_chain + 1)
                    # We require 3 sequential pops to trigger a confirmed runout lock
                    if current_chain >= 3:
                        rhythm_locked = True
                        last_rhythm_time = now
                break

    pop_history.append((now, peak, current_chain))
    return pop_history, rhythm_locked, last_rhythm_time

def calc_variance_boundary(low_val, low_std, high_val, high_std):
    gap = high_val - low_val
    if gap <= 0:
        return low_val + 0.0001
   
    total_noise = low_std + high_std
    if total_noise <= 0:
        return low_val + (gap * 0.5)
   
    ratio = low_std / total_noise
    ratio = max(0.2, min(0.8, ratio))
    return low_val + (gap * ratio)

def clean_stage_data(stage_metrics):
    cleaned = {}
    for k, v_list in stage_metrics.items():
        arr = np.array(v_list)
        med = float(np.median(arr))
        mad = float(np.median(np.abs(arr - med)))
        if mad == 0:
            mad = 1e-6
        threshold = med + (15 * mad)
        arr = np.where(arr > threshold, med, arr)
        cleaned[k] = arr.tolist()
    return cleaned