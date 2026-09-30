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


### Settings across branches

The delivery workflow reads every selectable branch's `config.yaml` and adds compatible settings to the schema installed from `main`, including nested settings such as `advanced`. Existing main defaults and field types take precedence. New settings with one consistent default receive that default. Settings with conflicting defaults or no default are optional, so they do not become compulsory for users of other branches. Enable Home Assistant's **Show unused optional configuration options** switch to reveal optional fields that have no value yet.

Incompatible new field types are not imported. Existing main fields are not replaced by incompatible branch definitions. The action emits a warning and records the affected names and branches in `.github/vinyl-guardian-settings-report.json`, without writing credential/default values into that report. Settings remain available after a branch is removed so existing saved values are preserved.

Home Assistant's native configuration form does not support conditional fields based on `code_branch`. The shared settings therefore remain visible when switching versions. Options apply only where the selected runtime supports them. Legacy `mic_volume` settings are explicitly ignored by the stable startup script when selecting `main`, so returning to stable operation retains its calibrated input behaviour.

New compatible branch settings become configurable after the advertised add-on update is installed. Schema synchronisation does not install a branch's extra system or Python dependencies; branches that need different dependencies may still need a dedicated image.


### Recording folder / external storage

Set `recording_directory` in the add-on Configuration tab, then save and restart. The default is `/share/vinyl_guardian`, retaining the existing layout. For mounted media storage, an example is `/media/Recordings/vinyl_guardian`. The `Recordings` storage folder must already exist and be mounted; Guardian creates the final `vinyl_guardian` folder and checks it is writable. It refuses startup if the chosen path is unavailable instead of silently falling back to the internal disk. A successful startup logs the resolved recording folder.

The setting covers calibration WAVs, continuous audio and feature datasets, feedback/ghost clips, experiment event audio and timelines, and temporary recognition WAVs. Calibration profiles, rollback history and audio-source preferences remain under `/share/vinyl_guardian`, so changing the recording disk does not discard your active calibration. Existing recordings are not moved automatically. To keep old calibration audio/feedback available for reuse, copy `calibration_data`, datasets, experiment folders and WAV feedback into the new recording root, preserving their relative layout.

The add-on now maps `/media` read/write as well as `/share`. A folder setting does not mount a USB disk itself. Home Assistant must first expose the disk or network share in one of those mapped folders. On Home Assistant OS, network storage added with Media usage appears beneath `/media`; the supported external data disk feature instead relocates Home Assistant's whole data disk. Those are different storage arrangements. The write check detects unavailable/unwritable paths but cannot guarantee an existing empty mountpoint still has its external filesystem mounted.


## Guided calibration and audio input

Update the installed app before using these controls. With `calibration_mode: true`,
open the **Vinyl Guardian** device under the MQTT integration. Read **Calibration
Instructions**, prepare the physical step, and press **Continue Calibration**.
The wizard waits as long as necessary between stages. The complete instruction is
also in the sensor's `instruction` attribute and the app logs. Capture errors stop
calibration instead of saving an empty result. Disable calibration mode and restart
when finished. Set `advanced.reuse_calibration_audio: false` for fresh recordings;
true deliberately analyses previously recorded audio instead.

Audio directions are from the **Home Assistant host's** perspective: the turntable
output feeds the host's **Audio Input**. Guardian captures this input and never
uses **Audio Output**. On the experimental branch, `audio_source` and **Find Audio
Input** also select the capture input. A successful scan persists its choice for
future runs, changes `audio_source` to `auto` and turns `audio_scan_on_start` off.
If Supervisor cannot reset the options, the saved completion marker still prevents
repeat startup scans. To re-arm in that case, turn the option off and restart,
then turn it on and restart. Failed scans do not consume the startup request.
The manual **Find Audio Input** button remains available for an intentional scan.

The configuration form labels show ✅ for both `main` and
`vinyl-guardian-detection-v2`, 🧪 for experimental only, and ⚪ for options unused
by either active version. The native Home Assistant form keeps these fields
visible when the runtime branch changes; experimental-only values are ignored on
main. Settings and profiles are retained when switching branches.


## Calibration screen

After updating the installed app, enable `calibration_mode`, restart and select
**Open Web UI** on the app's Info page. This Home Assistant ingress screen shows
the full current instruction, the seven recording stages and live calibration
logs alongside **Continue**. It works on phones and computers without opening
a second tab. Continue is enabled only while a step is waiting; old screens and
double clicks cannot advance a later step. The existing MQTT button still works.
The screen reconnects automatically and preserves the running calibration when
you close or reopen it. After completion, disable calibration mode and restart.


### Repeat or restart calibration

Use **Step to repeat** and **Repeat selected step** to return to a current or
previous recording stage, including while recording or after a failed stage.
The current capture stops at its next audio read, then waits for preparation
and Continue again. Earlier completed recordings are kept; the selected stage
and later recordings are replaced. Repeating **Input gain** redoes all recordings.
**Restart calibration** starts fresh from Input gain without restarting the app.
These controls also work after completion. The active detector profile remains
in place until a new result is saved. Controls briefly disable during saving.
A review screen waits before analysis, so you can repeat recordings first.

Input gain now uses a bounded midpoint search across 1–100%, including when the
10-second verification detects a loud spike. It tests at most seven volume
settings instead of walking the final range one percent at a time. Silence,
clipping at minimum gain and inconsistent audio produce an actionable error
with a Repeat option. Use a steady, loud passage for this step.


The gain search treats its preferred loudness range as a target. If adjacent
gain settings straddle that range, it verifies the highest safely sampled gain
over 10 seconds and accepts a lower, unclipped level. If that verification clips,
it searches the previously safe candidates by midpoint. It still rejects absent
audio and levels above its clipping safety limit.
