#!/bin/bash
echo "Starting Vinyl Guardian Audio Service..."

# --- HARDWARE DEBUG LOGGING ---
# Prints the exact names of your Dell Wyse's volume controls to the HA Log
echo "--- ALSA AUDIO HARDWARE DEBUG ---"
echo "Available mixer controls on this machine:"
amixer scontrols || true
echo "---------------------------------------"

# --- THE VOLUME HACK ---
echo "Configuring audio hardware volume..."
# Drop the volume of common capture inputs down to 2% to prevent line-level clipping.
# The '|| true' ensures the container doesn't crash if your specific machine 
# doesn't use one of these exact names.
amixer sset 'Capture' 2% || true
amixer sset 'Mic' 2% || true
amixer sset 'Internal Mic' 2% || true

echo "Launching Python application..."

# Launch the core Python script
python3 /usr/src/app/vinyl_guardian.py
