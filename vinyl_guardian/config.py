import sys
import os
import json
import time
import tempfile

# --- Path Setup ---
SHARE_DIR = "/share/vinyl_guardian"
os.makedirs(SHARE_DIR, exist_ok=True)
AUTO_CALIB_FILE = os.path.join(SHARE_DIR, "auto_calibration.json")

# --- Load Configuration ---
try:
    with open('/data/options.json') as f:
        config = json.load(f)
except Exception as e:
    print(f"🚨 Failed to load config: {e}")
    sys.exit(1)

# --- System Modes ---
CALIBRATION_MODE = config.get("calibration_mode", False)
TEST_CAPTURE_MODE = config.get("test_capture_mode", False)
DEBUG = config.get("debug_logging", False)

# 👻 TEMPORARY DEBUG TOGGLE: Capture False Positives
DEBUG_GHOST_CATCHER = True

# --- MQTT & API Keys ---
MQTT_BROKER = config.get("mqtt_broker", "core-mosquitto")
MQTT_PORT = config.get("mqtt_port", 1883)
MQTT_USER = config.get("mqtt_user", "")
MQTT_PASS = config.get("mqtt_password", "")

LFM_USER = config.get("lastfm_username", "")
LFM_PASS = config.get("lastfm_password", "")
LFM_KEY = config.get("lastfm_api_key", "")
LFM_SECRET = config.get("lastfm_api_secret", "")

adv = config.get("advanced", {})

# --- Default Fallback Thresholds ---
MUSIC_THRESHOLD = 0.005
RUMBLE_THRESHOLD = 0.015
MOTOR_POWER_THRESHOLD = 0.0045
MOTOR_POWER_CEILING = 0.0150
MOTOR_HFER_THRESHOLD = 0.0
MIC_VOLUME = 8
RECORD_SECONDS = config.get("recording_seconds", 10)

# --- Dynamic Calibration State Variables ---
RUNOUT_CREST_THRESHOLD = 4.5
MOTOR_HYSTERESIS_SEC = 1.0 
NEEDLE_HYSTERESIS_SEC = 2.0 
DYNAMIC_DEBOUNCE_CHUNKS = adv.get("trigger_debounce_chunks", 3)
IS_SILENT_HW = False

def log(message):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [Vinyl Guardian] {message}", flush=True)

def save_atomic_json(filepath, data):
    temp_fd, temp_path = tempfile.mkstemp(dir=SHARE_DIR)
    try:
        with os.fdopen(temp_fd, 'w') as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, filepath)
    except Exception as e:
        log(f"⚠️ Failed to save atomic JSON: {e}")
        try:
            os.unlink(temp_path)
        except:
            pass

# --- STRICT CALIBRATION ENFORCEMENT ---
if os.path.exists(AUTO_CALIB_FILE):
    try:
        with open(AUTO_CALIB_FILE, 'r') as f:
            auto_cal = json.load(f)
        MUSIC_THRESHOLD = auto_cal.get("music_threshold", MUSIC_THRESHOLD)
        RUMBLE_THRESHOLD = auto_cal.get("rumble_threshold", RUMBLE_THRESHOLD)
        MOTOR_POWER_THRESHOLD = auto_cal.get("motor_power_threshold", MOTOR_POWER_THRESHOLD)
        MOTOR_POWER_CEILING = auto_cal.get("motor_power_ceiling", MOTOR_POWER_CEILING)
        MIC_VOLUME = auto_cal.get("mic_volume", MIC_VOLUME)
        RUNOUT_CREST_THRESHOLD = auto_cal.get("runout_crest_threshold", RUNOUT_CREST_THRESHOLD)
        MOTOR_HYSTERESIS_SEC = auto_cal.get("motor_hysteresis_sec", MOTOR_HYSTERESIS_SEC)
        NEEDLE_HYSTERESIS_SEC = auto_cal.get("needle_hysteresis_sec", NEEDLE_HYSTERESIS_SEC)
        DYNAMIC_DEBOUNCE_CHUNKS = auto_cal.get("music_debounce_chunks", DYNAMIC_DEBOUNCE_CHUNKS)
        MOTOR_HFER_THRESHOLD = auto_cal.get("motor_hfer_threshold", MOTOR_HFER_THRESHOLD)
        IS_SILENT_HW = auto_cal.get("is_silent_hw", False)
       
        if not CALIBRATION_MODE:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Vinyl Guardian] 💡 Successfully loaded hardware calibration profile.")
    except Exception as e:
        if not CALIBRATION_MODE:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Vinyl Guardian] 🚨 FATAL ERROR: Calibration file is corrupted or unreadable: {e}")
            sys.exit(1)
else:
    if not CALIBRATION_MODE:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Vinyl Guardian] 🚨 FATAL ERROR: No calibration data found!")
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Vinyl Guardian] 👉 Please enable 'calibration_mode' in the Add-on configuration, start the Add-on to run the wizard, and then turn it off.")
        sys.exit(1)

# Manual UI Overrides
UI_MUSIC = adv.get("manual_override_music_threshold")
if UI_MUSIC is not None and UI_MUSIC > 0: MUSIC_THRESHOLD = UI_MUSIC

UI_RUMBLE = adv.get("manual_override_rumble_threshold")
if UI_RUMBLE is not None and UI_RUMBLE > 0: RUMBLE_THRESHOLD = UI_RUMBLE

UI_MOTOR = adv.get("manual_override_motor_threshold")
if UI_MOTOR is not None and UI_MOTOR > 0: MOTOR_POWER_THRESHOLD = UI_MOTOR

UI_MIC = adv.get("manual_override_mic_volume")
if UI_MIC is not None and UI_MIC > 0: MIC_VOLUME = UI_MIC

# --- ENGINE TUNING PARAMETERS ---
MAX_ATTEMPTS = adv.get("max_attempts", 3)
MIN_AUDIO_SECONDS = adv.get("min_audio_seconds", 5)
AUDIO_ONSET_THRESHOLD = adv.get("audio_onset_threshold", 1000)      
NEEDLE_LIFT_SECONDS = adv.get("needle_lift_seconds", 15)
CONSECUTIVE_FAILURE_TIMEOUT = adv.get("consecutive_failure_timeout", 1800)
FALLBACK_SLEEP_SECS = adv.get("fallback_sleep_secs", 60)          

# --- Audio Settings ---
CHANNELS = config.get("channels", 2)
RATE = 44100
CHUNK = 2048
MAX_BUFFER_SIZE = RATE * CHANNELS * 2 * 60