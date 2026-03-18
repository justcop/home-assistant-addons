#!/bin/sh
echo "Starting Vinyl Guardian Audio Service..."

# --- THE BRUTE-FORCE VOLUME HACK ---
# Setting volume to 2% to prevent line-level clipping, and forcing the pins to UNMUTE and CAPTURE
echo "Targeting Card 1 (Analog Audio) for volume reduction and unmuting..."
amixer -c 1 sset 'Capture' 2% unmute cap >/dev/null 2>&1 || true
amixer -c 1 sset 'Mic' 2% unmute cap >/dev/null 2>&1 || true
amixer -c 1 sset 'Internal Mic' 2% unmute cap >/dev/null 2>&1 || true
amixer -c 1 sset 'Line' 2% unmute cap >/dev/null 2>&1 || true
echo "Volume configuration complete."

echo "Launching main Python application..."
exec python3 -u /usr/src/app/vinyl_guardian.py
