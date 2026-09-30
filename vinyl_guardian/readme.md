🎵 Vinyl Guardian
Vinyl Guardian is a custom Home Assistant Add-on that bridges the gap between your analog record player and your digital smart home.
By listening to the audio output of your turntable, Vinyl Guardian automatically detects when the needle drops, records a short snippet, identifies the song using Shazam, and publishes the track metadata natively to Home Assistant via MQTT. It even includes bulletproof, native Last.fm scrobbling that perfectly mimics digital media players.
✨ Features
Zero-Key Shazam Recognition: Uses the shazamio library to fingerprint and identify tracks completely free, with no API keys or rate limits to worry about.
Native Last.fm Scrobbling: Built-in Last.fm integration that strictly follows official scrobbling rules (waits for 50% of the track duration or 4 minutes of continuous physical playtime).
Smart Needle-Lift Detection: If you lift the needle halfway through a song, the Add-on detects the silence and instantly aborts the scrobble to prevent false logs.
MQTT Auto-Discovery: Automatically creates beautiful, dedicated sensors in your Home Assistant dashboard without any manual YAML configuration.
Audio Health Monitoring: Actively monitors the audio stream and warns you in the Add-on logs if your audio is clipping or too quiet.
UI Volume Control: Adjust your physical soundcard's input volume directly from the Home Assistant Add-on configuration screen.
Runtime Branch Selection: Set `code_branch` in the Add-on configuration to run the Python code from another branch of this repository. `main` remains the safe default.
🛠️ Prerequisites
Hardware: A USB soundcard, audio capture device, or direct line-in connected to your Home Assistant host machine. You will need to route your turntable/pre-amp output into this input.
Software: An active MQTT Broker (like the official Mosquitto broker Add-on) running in Home Assistant.
📦 Installation
Navigate to Settings > Add-ons > Add-on Store in Home Assistant.
Click the three dots (⋮) in the top right corner and select Repositories.
Add the URL to your custom GitHub repository.
Close the modal, scroll down (or refresh), and look for Vinyl Guardian.
Click Install.
⚙️ Configuration
Before starting the Add-on, configure your settings in the UI:

`code_branch`: Leave this as `main` for the normal installed version. To test development code, choose a Git branch from the dropdown and restart the Add-on. Vinyl Guardian will download the Python runtime files from that branch each time it starts. If the branch cannot be downloaded, startup stops rather than silently running `main`.

Branch selection changes the runtime Python files only. Changes to `Dockerfile`, `config.yaml`, `run.sh`, system packages, or Python dependencies still require a normal Add-on rebuild/update.

The dropdown includes all repository branches and is refreshed automatically when branches change. Old development branches are available for testing but may not support current settings. Save and restart after changing `code_branch`. The installed version shown by Home Assistant is the delivery version from `main`, not the selected branch's original version.

The configuration also exposes the experimental audio-source, collection and harness settings. These take effect when running `vinyl-guardian-detection-v2`; the stable detector ignores them. Audio scanning on that branch uses its own per-process input selector, rather than the stable startup script changing Home Assistant's default input.

### Automatic updates

The release workflow checks every repository branch for changes to Vinyl Guardian runtime files, dependencies and configuration. It updates the branch dropdown and bumps the delivery version on `main` whenever those change. Home Assistant offers the update after refreshing the add-on repository, rather than immediately when a commit is pushed. No update is installed automatically by this workflow.

Versions now use `MAJOR.MINOR.PATCH`, starting at `4.40.0`, above the previous stable and experimental versions. Ordinary changes get a patch bump. Conventional `feat:` commits request a minor bump; `type!:` or `BREAKING CHANGE:` requests a major bump. The largest request wins for a batch of changes. The workflow can also be run manually with `patch`, `minor` or `major`. This is an explicit convention, not an AI guess about whether a change is compatible. Documentation and test-only edits do not publish updates.

Selecting another branch downloads only its Python runtime. Dependency, launcher and schema changes still require the advertised add-on update. Returning to `main` uses the stable files baked into that updated image.
