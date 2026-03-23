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

log "--- PULSEAUDIO HARDWARE DIAGNOSTIC ---"
pactl info || log "Warning: Could not get PulseAudio info"
log "Available Audio Sources:"
pactl list sources short || log "Warning: Could not list Pulse sources"
log "--------------------------------------"

# --- NATIVE AUDIO MIXER CONFIGURATION ---
log "Unmuting default audio capture source..."
# @DEFAULT_SOURCE@ automatically targets the active line-in/mic
pactl set-source-mute @DEFAULT_SOURCE@ 0 >/dev/null 2>&1 || true

log "Setting capture volume to 2% to prevent Line-Level clipping..."
pactl set-source-volume @DEFAULT_SOURCE@ 2% >/dev/null 2>&1 || true

log "Audio configuration complete. Launching main Python application..."
exec python3 -u /usr/src/app/vinyl_guardian.py
