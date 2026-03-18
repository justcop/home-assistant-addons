#!/bin/sh

# Dynamically pull the version number from config.yaml
if [ -f "/usr/src/app/config.yaml" ]; then
    VERSION=$(grep "^version:" /usr/src/app/config.yaml | sed 's/version: //g' | tr -d '"' | tr -d "'")
else
    VERSION="Unknown"
fi

echo ""
echo "========================================================"
echo "🔄 BOOTING VINYL GUARDIAN v${VERSION} 🔄"
echo "========================================================"
echo ""

# --- THE BRUTE-FORCE VOLUME HACK ---
echo "Targeting Card 1 (Analog Audio) for volume reduction and unmuting..."
amixer -c 1 sset 'Capture' 2% unmute cap >/dev/null 2>&1 || true
amixer -c 1 sset 'Mic' 2% unmute cap >/dev/null 2>&1 || true
amixer -c 1 sset 'Internal Mic' 2% unmute cap >/dev/null 2>&1 || true
amixer -c 1 sset 'Line' 2% unmute cap >/dev/null 2>&1 || true
echo "Volume configuration complete."

echo "Launching main Python application..."
exec python3 -u /usr/src/app/vinyl_guardian.py
