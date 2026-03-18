#!/bin/sh
echo "Starting Vinyl Guardian Audio Service..."

# --- ALSA CONFIGURATION OVERRIDE ---
# This stops libportaudio from crashing when it looks for PulseAudio
echo "Writing custom ALSA hardware config for Card 1..."
cat << 'EOF' > /etc/asound.conf
pcm.!default {
    type hw
    card 1
}
ctl.!default {
    type hw
    card 1
}
EOF

# --- THE BRUTE-FORCE VOLUME HACK ---
echo "Targeting Card 1 (Analog Audio) for volume reduction..."
amixer -c 1 sset 'Capture' 2% >/dev/null 2>&1 || true
amixer -c 1 sset 'Mic' 2% >/dev/null 2>&1 || true
amixer -c 1 sset 'Internal Mic' 2% >/dev/null 2>&1 || true
amixer -c 1 sset 'Line' 2% >/dev/null 2>&1 || true
echo "Volume configuration complete."

# --- CRASH ISOLATION TEST ---
echo "--- Testing Python Dependencies ---"
python3 -u -c "print('1. Core Python is working.')"
python3 -u -c "import acoustid; print('2. AcoustID library loaded.')"
python3 -u -c "import paho.mqtt.client; print('3. MQTT library loaded.')"
python3 -u -c "import sounddevice; print('4. Sounddevice/PortAudio library loaded.')"
echo "-----------------------------------"

echo "Launching main Python application..."
exec python3 -u /usr/src/app/vinyl_guardian.py
echo "Configuring audio hardware volume..."
if [ -n "$CARDS" ]; then
    for CARD in $CARDS; do
        set_control_if_present "$CARD" "Capture"
        set_control_if_present "$CARD" "Mic"
        set_control_if_present "$CARD" "Internal Mic"
        set_control_if_present "$CARD" "Line"
    done
else
    echo "Skipping mixer configuration because no ALSA cards were detected."
fi

echo "Launching Python application..."
exec python3 -u /usr/src/app/vinyl_guardian.py
