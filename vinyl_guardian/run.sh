#!/bin/sh
echo "Starting Vinyl Guardian Audio Service..."

# --- THE BRUTE-FORCE VOLUME HACK ---
echo "Targeting Card 1 (Analog Audio) for volume reduction..."

# We fire these directly at Card 1, bypassing the need for /proc/asound index files
amixer -c 1 sset 'Capture' 2% >/dev/null 2>&1 || true
amixer -c 1 sset 'Mic' 2% >/dev/null 2>&1 || true
amixer -c 1 sset 'Internal Mic' 2% >/dev/null 2>&1 || true
amixer -c 1 sset 'Line' 2% >/dev/null 2>&1 || true

echo "Volume configuration complete."
echo "Launching Python application..."

# Launch the core Python script
exec python3 /usr/src/app/vinyl_guardian.py
}

show_command_output() {
    label="$1"
    shift
    log_debug "$label"
    "$@" 2>&1 || true
}

show_mixer_controls() {
    card="$1"
    show_command_output "Available mixer controls on Card ${card}:" amixer -c "$card" scontrols
}

set_control_if_present() {
    card="$1"
    control="$2"

    if amixer -c "$card" scontrols 2>/dev/null | grep -F "'${control}'" >/dev/null 2>&1; then
        echo "Setting '${control}' on card ${card} to 2%..."
        amixer -c "$card" sset "$control" 2% >/dev/null 2>&1 || true
    fi
}

echo "Starting Vinyl Guardian Audio Service..."
echo "Hardware debug mode: ${DEBUG_MODE} (1=enabled, 0=disabled)"

if [ "$DEBUG_MODE" = "1" ]; then
    echo "--- ALSA AUDIO HARDWARE DEBUG ---"
    show_command_output "Home Assistant options summary:" show_options_summary
    show_file_if_present "Contents of /proc/asound/cards:" "/proc/asound/cards"
    show_file_if_present "Contents of /proc/asound/devices:" "/proc/asound/devices"
    show_command_output "Contents of /dev/snd:" ls -la /dev/snd
    show_command_output "Available ALSA recording devices (arecord -l):" arecord -l
    show_command_output "Available ALSA recording device names (arecord -L):" arecord -L
    show_command_output "Available ALSA playback devices (aplay -l):" aplay -l
fi

CARDS="$(list_cards)"
if [ -n "$CARDS" ]; then
    if [ "$DEBUG_MODE" = "1" ]; then
        for CARD in $CARDS; do
            show_mixer_controls "$CARD"
        done
    fi
else
    echo "No ALSA cards detected in /proc/asound/cards."
fi

if [ "$DEBUG_MODE" = "1" ]; then
    echo "---------------------------------------"
fi

echo "Configuring audio hardware volume..."
if [ -n "$CARDS" ]; then
    for CARD in $CARDS; do
        set_control_if_present "$CARD" "Capture"
        set_control_if_present "$CARD" "Mic"
        set_control_if_present "$CARD" "Internal Mic"
        set_control_if_present "$CARD" "Line"
    done
else
    echo "Skipping mixer configuration because no ALSA cards were detected."
fi

echo "Launching Python application..."
exec python3 /usr/src/app/vinyl_guardian.py
