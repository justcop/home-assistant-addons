#!/usr/bin/with-contenv bashio

# Extract version using Home Assistant's native bashio API
export ADDON_VERSION=$(bashio::addon.version 2>/dev/null)

# Fallback just in case the API is slow to respond
if [ -z "$ADDON_VERSION" ] || [ "$ADDON_VERSION" == "null" ]; then
    export ADDON_VERSION="Unknown"
fi

echo "[$(date +"%Y-%m-%d %H:%M:%S")] ========================================================"
echo "[$(date +"%Y-%m-%d %H:%M:%S")] 🔄 BOOTING VINYL GUARDIAN v${ADDON_VERSION} 🔄"
echo "[$(date +"%Y-%m-%d %H:%M:%S")] ========================================================"

# Read configuration from options.json
DEBUG_MODE=$(jq --raw-output '.debug_logging' /data/options.json)
# Remove obsolete configuration keys through the Supervisor. Existing recording,
# calibration, MQTT and Last.fm settings are left in place.
for option in code_branch acoustid_key audio_threshold debug_one_shot mic_volume record_diagnostic_sample; do
    if jq --exit-status --arg option "$option" 'has($option)' /data/options.json >/dev/null; then
        bashio::addon.option "$option" || echo "Could not remove obsolete option $option; it is ignored."
    fi
done

# Reuse is a normal setting and defaults on, including upgrades from the old
# advanced setting. Explicit top-level choices are retained.
if ! jq --exit-status 'has("reuse_calibration_audio")' /data/options.json >/dev/null; then
    bashio::addon.option reuse_calibration_audio true || echo "Could not persist default reuse setting; runtime defaults to on."
fi
if jq --exit-status '.advanced | has("reuse_calibration_audio") or has("manual_override_mic_volume") or has("manual_override_music_threshold") or has("manual_override_motor_threshold")' /data/options.json >/dev/null; then
    bashio::addon.option advanced "$(jq --compact-output '.advanced | del(.reuse_calibration_audio, .manual_override_mic_volume, .manual_override_music_threshold, .manual_override_motor_threshold)' /data/options.json)" || echo "Could not remove obsolete advanced settings; they are ignored."
fi

# Only show diagnostic spam if debug mode is explicitly true
if [ "$DEBUG_MODE" == "true" ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] --- PULSEAUDIO HARDWARE DIAGNOSTIC ---"
    pactl info
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Available Audio Sources:"
    pactl list short sources
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] --------------------------------------"
fi

if [ "$DEBUG_MODE" == "true" ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Audio configuration complete. Launching main Python application..."
fi

exec python3 /usr/src/app/vinyl_guardian.py
