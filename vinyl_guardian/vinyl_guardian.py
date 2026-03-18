import sys
import subprocess
import numpy as np
import paho.mqtt.client as mqtt
import json
import os
import collections
import time
import acoustid

# --- LOAD SECRETS FROM HOME ASSISTANT UI ---
OPTIONS_FILE = "/data/options.json"
try:
    with open(OPTIONS_FILE, "r") as f:
        options = json.load(f)
except Exception as e:
    print(f"Error reading options.json: {e}")
    options = {}

ACOUSTID_API_KEY = options.get("acoustid_key", "")
MQTT_BROKER = options.get("mqtt_broker", "")
MQTT_PORT = options.get("mqtt_port", 1883)
MQTT_USER = options.get("mqtt_user", "")
MQTT_PASSWORD = options.get("mqtt_password", "")
THRESHOLD = options.get("audio_threshold", 0.015)
DEBUG_LOGGING = options.get("debug_logging", True)

def debug_log(message):
    if DEBUG_LOGGING:
        print(f"[debug] {message}")

def dump_runtime_debug_info():
    debug_log(f"Options file present: {os.path.exists(OPTIONS_FILE)}")
    debug_log(
        "Startup options summary: "
        f"mqtt_broker={'set' if MQTT_BROKER else 'missing'}, "
        f"mqtt_port={MQTT_PORT}, "
        f"mqtt_user={'set' if MQTT_USER else 'missing'}, "
        f"acoustid_key={'set' if ACOUSTID_API_KEY else 'missing'}, "
        f"audio_threshold={THRESHOLD}"
    )

# Ensure essential options exist before starting
if not ACOUSTID_API_KEY or not MQTT_BROKER:
    print("\n" + "="*60)
    print("🚨  ACTION REQUIRED: CONFIGURATION MISSING  🚨")
    print("="*60)
    print("The Add-on cannot start because it is missing credentials.")
    print("Please go to the 'Configuration' tab of this Add-on and fill in:")
    print("  1. Your AcoustID API Key")
    print("  2. Your MQTT Broker IP Address")
    print("Once filled out, click 'Save' and restart the Add-on.")
    print("="*60 + "\n")
    sys.exit(1)

# --- CONFIGURATION ---
SAMPLE_RATE = 44100
CHANNELS = 1
QUEUE_FILE = "offline_queue.json"

# --- STATE VARIABLES ---
LAST_TRACK = ""
STRIKEOUTS = 0

# --- MQTT SETUP & AUTO-DISCOVERY ---
def on_connect(client, userdata, flags, reason_code, properties=None):
    print("Connected to MQTT Broker. Publishing Auto-Discovery configs...")

    device_config = {
        "identifiers": ["vinyl_guardian_01"],
        "name": "Vinyl Guardian",
        "model": "Audio Fingerprinter",
        "manufacturer": "Custom Python Script"
    }

    binary_config = {
        "name": "Vinyl Playing",
        "object_id": "vinyl_playing",
        "unique_id": "vg_binary_playing",
        "state_topic": "vinyl_guardian/playing/state",
        "device_class": "sound",
        "device": device_config
    }

    track_config = {
        "name": "Vinyl Current Track",
        "object_id": "vinyl_current_track",
        "unique_id": "vg_sensor_track",
        "state_topic": "vinyl_guardian/track/state",
        "json_attributes_topic": "vinyl_guardian/track/attributes",
        "icon": "mdi:record-player",
        "device": device_config
    }

    client.publish("homeassistant/binary_sensor/vinyl_guardian/playing/config", json.dumps(binary_config), retain=True)
    client.publish("homeassistant/sensor/vinyl_guardian/track/config", json.dumps(track_config), retain=True)

# Using VERSION2 for future-proofing
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "VinylGuardian")
if MQTT_USER and MQTT_PASSWORD:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
mqtt_client.on_connect = on_connect

dump_runtime_debug_info()

print("Connecting to MQTT...")
try:
    mqtt_client.connect(MQTT_BROKER, int(MQTT_PORT), 60)
    mqtt_client.loop_start() 
except Exception as e:
    print(f"Failed to connect to MQTT: {e}")

def publish_mqtt(sensor_type, state, attributes=None):
    try:
        if sensor_type == "binary_sensor":
            mqtt_client.publish("vinyl_guardian/playing/state", state, retain=True)
        elif sensor_type == "sensor":
            mqtt_client.publish("vinyl_guardian/track/state", state, retain=True)
            if attributes:
                mqtt_client.publish("vinyl_guardian/track/attributes", json.dumps(attributes), retain=True)
        return True
    except Exception as e:
        print(f"MQTT Publish Error: {e}")
        return False

