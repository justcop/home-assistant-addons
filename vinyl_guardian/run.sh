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

# Home Assistant installs the add-on image itself from the repository version.
# For development branches, swap in the selected branch's Python runtime files
# at startup while leaving the container and Supervisor-managed configuration alone.
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

# Do not force the first alsa_input to become Home Assistant's global default.
# Source ordering is not a stable device identifier and can change after OS,
# kernel, USB or PulseAudio updates. Vinyl Guardian selects its own capture
# source per-process using PULSE_SOURCE in audio_source.py.
SOURCE_COUNT=$(pactl list short sources 2>/dev/null | grep -v "\.monitor" | wc -l | tr -d ' ')
if [ "$SOURCE_COUNT" = "0" ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] 🚨 ERROR: PulseAudio exposes no capture sources. Check Home Assistant Audio/card profiles."
elif [ "$DEBUG_MODE" == "true" ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] 🎚️ ${SOURCE_COUNT} capture source(s) exposed. Guardian will select its own input."
fi

if [ "$DEBUG_MODE" == "true" ]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] Audio configuration complete. Launching main Python application..."
fi

python3 /usr/src/app/vinyl_guardian.py