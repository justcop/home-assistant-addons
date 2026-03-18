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

echo "Launching Python application..."

# Launch the core Python script
python3 /usr/src/app/vinyl_guardian.py
