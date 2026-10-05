import sys
import os
import json
import time
import tempfile

# --- Path Setup ---
SHARE_DIR = "/share/vinyl_guardian"
AUTO_CALIB_FILE = os.path.join(SHARE_DIR, "auto_calibration.json")

# --- Load Configuration ---
try:
    with open('/data/options.json') as f:
        config = json.load(f)
except Exception as e:
    print(f"🚨 Failed to load config: {e}")
    sys.exit(1)

# --- Recording Storage ---
from storage import DEFAULT_RECORDING_DIRECTORY, prepare_recording_directory, storage_diagnostics
recording_path = config.get("recording_directory", DEFAULT_RECORDING_DIRECTORY)
for diagnostic in storage_diagnostics(recording_path):
    print(f"📁 {diagnostic}", flush=True)
try:
    RECORDING_DIR = prepare_recording_directory(recording_path)
    os.makedirs(SHARE_DIR, exist_ok=True)
except (ValueError, OSError) as exc:
    print(f"🚨 Recording storage unavailable: {exc}", flush=True)
    sys.exit(1)
print(f"✅ Recording storage check passed: WAV write, read, rename and deletion in {RECORDING_DIR} and its calibration_data folder.", flush=True)

# --- System Modes ---
CALIBRATION_MODE = config.get("calibration_mode", False)
TEST_CAPTURE_MODE = config.get("test_capture_mode", False)
DEBUG = config.get("debug_logging", False)

# --- Dataset / Feature Collection ---
DATA_COLLECTION_ENABLED = config.get("data_collection_enabled", False)
DATA_COLLECTION_LABEL = config.get("data_collection_label", "unlabelled")
DATA_COLLECTION_RAW_AUDIO = config.get("data_collection_raw_audio", False)
DATA_COLLECTION_FEATURE_INTERVAL = config.get("data_collection_feature_interval", 0.25)
DATA_COLLECTION_RAW_SEGMENT_MINUTES = config.get("data_collection_raw_segment_minutes", 30)

# --- Audio Input Selection ---
# "auto" reuses Guardian's remembered source, "system_default" follows Home
# Assistant's PulseAudio default, or an exact Pulse source name can be used.
AUDIO_SOURCE = config.get("audio_source", "auto")
AUDIO_SCAN_ON_START = config.get("audio_scan_on_start", False)
AUDIO_SCAN_SECONDS = config.get("audio_scan_seconds", 2.5)

# --- Experimental Harness ---
DIAGNOSTIC_CAPTURE_MODE = config.get("diagnostic_capture_mode", "normal")
EXPERIMENT_HARNESS_ENABLED = config.get("experiment_harness_enabled", True)
AUTO_CAPTURE_INTERESTING_EVENTS = config.get("auto_capture_interesting_events", True)

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
MUSIC_HOLD_THRESHOLD = 0.003
MOTOR_POWER_THRESHOLD = 0.0045
MOTOR_POWER_CEILING = 0.0150
MOTOR_HFER_THRESHOLD = 0.0
SILENCE_GATE_RMS = 0.003
POP_AMPLITUDE_THRESHOLD = 0.0
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
        MUSIC_HOLD_THRESHOLD = auto_cal.get("music_hold_threshold", MUSIC_HOLD_THRESHOLD)
        MOTOR_POWER_THRESHOLD = auto_cal.get("motor_power_threshold", MOTOR_POWER_THRESHOLD)
        MOTOR_POWER_CEILING = auto_cal.get("motor_power_ceiling", MOTOR_POWER_CEILING)
        MIC_VOLUME = auto_cal.get("mic_volume", MIC_VOLUME)
        RUNOUT_CREST_THRESHOLD = auto_cal.get("runout_crest_threshold", RUNOUT_CREST_THRESHOLD)
        MOTOR_HYSTERESIS_SEC = auto_cal.get("motor_hysteresis_sec", MOTOR_HYSTERESIS_SEC)
        NEEDLE_HYSTERESIS_SEC = auto_cal.get("needle_hysteresis_sec", NEEDLE_HYSTERESIS_SEC)
        DYNAMIC_DEBOUNCE_CHUNKS = auto_cal.get("music_debounce_chunks", DYNAMIC_DEBOUNCE_CHUNKS)
        MOTOR_HFER_THRESHOLD = auto_cal.get("motor_hfer_threshold", MOTOR_HFER_THRESHOLD)
        IS_SILENT_HW = auto_cal.get("is_silent_hw", False)
        SILENCE_GATE_RMS = auto_cal.get("SILENCE_GATE_RMS", SILENCE_GATE_RMS)
        POP_AMPLITUDE_THRESHOLD = auto_cal.get("pop_amplitude_threshold", POP_AMPLITUDE_THRESHOLD)
       
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