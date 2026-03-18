#!/bin/sh

# Helper to print with a timestamp
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Simple version discovery
VERSION=$(grep "^version:" config.yaml | sed 's/version: //g' | tr -d '"' | tr -d "'")

echo ""
log "========================================================"
log "🔄 BOOTING VINYL GUARDIAN v${VERSION} 🔄"
log "========================================================"
echo ""

# --- AUDIO HARDWARE ACTIVATION ---
log "Targeting Card 1 (Analog Audio) for volume reduction and unmuting..."
# 'unmute' opens the channel, 'cap' sets it to capture/record mode
amixer -c 1 sset 'Capture' 2% unmute cap >/dev/null 2>&1 || true
amixer -c 1 sset 'Mic' 2% unmute cap >/dev/null 2>&1 || true
amixer -c 1 sset 'Internal Mic' 2% unmute cap >/dev/null 2>&1 || true
amixer -c 1 sset 'Line' 2% unmute cap >/dev/null 2>&1 || true
log "Volume and Mixer configuration complete."

log "Launching main Python application..."
# -u ensures Python logs aren't buffered (shows them in HA immediately)
exec python3 -u /usr/src/app/vinyl_guardian.py