# --- OFFLINE QUEUE LOGIC ---
def load_queue():
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_queue(queue_list):
    try:
        with open(QUEUE_FILE, "w") as f:
            json.dump(queue_list, f)
    except Exception as e:
        print(f"Error saving queue: {e}")

OFFLINE_QUEUE = load_queue()

def process_queue():
    global OFFLINE_QUEUE
    if not OFFLINE_QUEUE:
        return

    print(f"Attempting to process {len(OFFLINE_QUEUE)} queued tracks...")
    successful_items = []

    for item in OFFLINE_QUEUE:
        print(f"Pushing queued track to MQTT: {item['track']}")
        success = publish_mqtt("sensor", item['track'], {"duration": item['duration']})

        if success:
            successful_items.append(item)
            time.sleep(3) 
        else:
            break

    for item in successful_items:
        OFFLINE_QUEUE.remove(item)
    save_queue(OFFLINE_QUEUE)

# --- DIRECT ALSA AUDIO CAPTURE ---
def record_audio(duration_sec):
    # Bypass PortAudio entirely and talk directly to Card 1
    cmd = [
        "arecord",
        "-D", "pulse",
        "-f", "S16_LE",
        "-c", str(CHANNELS),
        "-r", str(SAMPLE_RATE),
        "-d", str(int(duration_sec)),
        "-q",
        "-t", "raw"
    ]
    try:
        raw_bytes = subprocess.check_output(cmd)
        return np.frombuffer(raw_bytes, dtype=np.int16)
    except Exception as e:
        print(f"ALSA arecord error: {e}")
        return np.array([], dtype=np.int16)

def get_rms(duration=1.0):
    audio = record_audio(duration)
    if len(audio) == 0: 
        return 0.0
    
    # Normalize 16-bit integers to float to match the previous logic
    audio_norm = audio.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(audio_norm**2)))

def _get_single_fingerprint(duration=10):
    audio = record_audio(duration)
    if len(audio) == 0:
        return None, 0

    try:
        duration_sec = len(audio) / SAMPLE_RATE
        # AcoustID expects the raw byte string
        fingerprint = acoustid.fingerprint(SAMPLE_RATE, CHANNELS, audio.tobytes())
        response = acoustid.lookup(ACOUSTID_API_KEY, fingerprint, duration_sec, meta='recordings')

        if response['status'] == 'ok' and response['results']:
            best_match = response['results'][0]
            if 'recordings' in best_match:
                recording = best_match['recordings'][0]
                title = recording.get('title', 'Unknown Title')
                artists = recording.get('artists', [])
                artist = artists[0]['name'] if artists else 'Unknown Artist'
                track_duration = recording.get('duration', 0)
                return f"{artist} - {title}", track_duration
    except Exception as e:
        print(f"Identification error on single fingerprint: {e}")

    return None, 0
def identify_track_with_voting(attempts=3, sample_length=10):
    print(f"Initiating {attempts}-sample voting process...")
    votes = []
    durations = {}

    for i in range(attempts):
        print(f"  Taking sample {i+1}/{attempts}...")
        track, duration = _get_single_fingerprint(sample_length)
        if track:
            votes.append(track)
            durations[track] = duration
        if i < attempts - 1:
            time.sleep(3) 

    if not votes:
        return None, 0

    vote_counts = collections.Counter(votes)
    winning_track, count = vote_counts.most_common(1)[0]

    # --- STRICT CONSENSUS LOGIC ---
    # If the highest vote count is 1, but we received multiple different tracks, it's a total tie.
    if count == 1 and len(vote_counts) > 1:
        print("Voting failed: No consensus reached. Samples returned different tracks.")
        return None, 0

    print(f"Voting concluded: '{winning_track}' won with {count}/{len(votes)} valid votes.")
    return winning_track, durations[winning_track]

