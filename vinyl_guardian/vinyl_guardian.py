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

# MQTT & API Keys
MQTT_BROKER = config.get("mqtt_broker", "core-mosquitto")
MQTT_PORT = config.get("mqtt_port", 1883)
MQTT_USER = config.get("mqtt_user", "")
MQTT_PASS = config.get("mqtt_password", "")

LFM_USER = config.get("lastfm_username", "")
LFM_PASS = config.get("lastfm_password", "")
LFM_KEY = config.get("lastfm_api_key", "")
LFM_SECRET = config.get("lastfm_api_secret", "")

# Load advanced dictionary
adv = config.get("advanced", {})

# --- HIERARCHICAL SETTINGS RESOLUTION ---
MUSIC_THRESHOLD = 0.005
RUMBLE_THRESHOLD = 0.015
MOTOR_POWER_THRESHOLD = 0.0045
MIC_VOLUME = 8
RECORD_SECONDS = config.get("recording_seconds", 10)

if os.path.exists(AUTO_CALIB_FILE):
    try:
        with open(AUTO_CALIB_FILE, 'r') as f:
            auto_cal = json.load(f)
        MUSIC_THRESHOLD = auto_cal.get("music_threshold", MUSIC_THRESHOLD)
        RUMBLE_THRESHOLD = auto_cal.get("rumble_threshold", RUMBLE_THRESHOLD)
        MOTOR_POWER_THRESHOLD = auto_cal.get("motor_power_threshold", MOTOR_POWER_THRESHOLD)
        MIC_VOLUME = auto_cal.get("mic_volume", MIC_VOLUME)
    except Exception as e:
        pass

UI_MIC = config.get("mic_volume")
if UI_MIC is not None and UI_MIC > 0:
    MIC_VOLUME = UI_MIC

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


# --- DIAGNOSTIC ENGINE ---
def run_diagnostics():
    """Captures advanced acoustic metrics to differentiate between Air and PVC."""
    log("=========================================")
    log("🔬 GROOVE vs AIR DIAGNOSTIC TEST 🔬")
    log("=========================================")
    
    try:
        inp = alsaaudio.PCM(type=alsaaudio.PCM_CAPTURE, mode=alsaaudio.PCM_NORMAL, device='default', channels=CHANNELS, rate=RATE, format=FORMAT, periodsize=CHUNK)
    except Exception as e: 
        log(f"🚨 ALSA Error: {e}"); sys.exit(1)

    log(f"🔊 Applying current mic volume: {MIC_VOLUME}%")
    try:
        subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{MIC_VOLUME}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass 

    def capture_stage(prompt, duration_secs):
        log(f"\n👉 {prompt}")
        log("Waiting 10 seconds for you to prepare...")
        for i in range(10, 0, -1):
            log(f"... {i} ...")
            inp.read()
            time.sleep(1)

        log(f"🔴 Capturing {duration_secs} seconds of data...")
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
                    # 1. Raw RMS (Volume)
                    rms = float(np.sqrt(np.mean(np.square(audio_data)))) / 32768.0
                    
                    # 2. Zero-Crossing Rate (Hiss/Frequency density)
                    zero_crossings = np.sum(np.diff(np.sign(audio_data)) != 0)
                    zcr = zero_crossings / len(audio_data)
                    
                    # 3. Crest Factor (Spikiness/Crackle)
                    peak = np.max(np.abs(audio_data))
                    rms_val = np.sqrt(np.mean(np.square(audio_data)))
                    crest = peak / rms_val if rms_val > 0 else 1.0
                    
                    # 4. Extreme Hiss RMS (Harsh high-pass filter)
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
            "rms": float(np.median(history_rms)),
            "zcr": float(np.median(history_zcr)),
            "crest": float(np.median(history_crest)),
            "hiss": float(np.median(history_hiss))
        }

    up_stats = capture_stage("STAGE 1: Turn Turntable ON, but leave the Needle UP (in the air).", 15)
    down_stats = capture_stage("STAGE 2: Drop the Needle into the SILENT RUNOUT GROOVE.", 15)
    inp.close()

    log("\n=========================================")
    log("📊 DIAGNOSTIC RESULTS 📊")
    log("=========================================")
    log(f"Metric         | Needle UP  | Runout Groove | Multiplier")
    log(f"---------------|------------|---------------|-----------")
    
    def calc_diff(up, down):
        if up == 0: return 0
        return down / up

    log(f"Raw Volume RMS | {up_stats['rms']:10.6f} | {down_stats['rms']:13.6f} | {calc_diff(up_stats['rms'], down_stats['rms']):.2f}x")
    log(f"Hiss Volume    | {up_stats['hiss']:10.6f} | {down_stats['hiss']:13.6f} | {calc_diff(up_stats['hiss'], down_stats['hiss']):.2f}x")
    log(f"Zero Crossings | {up_stats['zcr']:10.6f} | {down_stats['zcr']:13.6f} | {calc_diff(up_stats['zcr'], down_stats['zcr']):.2f}x")
    log(f"Crest Factor   | {up_stats['crest']:10.6f} | {down_stats['crest']:13.6f} | {calc_diff(up_stats['crest'], down_stats['crest']):.2f}x")
    log("=========================================")
    log("Copy these results and share them!")
    log("Sleeping to prevent auto-restart...")
    while True:
        time.sleep(3600)

if __name__ == "__main__":
    print("\033[2J\033[H", end="", flush=True)
    print("========================================================")
    log(f"🚀 BOOTING VINYL GUARDIAN (DIAGNOSTIC BUILD)...")
    print("========================================================")
    
    if CALIBRATION_MODE:
        run_diagnostics()
    else:
        log("Calibration Mode is OFF. This build only runs diagnostics.")
        log("Please enable Calibration Mode in the UI and restart.")
        while True:
            time.sleep(3600)