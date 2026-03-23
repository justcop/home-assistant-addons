#!/bin/sh

# Helper to print with a timestamp
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

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

# --- DYNAMIC MICROPHONE TARGETING ---
# Find the exact name of the physical input device (ignoring monitor loopbacks)
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

log "Setting capture volume to 2% to prevent Line-Level clipping..."
pactl set-source-volume "$MIC_SOURCE" 50% >/dev/null 2>&1 || true

log "Audio configuration complete. Launching main Python application..."
exec python3 -u /usr/src/app/vinyl_guardian.py
