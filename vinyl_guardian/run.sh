#!/bin/sh
echo "Starting Vinyl Guardian Audio Service..."

# --- THE BRUTE-FORCE VOLUME HACK ---
echo "Targeting Card 1 (Analog Audio) for volume reduction..."
amixer -c 1 sset 'Capture' 2% >/dev/null 2>&1 || true
amixer -c 1 sset 'Mic' 2% >/dev/null 2>&1 || true
amixer -c 1 sset 'Internal Mic' 2% >/dev/null 2>&1 || true
amixer -c 1 sset 'Line' 2% >/dev/null 2>&1 || true
echo "Volume configuration complete."

# --- DIAGNOSTICS ---
echo "--- ALSA HARDWARE CHECK ---"
ls -la /dev/snd
arecord -L | grep hw:
echo "---------------------------"

echo "Launching main Python application..."
exec python3 -u /usr/src/app/vinyl_guardian.py
