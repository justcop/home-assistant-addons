import sys
import os
import time
import wave
import subprocess
import alsaaudio
import numpy as np
from config import *
from audio_math import *

FORMAT = alsaaudio.PCM_FORMAT_S16_LE

def simulate_state_machine(calibration_data, t_mot, t_rum, t_cre, t_mus, h_mot, h_nee, debounce_chunks, is_silent_hw=False, t_hfer=0.0, t_ceil=0.015):
    power_max = int(RATE / CHUNK * h_mot)
    needle_max = int(RATE / CHUNK * h_nee)
    
    avg_pop_interval = (1.8 + 1.33) / 2.0
    pop_boost = int(RATE / CHUNK * (avg_pop_interval * 0.6))
    pop_boost = max(int(RATE / CHUNK * 1.0), min(int(RATE / CHUNK * 3.0), pop_boost))
   
    stages_order = ["STAGE_1_OFF", "STAGE_2_ON_IDLE", "STAGE_3_PLAYING", "STAGE_4_RUNOUT", "STAGE_5_LIFTED", "STAGE_6_OFF"]
   
    turntable_on, needle_down = False, False
    has_played_music = False
    power_score, needle_score = 0, 0
   
    for stage in stages_order:
        expect_on = stage in ["STAGE_2_ON_IDLE", "STAGE_3_PLAYING", "STAGE_4_RUNOUT", "STAGE_5_LIFTED"]
        expect_down = stage in ["STAGE_3_PLAYING", "STAGE_4_RUNOUT"]
        expect_music = stage == "STAGE_3_PLAYING"
       
        if stage in ["STAGE_1_OFF", "STAGE_2_ON_IDLE", "STAGE_3_PLAYING", "STAGE_6_OFF"]:
            turntable_on = expect_on
            power_score = power_max if expect_on else 0
            needle_down = expect_down
            needle_score = needle_max if expect_down else 0
            has_played_music = False

        chunks_rms = calibration_data[stage]["raw_chunks"]["rms"]
        chunks_music = calibration_data[stage]["raw_chunks"]["music_rms"]
        chunks_crest = calibration_data[stage]["raw_chunks"]["crest"]
        chunks_hfer = calibration_data[stage]["raw_chunks"]["hfer"]
       
        grace_period_chunks = int(max(power_max, needle_max) * 1.5)
        music_triggered = False
        trigger_chunks = 0
        eval_chunks, power_correct, needle_correct = 0, 0, 0
       
        for i in range(len(chunks_rms)):
            rms, m_rms, crest, hfer = chunks_rms[i], chunks_music[i], chunks_crest[i], chunks_hfer[i]
            motor_on_cond = False
            
            if rms > t_mot:
                if rms < t_ceil:
                    motor_on_cond = True
                    if t_hfer > 0.0 and hfer > t_hfer:
                        motor_on_cond = False
                else:
                    if turntable_on: motor_on_cond = True
                    else: motor_on_cond = False
            
            if motor_on_cond:
                power_score = min(power_score + 1, power_max)
                if power_score >= power_max: turntable_on = True
            else:
                power_score = max(power_score - 1, 0)
                if power_score <= 0: 
                    turntable_on, has_played_music = False, False
                   
            is_dust_pop = crest >= t_cre
            if is_dust_pop: needle_score = min(needle_score + pop_boost, needle_max)
            elif rms >= t_rum: needle_score = min(needle_score + 1, needle_max)
            else: needle_score = max(needle_score - 1, 0)
           
            needle_down = needle_score > (needle_max * 0.5)
           
            if m_rms > t_mus and not is_dust_pop:
                trigger_chunks += 1
                if trigger_chunks >= debounce_chunks: 
                    music_triggered, has_played_music = True, True
            else:
                trigger_chunks = 0

            effective_needle = needle_down
            if is_silent_hw and turntable_on and has_played_music: effective_needle = True

            if i > grace_period_chunks:
                eval_chunks += 1
                if is_silent_hw and stage in ["STAGE_1_OFF", "STAGE_6_OFF"]: power_correct += 1
                else:
                    if turntable_on == expect_on: power_correct += 1
                
                if is_silent_hw:
                    if stage != "STAGE_3_PLAYING": needle_correct += 1
                    else:
                        if effective_needle == expect_down: needle_correct += 1
                else:
                    if stage == "STAGE_4_RUNOUT": needle_correct += 1 
                    else:
                        if effective_needle == expect_down: needle_correct += 1
                   
        if eval_chunks > 0:
            p_acc = power_correct / eval_chunks
            n_acc = needle_correct / eval_chunks
            if p_acc < 0.95: return f"{stage}: Power mostly wrong (Acc: {p_acc*100:.1f}%)."
            if n_acc < 0.92: return f"{stage}: Needle mostly wrong (Acc: {n_acc*100:.1f}%)."
               
        if expect_music and not music_triggered: return f"{stage}: Music expected but not reliably detected."
        if not expect_music and music_triggered: return f"{stage}: Music falsely detected on static/noise."
           
    return "PASS"

