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
TEST_CAPTURE_MODE = config.get("test_capture_mode", False)
DEBUG = config.get("debug_logging", False)

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

# --- MAX GAIN DIAGNOSTIC ENGINE ---
def run_diagnostics():
    log("=========================================")
    log("🔬 MAX GAIN HYPOTHESIS TEST 🔬")
    log("=========================================")
    
    try:
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NORMAL, device='default', channels=CHANNELS, rate=RATE, format=FORMAT, periodsize=CHUNK)
    except Exception as e: 
        log(f"🚨 ALSA Error: {e}"); sys.exit(1)

    def record_stats(duration_secs):
        target_chunks = int(RATE / CHUNK * duration_secs)
        history_rms = []
        history_zcr = []
        history_crest = []
        history_hiss = []
        chunks = 0
        while chunks < target_chunks:
            length, data = inp.read()
            if length > 0:
                audio_data = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                if len(audio_data) > 1:
                    rms = float(np.sqrt(np.mean(np.square(audio_data)))) / 32768.0
                    zero_crossings = np.sum(np.diff(np.sign(audio_data)) != 0)
                    zcr = zero_crossings / len(audio_data)
                    peak = np.max(np.abs(audio_data))
                    rms_val = np.sqrt(np.mean(np.square(audio_data)))
                    crest = peak / rms_val if rms_val > 0 else 1.0
                    hiss_data = audio_data[1:] - audio_data[:-1]
                    hiss_rms = float(np.sqrt(np.mean(np.square(hiss_data)))) / 32768.0

                    history_rms.append(rms)
                    history_zcr.append(zcr)
                    history_crest.append(crest)
                    history_hiss.append(hiss_rms)
                chunks += 1
                if chunks % max(1, int(target_chunks / 10)) == 0:
                    print("█", end="", flush=True)
        print("")
        return {
            "rms": float(np.median(history_rms)) if history_rms else 0,
            "zcr": float(np.median(history_zcr)) if history_zcr else 0,
            "crest": float(np.median(history_crest)) if history_crest else 0,
            "hiss": float(np.median(history_hiss)) if history_hiss else 0
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
                    raw_rms, music_rms = calculate_audio_levels(data)
                    if music_rms > MUSIC_THRESHOLD:
                        log("🎵 Music detected! Now waiting for the track to finish...")
                        break
            
            silence_chunks = 0
            target_silence = int(RATE / CHUNK * 3) 
            while True:
                length, data = inp.read()
                if length > 0:
                    raw_rms, music_rms = calculate_audio_levels(data)
                    if music_rms < MUSIC_THRESHOLD:
                        silence_chunks += 1
                        if silence_chunks >= target_silence:
                            log("🔇 Silence detected. Assuming Runout Groove!")
                            break
                    else:
                        silence_chunks = 0
        else:
            log("Waiting 10 seconds for you to prepare...")
            for _ in range(10):
                log("...")
                inp.read()
                time.sleep(1)

        # 1. Capture Normal Volume
        log(f"🔴 Capturing {duration_secs}s at NORMAL volume ({MIC_VOLUME}%)...")
        for _ in range(5): inp.read() # Drain buffer
        norm_stats = record_stats(duration_secs)

        # 2. Capture Max Volume
        log(f"🔴 Capturing {duration_secs}s at MAX volume (100%)...")
        try:
            subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", "100%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError: pass
        
        for _ in range(5): inp.read() # Drain buffer to catch pactl pop
        max_stats = record_stats(duration_secs)

        # 3. Restore Volume
        try:
            subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{MIC_VOLUME}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError: pass
        
        return norm_stats, max_stats

    up_norm, up_max = capture_dual_volume("STAGE 1: Turn Turntable ON, leave Needle UP.", 10, wait_for_runout=False)
    down_norm, down_max = capture_dual_volume("STAGE 2: Drop the Needle near the END of a track.", 10, wait_for_runout=True)
    inp.close()

    def calc_diff(up, down):
        if up == 0: return 0
        return down / up

    log("\n=========================================")
    log("📊 [TEST 1] NORMAL VOLUME RESULTS 📊")
    log("=========================================")
    log(f"Metric         | Needle UP  | Runout Groove | Multiplier")
    log(f"---------------|------------|---------------|-----------")
    log(f"Raw Volume RMS | {up_norm['rms']:10.6f} | {down_norm['rms']:13.6f} | {calc_diff(up_norm['rms'], down_norm['rms']):.2f}x")
    log(f"Hiss Volume    | {up_norm['hiss']:10.6f} | {down_norm['hiss']:13.6f} | {calc_diff(up_norm['hiss'], down_norm['hiss']):.2f}x")
    log(f"Zero Crossings | {up_norm['zcr']:10.6f} | {down_norm['zcr']:13.6f} | {calc_diff(up_norm['zcr'], down_norm['zcr']):.2f}x")
    log(f"Crest Factor   | {up_norm['crest']:10.6f} | {down_norm['crest']:13.6f} | {calc_diff(up_norm['crest'], down_norm['crest']):.2f}x")

    log("\n=========================================")
    log("📊 [TEST 2] 100% MAX VOLUME RESULTS 📊")
    log("=========================================")
    log(f"Metric         | Needle UP  | Runout Groove | Multiplier")
    log(f"---------------|------------|---------------|-----------")
    log(f"Raw Volume RMS | {up_max['rms']:10.6f} | {down_max['rms']:13.6f} | {calc_diff(up_max['rms'], down_max['rms']):.2f}x")
    log(f"Hiss Volume    | {up_max['hiss']:10.6f} | {down_max['hiss']:13.6f} | {calc_diff(up_max['hiss'], down_max['hiss']):.2f}x")
    log(f"Zero Crossings | {up_max['zcr']:10.6f} | {down_max['zcr']:13.6f} | {calc_diff(up_max['zcr'], down_max['zcr']):.2f}x")
    log(f"Crest Factor   | {up_max['crest']:10.6f} | {down_max['crest']:13.6f} | {calc_diff(up_max['crest'], down_max['crest']):.2f}x")
    
    log("\n=========================================")
    log("Copy these results and share them!")
    log("Sleeping to prevent auto-restart...")
    while True:
        time.sleep(3600)

if __name__ == "__main__":
    print("\033[2J\033[H", end="", flush=True)
    print("========================================================")
    log(f"🚀 BOOTING VINYL GUARDIAN (MAX GAIN TEST BUILD)...")
    print("========================================================")
    
    if CALIBRATION_MODE:
        run_diagnostics()
    else:
        log("Calibration Mode is OFF. This build only runs diagnostics.")
        log("Please enable Calibration Mode in the UI and restart.")
        while True:
            time.sleep(3600)