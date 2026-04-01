import sys
import os
import json
import time
import threading
import wave
import requests
import urllib.parse
import numpy as np
import alsaaudio
import paho.mqtt.client as mqtt
import asyncio
import subprocess
from shazamio import Shazam
import pylast
import signal

# --- LOAD CONFIGURATION ---
try:
    with open('/data/options.json') as f:
        config = json.load(f)
except Exception as e:
    print(f"🚨 Failed to load config: {e}")
    sys.exit(1)

# Path Setup
SHARE_DIR = "/share/vinyl_guardian"
os.makedirs(SHARE_DIR, exist_ok=True)
AUTO_CALIB_FILE = os.path.join(SHARE_DIR, "auto_calibration.json")

# System Modes
CALIBRATION_MODE = config.get("calibration_mode", False)

# --- HIERARCHICAL SETTINGS RESOLUTION ---
MUSIC_THRESHOLD = 0.005
MIC_VOLUME = 8

if os.path.exists(AUTO_CALIB_FILE):
    try:
        with open(AUTO_CALIB_FILE, 'r') as f:
            auto_cal = json.load(f)
        MUSIC_THRESHOLD = auto_cal.get("music_threshold", MUSIC_THRESHOLD)
        MIC_VOLUME = auto_cal.get("mic_volume", MIC_VOLUME)
    except Exception as e:
        pass

UI_MIC = config.get("mic_volume")
if UI_MIC is not None and UI_MIC > 0:
    MIC_VOLUME = UI_MIC

UI_MUSIC = config.get("music_threshold")
if UI_MUSIC is not None and UI_MUSIC > 0:
    MUSIC_THRESHOLD = UI_MUSIC

# Audio Settings
CHANNELS = config.get("channels", 2)
RATE = 44100
FORMAT = alsaaudio.PCM_FORMAT_S16_LE
CHUNK = 2048

def log(message):
    print(f"[Vinyl Guardian] {message}", flush=True)

def signal_handler(sig, frame):
    log("🛑 Shutting down gracefully...")
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

def calculate_audio_levels(data):
    try:
        audio_data = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        if len(audio_data) <= 1: return 0.0, 0.0
        raw_rms = float(np.sqrt(np.mean(np.square(audio_data)))) / 32768.0
        filtered_data = audio_data[1:] - 0.95 * audio_data[:-1]
        music_rms = float(np.sqrt(np.mean(np.square(filtered_data)))) / 32768.0
        return raw_rms, music_rms
    except Exception as e: 
        return 0.0, 0.0

