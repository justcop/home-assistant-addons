#!/usr/bin/with-contenv bashio

echo "[2026-03-30 $(date +"%H:%M:%S")] 🔄 BOOTING VINYL GUARDIAN v 🔄"
echo "[2026-03-30 $(date +"%H:%M:%S")] ========================================================"

# Ensure PulseAudio recognizes the hardware
echo "[2026-03-30 $(date +"%H:%M:%S")] --- PULSEAUDIO HARDWARE DIAGNOSTIC ---"
pactl info
echo "[2026-03-30 $(date +"%H:%M:%S")] Available Audio Sources:"
pactl list short sources
echo "[2026-03-30 $(date +"%H:%M:%S")] --------------------------------------"

# Find physical soundcard input
PHYSICAL_SINK=$(pactl list short sources | grep "alsa_input" | awk '{print $2}' | head -n 1)

if [ -z "$PHYSICAL_SINK" ]; then
    echo "🚨 ERROR: Could not find physical ALSA capture device! Please ensure 'Audio' is enabled in Add-on config."
else
    echo "🎯 TARGET LOCKED: Found physical mic port -> $PHYSICAL_SINK"
    pactl set-default-source "$PHYSICAL_SINK"
    pactl set-source-mute "$PHYSICAL_SINK" 0
    
    # Grab Volume from options.json (Absolute Path)
    CONFIG_VOL=$(jq --raw-output '.mic_volume' /data/options.json)
    
    if [ "$CONFIG_VOL" != "null" ] && [ -n "$CONFIG_VOL" ]; then
        echo "Applying UI Configuration: Setting capture volume to ${CONFIG_VOL}%..."
        pactl set-source-volume "$PHYSICAL_SINK" "${CONFIG_VOL}%"
    fi
fi

echo "Audio configuration complete. Launching main Python application..."
python3 /usr/src/app/vinyl_guardian.py