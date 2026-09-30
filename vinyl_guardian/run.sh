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
CODE_BRANCH=$(jq --raw-output '.code_branch // "main"' /data/options.json)

# The Supervisor installs the image built from the add-on repository. For development
# branches, replace only the runtime Python files with the selected branch at startup.
# Keeping "main" uses the files baked into the installed image and requires no network.
if [ "$CODE_BRANCH" != "main" ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] 🌿 Runtime branch selected: $CODE_BRANCH"
    export CODE_BRANCH

    if python3 - <<'PY'
import io
import os
import shutil
import sys
import tarfile
import urllib.parse
import urllib.request

branch = os.environ["CODE_BRANCH"].strip()
if not branch:
    print("Branch name is empty.", file=sys.stderr)
    sys.exit(1)

url = (
    "https://codeload.github.com/justcop/home-assistant-addons/tar.gz/refs/heads/"
    + urllib.parse.quote(branch, safe="")
)

try:
    with urllib.request.urlopen(url, timeout=30) as response:
        archive = response.read()

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = [
            member
            for member in tar.getmembers()
            if member.isfile()
            and "/vinyl_guardian/" in member.name
            and member.name.endswith(".py")
        ]

        if not members:
            raise RuntimeError(
                f"Branch {branch!r} contains no vinyl_guardian Python files."
            )

        app_dir = "/usr/src/app"
        copied = 0
        for member in members:
            filename = os.path.basename(member.name)
            source = tar.extractfile(member)
            if source is None:
                continue
            with source, open(os.path.join(app_dir, filename), "wb") as target:
                shutil.copyfileobj(source, target)
            copied += 1

        if copied == 0:
            raise RuntimeError("No runtime Python files could be copied.")

    print(f"Loaded {copied} Python files from branch {branch!r}.")
except Exception as exc:
    print(f"Unable to load branch {branch!r}: {exc}", file=sys.stderr)
    sys.exit(1)
PY
    then
        echo "[$(date +"%Y-%m-%d %H:%M:%S")] ✅ Branch '$CODE_BRANCH' loaded successfully."
    else
        echo "[$(date +"%Y-%m-%d %H:%M:%S")] 🚨 ERROR: Could not load branch '$CODE_BRANCH'."
        echo "[$(date +"%Y-%m-%d %H:%M:%S")] Refusing to fall back silently to main. Set code_branch to 'main' or fix the branch name."
        exit 1
    fi
else
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] 🌿 Runtime branch: main (using installed image)"
fi

# Only show diagnostic spam if debug mode is explicitly true
if [ "$DEBUG_MODE" == "true" ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] --- PULSEAUDIO HARDWARE DIAGNOSTIC ---"
    pactl info
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Available Audio Sources:"
    pactl list short sources
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] --------------------------------------"
fi

# Experimental runtimes select their own source without changing HA defaults.
if [ ! -f /usr/src/app/audio_source.py ]; then
# Find physical soundcard input quietly
PHYSICAL_SINK=$(pactl list short sources | grep "alsa_input" | awk '{print $2}' | head -n 1)

if [ -z "$PHYSICAL_SINK" ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] 🚨 ERROR: Could not find physical ALSA capture device! Please ensure 'Audio' is enabled in Add-on config."
else
    if [ "$DEBUG_MODE" == "true" ]; then
        echo "[$(date +"%Y-%m-%d %H:%M:%S")] 🎯 TARGET LOCKED: Found physical mic port -> $PHYSICAL_SINK"
    fi

    pactl set-default-source "$PHYSICAL_SINK"
    pactl set-source-mute "$PHYSICAL_SINK" 0

    # Grab Volume from options.json
    CONFIG_VOL=$(jq --raw-output '.mic_volume' /data/options.json)

    # mic_volume belongs to legacy branches. A retained legacy value must not
    # override the stable detector's calibrated input volume after switching back.
    if [ "$CODE_BRANCH" != "main" ] && [ "$CONFIG_VOL" != "null" ] && [ -n "$CONFIG_VOL" ]; then
        if [ "$DEBUG_MODE" == "true" ]; then
            echo "[$(date +"%Y-%m-%d %H:%M:%S")] Applying UI Configuration: Setting capture volume to ${CONFIG_VOL}%..."
        fi
        pactl set-source-volume "$PHYSICAL_SINK" "${CONFIG_VOL}%"
    fi
fi

fi

if [ "$DEBUG_MODE" == "true" ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Audio configuration complete. Launching main Python application..."
fi

python3 /usr/src/app/vinyl_guardian.py