# --- ADVANCED DIAGNOSTIC ENGINE ---
def run_diagnostics():
    log("=========================================")
    log("🔬 ADVANCED GROOVE METRICS TEST 🔬")
    log("=========================================")
    
    try:
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NORMAL, device='default', channels=CHANNELS, rate=RATE, format=FORMAT, periodsize=CHUNK)
    except Exception as e: 
        log(f"🚨 ALSA Error: {e}"); sys.exit(1)

    def record_stats(duration_secs):
        target_chunks = int(RATE / CHUNK * duration_secs)
        
        history_rms = []
        history_hiss = []
        history_zcr = []
        history_crest = []
        history_hf_ratio = []
        history_stereo_corr = []
        total_pops = 0
        
        chunks = 0
        while chunks < target_chunks:
            length, data = inp.read()
            if length > 0:
                audio_data = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                if len(audio_data) > 1:
                    # 1. Volume RMS
                    rms = float(np.sqrt(np.mean(np.square(audio_data)))) / 32768.0
                    history_rms.append(rms)
                    
                    # 2. Hiss RMS (Differentiated)
                    hiss_data = audio_data[1:] - audio_data[:-1]
                    hiss_rms = float(np.sqrt(np.mean(np.square(hiss_data)))) / 32768.0
                    history_hiss.append(hiss_rms)

                    # 3. Zero-Crossing Rate
                    zcr = np.sum(np.diff(np.sign(audio_data)) != 0) / len(audio_data)
                    history_zcr.append(zcr)
                    
                    # 4. Crest Factor (Spikiness per chunk)
                    peak = np.max(np.abs(audio_data))
                    rms_val = np.sqrt(np.mean(np.square(audio_data)))
                    crest = peak / rms_val if rms_val > 0 else 1.0
                    history_crest.append(crest)
                    
                    # 5. Pop Detection (Count extreme transients)
                    # Anything 6x louder than the local RMS is considered a dust pop
                    if rms_val > 0:
                        pops_in_chunk = np.sum(np.abs(audio_data) > (6.0 * rms_val))
                        total_pops += pops_in_chunk
                        
                    # 6. Stereo Correlation (Mono vs Stereo width)
                    if CHANNELS == 2 and len(audio_data) % 2 == 0:
                        stereo = audio_data.reshape(-1, 2)
                        L = stereo[:, 0]
                        R = stereo[:, 1]
                        if np.std(L) > 0 and np.std(R) > 0:
                            corr = np.corrcoef(L, R)[0, 1]
                        else:
                            corr = 1.0
                        history_stereo_corr.append(abs(corr))
                    
                    # 7. High-Frequency FFT (>10kHz Energy)
                    fft_out = np.abs(np.fft.rfft(audio_data))
                    freqs = np.fft.rfftfreq(len(audio_data), 1.0/RATE)
                    hf_energy = np.sum(fft_out[freqs > 10000])
                    total_energy = np.sum(fft_out)
                    hf_ratio = hf_energy / total_energy if total_energy > 0 else 0
                    history_hf_ratio.append(hf_ratio)

                chunks += 1
                if chunks % max(1, int(target_chunks / 10)) == 0:
                    print("█", end="", flush=True)
        print("")
        
        return {
            "rms": float(np.median(history_rms)) if history_rms else 0,
            "hiss": float(np.median(history_hiss)) if history_hiss else 0,
            "zcr": float(np.median(history_zcr)) if history_zcr else 0,
            "crest_max": float(np.max(history_crest)) if history_crest else 0,
            "pops": total_pops,
            "stereo_corr": float(np.median(history_stereo_corr)) if history_stereo_corr else 1.0,
            "hf_ratio": float(np.median(history_hf_ratio)) if history_hf_ratio else 0
        }

    def capture_dual_volume(prompt, duration_secs, wait_for_runout=False):
        log(f"\n👉 {prompt}")
        
        try:
            subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{MIC_VOLUME}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError: pass

        if wait_for_runout:
            log("Waiting for you to drop the needle. Listening for music...")
            while True:
                length, data = inp.read()
                if length > 0:
                    _, music_rms = calculate_audio_levels(data)
                    if music_rms > MUSIC_THRESHOLD:
                        log("🎵 Music detected! Now waiting for the track to finish...")
                        break
            
            silence_chunks = 0
            target_silence = int(RATE / CHUNK * 3) 
            while True:
                length, data = inp.read()
                if length > 0:
                    _, music_rms = calculate_audio_levels(data)
                    if music_rms < MUSIC_THRESHOLD:
                        silence_chunks += 1
                        if silence_chunks >= target_silence:
                            log("🔇 Silence detected.")
                            break
                    else:
                        silence_chunks = 0
                        
            # --- THE 10 SECOND PHYSICAL BUFFER ---
            log("⏳ Waiting 10 seconds for the needle to physically enter the Runout Groove...")
            start_wait = time.time()
            while time.time() - start_wait < 10.0:
                inp.read() # Actively drain buffer so we don't crash ALSA
        else:
            log("Waiting 10 seconds for you to prepare...")
            start_wait = time.time()
            while time.time() - start_wait < 10.0:
                inp.read()

        # 1. Capture Normal Volume
        log(f"🔴 Capturing {duration_secs}s at NORMAL volume ({MIC_VOLUME}%)...")
        for _ in range(5): inp.read()
        norm_stats = record_stats(duration_secs)

        # 2. Capture Max Volume
        log(f"🔴 Capturing {duration_secs}s at MAX volume (100%)...")
        try:
            subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", "100%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError: pass
        
        for _ in range(5): inp.read()
        max_stats = record_stats(duration_secs)

        # 3. Restore Volume
        try:
            subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{MIC_VOLUME}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError: pass
        
        return norm_stats, max_stats

    up_norm, up_max = capture_dual_volume("STAGE 1: Turn Turntable ON, leave Needle UP.", 15, wait_for_runout=False)
    down_norm, down_max = capture_dual_volume("STAGE 2: Drop the Needle near the END of a track.", 15, wait_for_runout=True)
    inp.close()

    def calc_diff(up, down):
        if up == 0: return 0
        return down / up

    def print_table(title, up_stats, down_stats):
        log(f"\n============================================================")
        log(f"📊 {title} 📊")
        log(f"============================================================")
        log(f"Metric               | Needle UP    | Runout Groove | Multiplier")
        log(f"---------------------|--------------|---------------|-----------")
        log(f"Raw Volume RMS       | {up_stats['rms']:12.6f} | {down_stats['rms']:13.6f} | {calc_diff(up_stats['rms'], down_stats['rms']):.2f}x")
        log(f"Hiss Volume RMS      | {up_stats['hiss']:12.6f} | {down_stats['hiss']:13.6f} | {calc_diff(up_stats['hiss'], down_stats['hiss']):.2f}x")
        log(f"Zero Crossing Rate   | {up_stats['zcr']:12.6f} | {down_stats['zcr']:13.6f} | {calc_diff(up_stats['zcr'], down_stats['zcr']):.2f}x")
        log(f"Max Crest (Spikes)   | {up_stats['crest_max']:12.6f} | {down_stats['crest_max']:13.6f} | {calc_diff(up_stats['crest_max'], down_stats['crest_max']):.2f}x")
        log(f"Total Pop Count      | {up_stats['pops']:12} | {down_stats['pops']:13} | {'N/A' if up_stats['pops']==0 else f'{down_stats['pops']/up_stats['pops']:.2f}x'}")
        log(f"Stereo Correlation   | {up_stats['stereo_corr']:12.6f} | {down_stats['stereo_corr']:13.6f} | {calc_diff(up_stats['stereo_corr'], down_stats['stereo_corr']):.2f}x")
        log(f"High-Freq FFT Ratio  | {up_stats['hf_ratio']:12.6f} | {down_stats['hf_ratio']:13.6f} | {calc_diff(up_stats['hf_ratio'], down_stats['hf_ratio']):.2f}x")

    print_table("[TEST 1] NORMAL VOLUME RESULTS", up_norm, down_norm)
    print_table("[TEST 2] 100% MAX VOLUME RESULTS", up_max, down_max)
    
    log("\n============================================================")
    log("Copy these results and share them!")
    log("Sleeping to prevent auto-restart...")
    while True:
        time.sleep(3600)

if __name__ == "__main__":
    print("\033[2J\033[H", end="", flush=True)
    print("========================================================")
    log(f"🚀 BOOTING VINYL GUARDIAN (ADVANCED METRICS BUILD)...")
    print("========================================================")
    
    if CALIBRATION_MODE:
        run_diagnostics()
    else:
        log("Calibration Mode is OFF. This build only runs diagnostics.")
        log("Please enable Calibration Mode in the UI and restart.")
        while True:
            time.sleep(3600)