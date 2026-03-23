#!/bin/sh

# Helper to print with a timestamp
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

VERSION=$(grep "^version:" config.yaml | sed 's/version: //g' | tr -d '"' | tr -d "'")

# Parse mic volume from Home Assistant options (defaults to 10 if not found)
MIC_VOLUME=$(grep -o '"mic_volume": *[0-9]*' /data/options.json | grep -o '[0-9]*')
if [ -z "$MIC_VOLUME" ]; then
    MIC_VOLUME=10
fi

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

# --- DYNAMIC MICROPHONE TARGETING ---
MIC_SOURCE=$(pactl list short sources | grep -i "input" | awk '{print $2}' | head -n 1)

if [ -z "$MIC_SOURCE" ]; then
    log "🚨 ERROR: No physical input source found! Falling back to default."
    MIC_SOURCE="@DEFAULT_SOURCE@"
else
    log "🎯 TARGET LOCKED: Found physical mic port -> $MIC_SOURCE"
fi

log "Setting $MIC_SOURCE as the default recording device..."
pactl set-default-source "$MIC_SOURCE"

log "Unmuting the microphone..."
pactl set-source-mute "$MIC_SOURCE" 0 >/dev/null 2>&1 || true

log "Applying UI Configuration: Setting capture volume to ${MIC_VOLUME}%..."
pactl set-source-volume "$MIC_SOURCE" ${MIC_VOLUME}% >/dev/null 2>&1 || true

log "Audio configuration complete. Launching main Python application..."
exec python3 -u /usr/src/app/vinyl_guardian.py