def run_calibration():
    print("\n\n")
    log("==================================================")
    log("🎛️ VINYL GUARDIAN: AUTO-CALIBRATION WIZARD 🎛️")
    log("==================================================")
   
    reuse_audio = adv.get("reuse_calibration_audio", False)
    if reuse_audio: log("♻️ 'reuse_calibration_audio' is ENABLED. Will attempt to load previous recordings.")

    try:
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NORMAL, device='default', channels=CHANNELS, rate=RATE, format=FORMAT, periodsize=CHUNK)
    except Exception as e:
        log(f"🚨 ALSA Error: {e}"); sys.exit(1)

    current_vol = MIC_VOLUME
    calibration_data = {}
   
    def record_stage(stage_id, duration_secs):
        wav_path = os.path.join(SHARE_DIR, f"calib_{stage_id}.wav")
        stage_metrics = {"rms": [], "music_rms": [], "crest": [], "hfer": []}
        t_chunks = int(RATE / CHUNK * duration_secs)
        shave_chunks = int(RATE / CHUNK * 10.0)
        
        reuse_audio_for_stage = False
        if reuse_audio and os.path.exists(wav_path):
            log(f"♻️ Reusing existing audio file for {stage_id}...")
            try:
                with wave.open(wav_path, 'rb') as wf: raw_bytes = wf.readframes(wf.getnframes())
                chunk_bytes = CHUNK * CHANNELS * 2
                chunks = 0
                for i in range(0, len(raw_bytes), chunk_bytes):
                    chunk_data = raw_bytes[i:i + chunk_bytes]
                    if len(chunk_data) == chunk_bytes:
                        if chunks >= shave_chunks:
                            metrics = calculate_deep_metrics(chunk_data)
                            if metrics:
                                for k in stage_metrics.keys(): stage_metrics[k].append(metrics[k])
                        chunks += 1
                log("✅ Local file processed.")
                reuse_audio_for_stage = True
            except Exception as e:
                log(f"⚠️ Failed to load {wav_path}: {e}. Falling back to live recording.")
                reuse_audio_for_stage = False

        if not reuse_audio_for_stage:
            log(f"🔴 Recording {duration_secs} seconds of audio... Please wait.")
            chunks = 0; buffer = bytearray()
            while chunks < t_chunks:
                length, data = inp.read()
                if length > 0:
                    buffer.extend(data)
                    if chunks >= shave_chunks:
                        metrics = calculate_deep_metrics(data)
                        if metrics:
                            for k in stage_metrics.keys(): stage_metrics[k].append(metrics[k])
                    chunks += 1
            try:
                with wave.open(wav_path, "wb") as wf:
                    wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(buffer)
                log(f"💾 Saved full raw audio to {wav_path} (First 10s shaved from internal math)")
            except Exception as e: log(f"⚠️ Failed to save {stage_id} wav: {e}")
            log("✅ Capture complete.")
   
        if stage_id in ["STAGE_1_OFF", "STAGE_2_ON_IDLE", "STAGE_5_LIFTED", "STAGE_6_OFF"]:
            stage_metrics = clean_stage_data(stage_metrics)

        summary = {}
        for k, v_list in stage_metrics.items():
            if v_list:
                arr = np.array(v_list)
                summary[k] = {"median": float(np.median(arr)), "mean": float(np.mean(arr)), "min": float(np.min(arr)), "max": float(np.max(arr)), "std_dev": float(np.std(arr))}
       
        calibration_data[stage_id] = {"raw_chunks": stage_metrics, "summary": summary}

    if not reuse_audio:
        log("\n" + "="*50); log("▶️ ACTION 1: 💽 Drop the needle onto a 🔊 LOUD playing record."); log("="*50)
        log("⏳ Waiting 10 seconds for you to prepare...")
        for i in range(10): inp.read(); time.sleep(1)
           
        log(f"⚙️ Calibrating microphone volume...")
        good_passes, target_chunks = 0, int(RATE / CHUNK * 3)
       
        while good_passes < 2:
            try: subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{current_vol}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError: pass
            for _ in range(5): inp.read()
               
            buffer = bytearray(); chunks = 0
            while chunks < target_chunks:
                length, data = inp.read()
                if length > 0: buffer.extend(data); chunks += 1
           
            audio_data = np.frombuffer(buffer, dtype=np.int16)
            peak = int(np.max(np.abs(audio_data.astype(np.int32)))) if len(audio_data) > 0 else 0
           
            if peak > 25000:
                current_vol = max(1, current_vol - (5 if peak > 30000 else 2))
                if DEBUG: log(f"📉 [Peak: {peak:5d}] - Auto-decreasing to {current_vol}%...")
                good_passes = 0; time.sleep(0.5)
            elif peak < 15000:
                current_vol = min(100, current_vol + (5 if peak < 10000 else 2))
                if DEBUG: log(f"📈 [Peak: {peak:5d}] - Auto-increasing to {current_vol}%...")
                good_passes = 0; time.sleep(0.5)
            else:
                log(f"✅ Volume successfully locked at {current_vol}%!")
                good_passes += 1
               
            if current_vol == 1 or current_vol == 100: break

    if not reuse_audio:
        log("\n" + "="*50); log("▶️ ACTION 2: 🛑 STOP the record and turn Turntable 🔌 OFF."); log("="*50)
        log("⏳ Waiting 10 seconds for you to prepare...")
        for _ in range(10): inp.read(); time.sleep(1)
    record_stage("STAGE_1_OFF", 25)

    if not reuse_audio:
        log("\n" + "="*50); log("▶️ ACTION 3: ⚡ Turn Turntable ON (🔄 Motor spinning, ⬆️ Needle UP)."); log("="*50)
        log("⏳ Waiting 10 seconds for you to prepare...")
        for _ in range(10): inp.read(); time.sleep(1)
    record_stage("STAGE_2_ON_IDLE", 25)

    if not reuse_audio:
        log("\n" + "="*50); log("▶️ ACTION 4: ⬇️ Drop the needle NEAR THE END of a playing track 🎵."); log("="*50)
        log("⏳ Waiting 10 seconds for you to prepare...")
        for _ in range(10): inp.read(); time.sleep(1)
    record_stage("STAGE_3_PLAYING", 30)

    if not reuse_audio:
        temp_music_thresh = calibration_data["STAGE_2_ON_IDLE"]["summary"]["music_rms"]["max"] * 1.25
        log("\n" + "="*50); log("▶️ ACTION 5: ⏳ Let the track finish playing into the 〰️ Runout Groove."); log("="*50)
        log("⏳ Listening for the music to stop...")
        silence_chunks, target_silence = 0, int(RATE / CHUNK * 15.0) 
        runout_timeout, timeout_chunks = int(RATE / CHUNK * 600.0), 0
        while True:
            length, data = inp.read()
            if length > 0:
                timeout_chunks += 1
                if timeout_chunks > runout_timeout:
                    log("⚠️ Runout detection timeout reached (10 mins). Proceeding.")
                    break
                _, music_rms, _ = calculate_audio_levels(data)
                if music_rms < temp_music_thresh:
                    silence_chunks += 1
                    if silence_chunks >= target_silence:
                        log("🔇 15-second floor reached. Runout Groove detected! Settling...")
                        break
                else: silence_chunks = 0
        for _ in range(int(RATE / CHUNK * 3.0)): inp.read()
    
    record_stage("STAGE_4_RUNOUT", 30)

    if not reuse_audio:
        log("\n" + "="*50); log("▶️ ACTION 6: ⬆️ Lift the needle (🔄 Motor still ON, ⬆️ Needle UP)."); log("="*50)
        log("⏳ Waiting 10 seconds for you to prepare...")
        for _ in range(10): inp.read(); time.sleep(1)
    record_stage("STAGE_5_LIFTED", 25)

    if not reuse_audio:
        log("\n" + "="*50); log("▶️ ACTION 7: 🔌 Turn the Turntable OFF."); log("="*50)
        log("⏳ Waiting 10 seconds for you to prepare...")
        for _ in range(10): inp.read(); time.sleep(1)
    record_stage("STAGE_6_OFF", 25)
    inp.close()

    log("\n📊 --- CALIBRATION STAGE ANALYSIS (CLEANED) ---")
    for st in ["STAGE_1_OFF", "STAGE_2_ON_IDLE", "STAGE_3_PLAYING", "STAGE_4_RUNOUT", "STAGE_5_LIFTED", "STAGE_6_OFF"]:
        if st in calibration_data:
            s = calibration_data[st]["summary"]
            log(f"🔹 {st}:")
            log(f"   ┣ RMS   : Median {s['rms']['median']:.6f} | Mean {s['rms']['mean']:.6f} | Max {s['rms']['max']:.6f}")
            log(f"   ┣ Music : Median {s['music_rms']['median']:.6f} | Mean {s['music_rms']['mean']:.6f} | Max {s['music_rms']['max']:.6f}")
            log(f"   ┣ Crest : Median {s['crest']['median']:.3f} | Mean {s['crest']['mean']:.3f} | Max {s['crest']['max']:.3f}")
            log(f"   ┗ HFER  : Median {s['hfer']['median']:.4f} | Mean {s['hfer']['mean']:.4f}")
    log("----------------------------------------------\n")
    log("⚙️ Analyzing data and running statistical simulations... This may take a minute.")

    s1, s2, s3, s4, s5, s6 = calibration_data["STAGE_1_OFF"]["summary"], calibration_data["STAGE_2_ON_IDLE"]["summary"], calibration_data["STAGE_3_PLAYING"]["summary"], calibration_data["STAGE_4_RUNOUT"]["summary"], calibration_data["STAGE_5_LIFTED"]["summary"], calibration_data["STAGE_6_OFF"]["summary"]
   
    off_val, off_std = min(s1["rms"]["median"], s6["rms"]["median"]), min(s1["rms"]["std_dev"], s6["rms"]["std_dev"])
    on_val, on_std = (s2["rms"]["median"] + s5["rms"]["median"]) / 2.0, (s2["rms"]["std_dev"] + s5["rms"]["std_dev"]) / 2.0
    runout_val, runout_std = s4["rms"]["median"], s4["rms"]["std_dev"]
    music_idle_val, music_idle_std = max(s2["music_rms"]["median"], s4["music_rms"]["median"], s5["music_rms"]["median"]), max(s2["music_rms"]["std_dev"], s4["music_rms"]["std_dev"], s5["music_rms"]["std_dev"])
    music_play_val, music_play_std, play_val = s3["music_rms"]["median"], s3["music_rms"]["std_dev"], s3["rms"]["median"]
    hfer_off, hfer_on = min(s1["hfer"]["median"], s6["hfer"]["median"]), max(s2["hfer"]["median"], s5["hfer"]["median"])
    idle_max_crest, idle_median = max(s2["crest"]["max"], s5["crest"]["max"]), max(s2["rms"]["median"], s5["rms"]["median"])
    guess_motor_hfer = 0.0
    
    silence_ratio = on_val / play_val if play_val > 0 else 1.0
    is_silent_hw = silence_ratio < 0.15 or on_val < 0.0035
    runout_crest_std = s4["crest"]["std_dev"]

    if is_silent_hw:
        log("\n👻 SILENT HARDWARE DETECTED: Utilizing Advanced Rhythm Tracker & Stability Buffers.")
        guess_motor = calc_variance_boundary(off_val, off_std, on_val, on_std)
        guess_ceiling = max(idle_median * 2.5, guess_motor * 1.5)
        log(f"   ┣ Strict Motor Ceiling Locked: {guess_ceiling:.4f}")
        if hfer_off > (hfer_on * 1.5):
            guess_motor_hfer = (hfer_off + hfer_on) / 2.0
            log(f"   ┗ HFER Hiss Detection Armed (Threshold: {guess_motor_hfer:.4f})")
        guess_rumble = max(0.025, play_val * 0.4) 
        guess_crest = max(idle_max_crest + 0.5, s4["crest"]["median"] + (runout_crest_std * 2.5))
        guess_m_hyst = 1.0 
    else:
        guess_motor, guess_ceiling = calc_variance_boundary(off_val, off_std, on_val, on_std), max(idle_median * 2.5, calc_variance_boundary(off_val, off_std, on_val, on_std) * 1.5)
        guess_rumble, guess_crest = calc_variance_boundary(on_val, on_std, runout_val, runout_std), max(idle_max_crest + 0.5, s4["crest"]["median"] + (runout_crest_std * 2.5))
        guess_m_hyst = 1.5 
        
    guess_music = calc_variance_boundary(music_idle_val, music_idle_std, music_play_val, music_play_std)
    guess_debounce = 8 
    if music_idle_std > 0.005: guess_debounce = 10
    if music_idle_std > 0.010: guess_debounce = 12
    guess_n_hyst = 2.0 

    t_mot, t_rum, t_cre, t_mus = guess_motor, guess_rumble, guess_crest, guess_music
    h_mot, h_nee, t_ceil, d_chunk = guess_m_hyst, guess_n_hyst, guess_ceiling, guess_debounce
   
    success = False
    for hyst_loop in range(3):
        for attempt in range(50):
            result = simulate_state_machine(calibration_data, t_mot, t_rum, t_cre, t_mus, h_mot, h_nee, d_chunk, is_silent_hw, guess_motor_hfer, t_ceil)
            if result == "PASS":
                p_high = simulate_state_machine(calibration_data, t_mot*1.1, t_rum*1.1, t_cre*1.1, t_mus*1.1, h_mot*1.1, h_nee*1.1, int(d_chunk*1.2), is_silent_hw, guess_motor_hfer, t_ceil)
                p_low = simulate_state_machine(calibration_data, t_mot*0.9, t_rum*0.9, t_cre*0.9, t_mus*0.9, h_mot*0.9, h_nee*0.9, int(d_chunk*0.8), is_silent_hw, guess_motor_hfer, t_ceil)
                if p_high == "PASS" and p_low == "PASS":
                    success = True; break
                else:
                    if DEBUG: log(f"  [Attempt {attempt}] Perturbation failed. Nudging thresholds...")
                    if p_high != "PASS": t_mot *= 0.98; t_rum *= 0.98; t_mus *= 0.98
                    if p_low != "PASS": t_mot *= 1.02; t_rum *= 1.02; t_mus *= 1.02
                    continue
            if "Power expected False" in result or "Power flicker" in result: t_mot *= 1.10
            elif "Power expected True" in result or "transition during grace" in result: t_mot *= 0.90
            elif "Needle expected False" in result: t_rum *= 1.10; t_cre += 0.2
            elif "Needle expected True" in result: t_rum *= 0.90; t_cre = max(1.5, t_cre - 0.2)
            elif "Music expected but not reliably" in result: t_mus *= 0.90
            elif "Music falsely detected" in result: t_mus *= 1.15
        if success: break
       
        if DEBUG: log(f"⚠️ Expanding Hysteresis Time Buffers...")
        h_mot, h_nee = min(h_mot + 1.0, 5.0), min(h_nee + 0.5, 3.0)
        t_mot, t_rum, t_cre, t_mus = guess_motor, guess_rumble, guess_crest, guess_music

    if success:
        log("\n" + "="*50); log("✅ CALIBRATION SUCCESSFUL!"); log("="*50)
        log(f"The algorithm successfully mapped your hardware states.\n")
        auto_cal_data = {"mic_volume": current_vol, "music_threshold": float(t_mus), "rumble_threshold": float(t_rum), "motor_power_threshold": float(t_mot), "motor_power_ceiling": float(t_ceil), "runout_crest_threshold": float(t_cre), "motor_hysteresis_sec": float(h_mot), "needle_hysteresis_sec": float(h_nee), "music_debounce_chunks": int(d_chunk), "motor_hfer_threshold": float(guess_motor_hfer), "is_silent_hw": bool(is_silent_hw)}
        save_atomic_json(AUTO_CALIB_FILE, auto_cal_data)
        log("👉 Turn OFF 'calibration_mode' in the Add-on UI and Restart to begin using Vinyl Guardian!")
    else:
        log("\n" + "="*50); log("❌ CALIBRATION FAILED"); log("="*50)
        log(f"The ambient noise floor is too high to distinguish between states.")
        if DEBUG: log(f"Final error: {result}")
   
    log("\n💤 Sleeping to prevent auto-restart. You can restart the Add-on now.")
    while True: time.sleep(3600)