#!/bin/bash
echo "Starting Vinyl Guardian Audio Service..."

# --- HARDWARE DEBUG LOGGING ---
echo "--- ALSA AUDIO HARDWARE DEBUG ---"
echo "Available Physical Recording Devices:"
arecord -l || true

echo "Available mixer controls on Card 0:"
amixer -c 0 scontrols || true

echo "Available mixer controls on Card 1:"
amixer -c 1 scontrols || true
echo "---------------------------------------"

# --- THE VOLUME HACK ---
echo "Configuring audio hardware volume..."
# Loop through card 0 and card 1 to ensure we hit the physical hardware directly, 
# bypassing the virtual container default.
for CARD in 0 1; do
    amixer -c $CARD sset 'Capture' 2% || true
    amixer -c $CARD sset 'Mic' 2% || true
    amixer -c $CARD sset 'Internal Mic' 2% || true
done

echo "Launching Python application..."

# Launch the core Python script
python3 /usr/src/app/vinyl_guardian.py
