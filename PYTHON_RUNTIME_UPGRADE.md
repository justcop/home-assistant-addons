# Stable Python migration

Reviewed 9 November 2026 for #131. Python.org lists 3.15.2 as the current stable patch release. Home Assistant docker-base 2026.11.0 builds that release for amd64 and aarch64.

| Add-on | Proposed runtime | Advertised architectures checked by CI |
| --- | --- | --- |
| AudioShelf | HA Python 3.15.2, Alpine 3.24 | amd64, aarch64 |
| Listening Analytics | HA Python 3.15.2, Alpine 3.24 | amd64, aarch64 |
| Money Locations | HA Python 3.15.2, Alpine 3.24 | amd64, aarch64 |
| Music Video Companion | HA Python 3.15.2, Alpine 3.24 | amd64, aarch64 |
| Vinyl Guardian | HA Python 3.15.2, Alpine 3.24 | amd64 |
| Work Audit | official Python 3.15.2, Alpine 3.24 | amd64, aarch64 |
| Python Venv SSH | official Python 3.15.2, Debian bookworm | amd64, aarch64; armv7/i386 manifest-only; armhf blocked |

The SSH add-on now uses Python 3.15.2 too, with native amd64/ARM64 venv creation checks. Its official slim-bookworm manifest also includes arm/v7 and 386, but no arm/v6 image for advertised armhf support. The current 3.11.18-slim-bookworm and 3.13.18-slim-bookworm manifests likewise lack arm/v6, so this is a pre-existing architecture-support gap, not a reason to freeze the other architectures. A verified source build for armhf, or an explicit decision to retire that architecture, is still required. No advertised architecture was removed here. Native 32-bit execution and SSH integration remain unverified. The Node bridge and curl/jq cleanup add-ons do not run Python.

## Vinyl Guardian crash

Rebuilt the exact #122 source at 25465bd39c14328eddfb3aa3b8f0561c2c9de4b6 and ran each import in a fresh network-isolated process with faulthandler. audioop, numpy, alsaaudio, MQTT, requests, pylast and pydub import successfully. Importing shazamio crashes while loading the native shazamio_core extension on the old Python 3.14 image. The original import order reproduces the same fault. This identifies the failing extension boundary, not its underlying C/Rust defect.

Diagnostic evidence: https://github.com/justcop/home-assistant-addons/actions/runs/42589599234 . The diagnostic job intentionally records failures rather than being a compatibility pass.

The replacement combines the ShazamIO 0.9 dependency update already merged in #129 with the current dated Python 3.15.2 image. Its native imports and actual audio decoder/fingerprint generation pass in the built production image. The runtime smoke test blocks network access, substitutes only the recognition HTTP response, exercises the production response parser, initializes a Paho client, and opens the ALSA null device. Real input hardware, live Shazam results and scrobble delivery remain unverified.

## Validation and release

Python CI uses 3.15. The runtime workflow builds native images on amd64 and ARM64 runners, asserts the actual 3.15.2 interpreter, checks independent and combined imports, verifies image decode/resize/encode, and checks server startup and ingress access boundaries. Existing application and browser suites remain in place. Older CI interpreters are not a substitute for testing production images.

No add-on versions are bumped here. Do not merge a failing PR or claim green tests prove Home Assistant, audio hardware, Spotify or external-service compatibility. After manual review, release through the existing controlled process and validate those integrations on the actual installation. Automatic merging remains disabled.

Sources: https://www.python.org/downloads/ , https://github.com/home-assistant/docker-base/releases/tag/2026.11.0 , official image manifests, and the linked CI diagnostic.
