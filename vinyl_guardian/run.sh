#!/usr/bin/with-contenv bashio

# Extract version using Home Assistant's native bashio API
export ADDON_VERSION=$(bashio::addon.version 2>/dev/null)

# Fallback just in case the API is slow to respond
if [ -z "$ADDON_VERSION" ] || [ "$ADDON_VERSION" == "null" ]; then
    export ADDON_VERSION="Unknown"
fi

echo "[$(date +"%Y-%m-%d %H:%M:%S")] ========================================================"
echo "[$(date +"%Y-%m-%d %H:%M:%S")] 🔄 BOOTING VINYL GUARDIAN v${ADDON_VERSION} 🔄"
echo "[$(date +"%Y-%m-%d %H:%M:%S")] ========================================================"

# Read debug mode from options.json
DEBUG_MODE=$(jq --raw-output '.debug_logging' /data/options.json)

# Only show diagnostic spam if debug mode is explicitly true
if [ "$DEBUG_MODE" == "true" ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] --- PULSEAUDIO HARDWARE DIAGNOSTIC ---"
    pactl info
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Available Audio Sources:"
    pactl list short sources
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] --------------------------------------"
fi

# Find physical soundcard input quietly
PHYSICAL_SINK=$(pactl list short sources | grep "alsa_input" | awk '{print $2}' | head -n 1)

if [ -z "$PHYSICAL_SINK" ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] 🚨 ERROR: Could not find physical ALSA capture device! Please ensure 'Audio' is enabled in Add-on config."
else
    if [ "$DEBUG_MODE" == "true" ]; then
        echo "[$(date +"%Y-%m-%d %H:%M:%S")] 🎯 TARGET LOCKED: Found physical mic port -> $PHYSICAL_SINK"
    fi
    
    pactl set-default-source "$PHYSICAL_SINK"
    pactl set-source-mute "$PHYSICAL_SINK" 0
    
    # Grab Volume from options.json
    CONFIG_VOL=$(jq --raw-output '.mic_volume' /data/options.json)
    
    if [ "$CONFIG_VOL" != "null" ] && [ -n "$CONFIG_VOL" ]; then
        if [ "$DEBUG_MODE" == "true" ]; then
            echo "[$(date +"%Y-%m-%d %H:%M:%S")] Applying UI Configuration: Setting capture volume to ${CONFIG_VOL}%..."
        fi
        pactl set-source-volume "$PHYSICAL_SINK" "${CONFIG_VOL}%"
    fi
fi

if [ "$DEBUG_MODE" == "true" ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Audio configuration complete. Launching main Python application..."
fi

python3 /usr/src/app/vinyl_guardian.py