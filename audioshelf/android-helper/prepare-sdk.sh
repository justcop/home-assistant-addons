#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p app/libs
sdk_file=app/libs/spotify-app-remote-release-0.8.0.aar
curl --fail --location --retry 3 --output "$sdk_file" https://raw.githubusercontent.com/spotify/android-sdk/5aa4d62465f61a0677081ae9a3108177d0365fc3/app-remote-lib/spotify-app-remote-release-0.8.0.aar
printf '%s  %s\n' b5a6dd880eaf01f63a871cba9ef7af77c341f8a94ffc8fdf2e9021f9a9d4c198 "$sdk_file" | sha256sum --check