while True:
    if OFFLINE_QUEUE:
        process_queue()

    rms = get_rms(1.0)

    # --- TURNTABLE IS ACTIVE ---
    if rms > THRESHOLD:
        publish_mqtt("binary_sensor", "ON")

        current_track, track_duration = identify_track_with_voting(attempts=3, sample_length=10)

        if current_track:
            STRIKEOUTS = 0 

            if current_track != LAST_TRACK:
                print(f"New Track Confirmed: {current_track} ({track_duration}s)")
                LAST_TRACK = current_track

                success = publish_mqtt("sensor", current_track, {"duration": track_duration})
                if not success:
                    print("Adding track to offline queue.")
                    OFFLINE_QUEUE.append({"track": current_track, "duration": track_duration})
                    save_queue(OFFLINE_QUEUE)

                # --- THE SMART SLEEP ---
                if track_duration > 60:
                    sleep_time = track_duration - 40 
                    print(f"Entering Smart Sleep for {sleep_time} seconds...")

                    end_time = time.time() + sleep_time
                    while time.time() < end_time:
                        if get_rms(1.0) < THRESHOLD:
                            print("Silence detected early. Needle lifted?")
                            break
                        time.sleep(2) 
                else:
                    print("Track too short for Smart Sleep. Falling back to standard polling.")
                    time.sleep(5)

            else:
                # --- TAIL-END POLLING ---
                print(f"Still playing: {current_track}. Tail-end polling...")
                time.sleep(5)

        else:
            # --- THE STRIKEOUT LOGIC (Run-Out Groove) ---
            STRIKEOUTS += 1
            print(f"No match found. Strikeout {STRIKEOUTS}/3.")

            if STRIKEOUTS >= 3:
                print("Run-out groove detected. Waiting for needle lift...")
                while get_rms(1.0) > THRESHOLD:
                    time.sleep(3) 
                print("Needle lifted. Resetting.")
                STRIKEOUTS = 0
            else:
                time.sleep(5) 

    # --- TURNTABLE IS SILENT ---            
    else:
        publish_mqtt("binary_sensor", "OFF")
        STRIKEOUTS = 0
        time.sleep(2)
# --- CONFIGURATION ---
SAMPLE_RATE = 44100
CHANNELS = 1
QUEUE_FILE = "offline_queue.json"

# --- STATE VARIABLES ---
LAST_TRACK = ""
STRIKEOUTS = 0

# Ensure essential options exist before starting
import sys

# Ensure essential options exist before starting
if not ACOUSTID_API_KEY or not MQTT_BROKER:
    print("\n" + "="*60)
    print("🚨  ACTION REQUIRED: CONFIGURATION MISSING  🚨")
    print("="*60)
    print("The Add-on cannot start because it is missing credentials.")
    print("Please go to the 'Configuration' tab of this Add-on and fill in:")
    print("  1. Your AcoustID API Key")
    print("  2. Your MQTT Broker IP Address")
    print("Once filled out, click 'Save' and restart the Add-on.")
    print("="*60 + "\n")
    sys.exit(1) # Gracefully kill the container
    

# --- MQTT SETUP & AUTO-DISCOVERY ---
def on_connect(client, userdata, flags, reason_code, properties):
    print("Connected to MQTT Broker. Publishing Auto-Discovery configs...")
      
    device_config = {
        "identifiers": ["vinyl_guardian_01"],
        "name": "Vinyl Guardian",
        "model": "Audio Fingerprinter",
        "manufacturer": "Custom Python Script"
    }

    binary_config = {
        "name": "Vinyl Playing",
        "object_id": "vinyl_playing",
        "unique_id": "vg_binary_playing",
        "state_topic": "vinyl_guardian/playing/state",
        "device_class": "sound",
        "device": device_config
    }
    
    track_config = {
        "name": "Vinyl Current Track",
        "object_id": "vinyl_current_track",
        "unique_id": "vg_sensor_track",
        "state_topic": "vinyl_guardian/track/state",
        "json_attributes_topic": "vinyl_guardian/track/attributes",
        "icon": "mdi:record-player",
        "device": device_config
    }

    client.publish("homeassistant/binary_sensor/vinyl_guardian/playing/config", json.dumps(binary_config), retain=True)
    client.publish("homeassistant/sensor/vinyl_guardian/track/config", json.dumps(track_config), retain=True)

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "VinylGuardian")
if MQTT_USER and MQTT_PASSWORD:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
mqtt_client.on_connect = on_connect

dump_runtime_debug_info()

print("Connecting to MQTT...")
try:
    mqtt_client.connect(MQTT_BROKER, int(MQTT_PORT), 60)
    mqtt_client.loop_start() 
except Exception as e:
    print(f"Failed to connect to MQTT: {e}")

def publish_mqtt(sensor_type, state, attributes=None):
    try:
        if sensor_type == "binary_sensor":
            mqtt_client.publish("vinyl_guardian/playing/state", state, retain=True)
        elif sensor_type == "sensor":
            mqtt_client.publish("vinyl_guardian/track/state", state, retain=True)
            if attributes:
                mqtt_client.publish("vinyl_guardian/track/attributes", json.dumps(attributes), retain=True)
        return True
    except Exception as e:
        print(f"MQTT Publish Error: {e}")
        return False

# --- OFFLINE QUEUE LOGIC ---
def load_queue():
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_queue(queue_list):
    try:
        with open(QUEUE_FILE, "w") as f:
            json.dump(queue_list, f)
    except Exception as e:
        print(f"Error saving queue: {e}")

OFFLINE_QUEUE = load_queue()

