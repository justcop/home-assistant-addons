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