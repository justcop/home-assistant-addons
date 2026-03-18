#!/bin/sh

OPTIONS_FILE="/data/options.json"

read_debug_mode() {
    python3 - <<'PY'
import json
from pathlib import Path

options_path = Path('/data/options.json')
try:
    options = json.loads(options_path.read_text())
except Exception:
    options = {}

value = options.get('debug_logging', True)
print('1' if value else '0')
PY
}

DEBUG_MODE="$(read_debug_mode 2>/dev/null)"
if [ -z "$DEBUG_MODE" ]; then
    DEBUG_MODE="1"
fi

log_debug() {
    if [ "$DEBUG_MODE" = "1" ]; then
        echo "$1"
    fi
}

list_cards() {
    if [ -r /proc/asound/cards ]; then
        awk '/^[[:space:]]*[0-9]+ \[/{print $1}' /proc/asound/cards
    fi
}

show_file_if_present() {
    label="$1"
    path="$2"
    log_debug "$label"
    if [ -r "$path" ]; then
        cat "$path" 2>&1 || true
    else
        log_debug "  $path is not present."
    fi
}

show_options_summary() {
    python3 - <<'PY'
import json
from pathlib import Path

options_path = Path('/data/options.json')
try:
    options = json.loads(options_path.read_text())
except Exception as err:
    print(f"Unable to read {options_path}: {err}")
else:
    summary = {
        'acoustid_key': 'set' if options.get('acoustid_key') else 'missing',
        'mqtt_broker': options.get('mqtt_broker', ''),
        'mqtt_port': options.get('mqtt_port', 1883),
        'mqtt_user': 'set' if options.get('mqtt_user') else 'missing',
        'mqtt_password': 'set' if options.get('mqtt_password') else 'missing',
        'audio_threshold': options.get('audio_threshold', 0.015),
        'debug_logging': options.get('debug_logging', True),
    }
    print(summary)
PY
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