def process_queue():
    global OFFLINE_QUEUE
    if not OFFLINE_QUEUE:
        return

    print(f"Attempting to process {len(OFFLINE_QUEUE)} queued tracks...")
    successful_items = []
    
    for item in OFFLINE_QUEUE:
        print(f"Pushing queued track to MQTT: {item['track']}")
        success = publish_mqtt("sensor", item['track'], {"duration": item['duration']})
        
        if success:
            successful_items.append(item)
            time.sleep(3) 
        else:
            break
            
    for item in successful_items:
        OFFLINE_QUEUE.remove(item)
    save_queue(OFFLINE_QUEUE)

# --- AUDIO LOGIC ---
def get_rms(duration=1.0):
    audio = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS)
    sd.wait()
    return np.sqrt(np.mean(audio**2))

def _get_single_fingerprint(duration=10):
    audio = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='int16')
    sd.wait()
    
    try:
        duration_sec = len(audio) / SAMPLE_RATE
        fingerprint = acoustid.fingerprint(SAMPLE_RATE, CHANNELS, audio)
        response = acoustid.lookup(ACOUSTID_API_KEY, fingerprint, duration_sec, meta='recordings')
        
        if response['status'] == 'ok' and response['results']:
            best_match = response['results'][0]
            if 'recordings' in best_match:
                recording = best_match['recordings'][0]
                title = recording.get('title', 'Unknown Title')
                artists = recording.get('artists', [])
                artist = artists[0]['name'] if artists else 'Unknown Artist'
                track_duration = recording.get('duration', 0)
                return f"{artist} - {title}", track_duration
    except Exception as e:
        print(f"Identification error on single fingerprint: {e}")
        
    return None, 0

def identify_track_with_voting(attempts=3, sample_length=10):
    print(f"Initiating {attempts}-sample voting process...")
    votes = []
    durations = {}
    
    for i in range(attempts):
        print(f"  Taking sample {i+1}/{attempts}...")
        track, duration = _get_single_fingerprint(sample_length)
        if track:
            votes.append(track)
            durations[track] = duration
        if i < attempts - 1:
            time.sleep(3) 
            
    if not votes:
        return None, 0
        
    vote_counts = collections.Counter(votes)
    winning_track, count = vote_counts.most_common(1)[0]
    
    print(f"Voting concluded: '{winning_track}' won with {count}/{attempts} votes.")
    return winning_track, durations[winning_track]

print("Vinyl Guardian Online. Listening for needle drop...")

while True:
    if OFFLINE_QUEUE:
        process_queue()

    rms = get_rms(1.0)
    
    # --- TURNTABLE IS ACTIVE ---
    if rms > THRESHOLD:
        publish_mqtt("binary_sensor", "ON")
        
        current_track, track_duration = identify_track_with_voting(attempts=3, sample_length=10)
        
        if current_track:
            STRIKEOUTS = 0 
            
            if current_track != LAST_TRACK:
                print(f"New Track Confirmed: {current_track} ({track_duration}s)")
                LAST_TRACK = current_track
                
                success = publish_mqtt("sensor", current_track, {"duration": track_duration})
                if not success:
                    print("Adding track to offline queue.")
                    OFFLINE_QUEUE.append({"track": current_track, "duration": track_duration})
                    save_queue(OFFLINE_QUEUE)
                
                # --- THE SMART SLEEP ---
                if track_duration > 60:
                    sleep_time = track_duration - 40 
                    print(f"Entering Smart Sleep for {sleep_time} seconds...")
                    
                    end_time = time.time() + sleep_time
                    while time.time() < end_time:
                        if get_rms(1.0) < THRESHOLD:
                            print("Silence detected early. Needle lifted?")
                            break
                        time.sleep(2) 
                else:
                    print("Track too short for Smart Sleep. Falling back to standard polling.")
                    time.sleep(5)
                    
            else:
                # --- TAIL-END POLLING ---
                print(f"Still playing: {current_track}. Tail-end polling...")
                time.sleep(5)
                
        else:
            # --- THE STRIKEOUT LOGIC (Run-Out Groove) ---
            STRIKEOUTS += 1
            print(f"No match found. Strikeout {STRIKEOUTS}/3.")
            
            if STRIKEOUTS >= 3:
                print("Run-out groove detected. Waiting for needle lift...")
                while get_rms(1.0) > THRESHOLD:
                    time.sleep(3) 
                print("Needle lifted. Resetting.")
                STRIKEOUTS = 0
            else:
                time.sleep(5) 
                
    # --- TURNTABLE IS SILENT ---            
    else:
        publish_mqtt("binary_sensor", "OFF")
        STRIKEOUTS = 0
        time.sleep(2)
