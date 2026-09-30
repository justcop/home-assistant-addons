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

### Runtime branch selection

Set `code_branch` to `main` for normal operation. To test another development branch, choose that branch from the dropdown and restart the Add-on, for example:

```yaml
code_branch: "vinyl-guardian-detection-v2"
```

At startup Vinyl Guardian downloads the Python runtime files from that branch. If the branch is unavailable or invalid, startup stops rather than silently falling back to `main`.

This selector changes Python runtime files only. Changes to `Dockerfile`, `config.yaml`, `run.sh`, system packages, or Python dependencies still require a normal Add-on rebuild/update.


## Experimental detector lab (v4.35 branch)

The `vinyl-guardian-detection-v2` branch can collect a labelled feature dataset without allowing the experimental measurements to influence detection yet.

Useful add-on options:

```yaml
data_collection_enabled: true
data_collection_label: "known_off"
data_collection_raw_audio: false
data_collection_feature_interval: 0.25
data_collection_raw_segment_minutes: 30
```

Each run creates a new folder under:

```text
/share/vinyl_guardian/datasets/
```

It contains `metadata.json` and `features.csv`. If `data_collection_raw_audio` is enabled, it also creates segmented WAV files.

### Recommended recordings

For a long known-off baseline, use:

```yaml
data_collection_enabled: true
data_collection_label: "known_off"
data_collection_raw_audio: false
```

Feature collection can be left running for a day or two without storing continuous raw audio. While the label is `known_off`, any detector transition from OFF to ON is automatically preserved as a `ghost_trigger_auto_*.wav` clip containing roughly the preceding 20 seconds.

For an album calibration recording, use:

```yaml
data_collection_enabled: true
data_collection_label: "album_playback"
data_collection_raw_audio: true
```

Raw stereo PCM at 44.1 kHz/16-bit is large (roughly 635 MB/hour), so raw capture is intended for deliberate album/test sessions rather than multi-day background collection.

### Candidate measurements

The feature stream currently records:

- Existing Guardian RMS, HFER, crest, music evidence and detector confidence.
- Spectral centroid, bandwidth, flatness, entropy, rolloff and spectral flux.
- Nine frequency-band energy ratios from 20 Hz to 16 kHz, plus low/mid/high ratios.
- Dominant frequency and low-frequency peak/prominence.
- Waveform autocorrelation periodicity and estimated periodic frequency.
- Zero-crossing rate, DC offset, clipping, peak and within-chunk RMS dynamics.
- Per-channel RMS/peak.
- L/R correlation and level difference.
- Mid/side energy, channel-difference energy and identical-sample fraction, which will tell us whether the USB interface supplies genuine stereo, duplicated mono, one useful channel, or something else.
- Rolling 1 s, 3 s and 5 s means/standard deviations for key music-vs-noise features.
- The detector's current predicted state and recognised track alongside every row.

The intention is to collect broadly first, then analyse labelled recordings and remove measurements that do not improve discrimination.


### Experimental harness

Version 4.35 adds an observational harness around the live detector. The production detector still controls Home Assistant and scrobbling; experimental detectors run in shadow mode and cannot change live state.

By default:

```yaml
experiment_harness_enabled: true
auto_capture_interesting_events: true
```

Three shadow detectors run alongside production:

- `music_sensitive` tests more permissive music thresholds.
- `profile_heavy` gives the learned motor/noise profile more influence.
- `power_conservative` requires stronger, longer evidence before declaring power ON.

Their states are stored in datasets and exposed through the Home Assistant experiment entities. Sustained disagreement automatically captures about 20 seconds before and 5 seconds after the interesting event without affecting production.

Other automatically captured situations include prolonged uncertain motor/music confidence, a runout lock, and a power-ON decision during a `known_off` session. Event WAVs and JSON sidecars are stored under:

```text
/share/vinyl_guardian/experiments/event_audio/
```

A structured event timeline is written to `/share/vinyl_guardian/experiments/events_*.jsonl`.

### Ground-truth labelling

Home Assistant exposes **Ground Truth Label** plus **Mark Ground Truth Now**. Available labels are:

```text
actually_off
motor_on_needle_up
playing
between_tracks
runout
needle_lifted
wrong_state
ignore
```

Pressing the mark button records a labelled event with pre/post-roll audio. Explicit `actually_off`, `motor_on_needle_up`, and `playing` examples automatically become historical regression fixtures. Explicit off clips can also contribute to the next calibrated negative motor profile.

A broad `album_playback` dataset is intentionally not treated as frame-by-frame ground truth because it naturally contains track gaps, lead-out, runout and needle lifts.

### Hardware health

The experiment harness continuously checks the capture path and reports whether it looks like:

- true mono,
- one weak/missing channel,
- probable duplicated mono,
- or stereo/independent channels.

It also reports L/R correlation and imbalance, mid/side energy, identical-sample fraction, clipping, DC offset, bandwidth and a trusted known-off noise floor. The latest full report is stored at:

```text
/share/vinyl_guardian/experiments/hardware_health.json
```

### Runout diagnostics

Accepted runout candidates now retain their actual click intervals as well as:

- expected speed label (33⅓ or 45),
- estimated physical RPM,
- phase jitter in milliseconds,
- aligned-click support,
- rhythm confidence.

Each accepted candidate is written to the event timeline, making false locks and missed locks diagnosable from the individual revolutions rather than only the final state.

### Record-side sessions

Guardian now tracks a listening side from the first sustained music state until power-off or a needle lift after runout. Identified tracks, duration, runout status and RPM are retained in:

```text
/share/vinyl_guardian/experiments/side_sessions.jsonl
/share/vinyl_guardian/experiments/latest_side_session.json
```

### Replay lab

**Replay Latest Dataset** re-runs the newest raw dataset through production and every shadow detector faster than real time. It produces a JSON report plus a CSV transition timeline alongside the dataset, including state durations, shadow disagreement time and runout diagnostics.

The replay engine can also be run directly inside an appropriate Python environment:

```text
python replay_lab.py /path/to/dataset
```

### Calibration quality, profiles and rollback

Every new calibration is now a candidate profile rather than an unconditional overwrite. Calibration measures motor/off separability, quiet-music margin, room-disturbance leakage, runout acquisition/jitter and input-channel behaviour, then replays historical labelled examples against both the candidate and current profile.

Reports are written to:

```text
/share/vinyl_guardian/calibration_quality.json
/share/vinyl_guardian/regression_report.json
```

Profiles are versioned under:

```text
/share/vinyl_guardian/profiles/
```

If an existing profile is present, a critically weak or regressing calibration is saved for inspection but is **not** promoted. **Rollback Detector Profile** restores the previous accepted calibration file; restart the Add-on afterwards so the running detector reloads it.

### Trusted adaptive baselines

Guardian collects feature distributions only from explicitly trusted labels (plus an explicitly configured `known_off` session). It never teaches itself from its own state predictions. These observational baselines are stored at:

```text
/share/vinyl_guardian/experiments/trusted_baselines.json
```

They are not yet used to control production decisions; they exist so future detector changes can be based on measured evidence rather than self-reinforcing guesses.


## Audio input discovery (v4.36)

Vinyl Guardian no longer assumes that the first PulseAudio `alsa_input` is the turntable. Source ordering can change after OS/kernel updates or when USB hardware is added, so using "first source wins" is not a stable way to identify a soundcard.

Guardian now selects its input per add-on process using PulseAudio's `PULSE_SOURCE`. This means choosing a turntable input does not normally replace Home Assistant's global default microphone.

### Normal behaviour

The default setting is:

```yaml
audio_source: "auto"
```

`auto` reuses the last source Guardian successfully selected. If there is no remembered source yet, Guardian follows Home Assistant's current default rather than guessing from source order.

Other supported values are:

```yaml
audio_source: "system_default"
```

or an exact PulseAudio source name.

Home Assistant also exposes **Guardian Audio Source**, which lists the currently available capture sources and lets the source be changed without editing YAML. Changing capture hardware resets detector confidence and should be followed by a fresh calibration.

### Find the correct input automatically

If you are unsure which input is the turntable:

1. Connect the turntable/preamp to the input you want to test.
2. Start playing a record with ordinary music.
3. Press **Find Audio Input — Play Music** in Home Assistant.
4. Keep the music playing while Guardian works through the candidates.

Guardian records a short sample from every currently exposed non-monitor input and scores which source actually contains a healthy changing audio signal.

If none of the exposed inputs contains convincing audio, Guardian then inspects the PulseAudio cards for inactive profiles that provide capture. It temporarily tries those input-capable profiles, samples the newly exposed sources, and restores each original profile after testing. Duplex input/output profiles are preferred over input-only profiles where possible.

Only the winning card/profile is left active. If no convincing signal is found, the existing selection is left unchanged.

The chosen source is remembered in:

```text
/share/vinyl_guardian/audio_source.json
```

The full last scan, including every candidate and its signal score, is stored in:

```text
/share/vinyl_guardian/audio_scan_last.json
```

The scan report includes RMS, dBFS level, pre-emphasised/changing energy, activity, spectral entropy, score and confidence. This gives us useful evidence if an onboard input is visible but silent, or if a Home Assistant update changes how a card is exposed.

### Startup scan

For initial setup or when calibration mode is running, the scan can instead happen automatically as the add-on starts:

```yaml
audio_scan_on_start: true
audio_scan_seconds: 2.5
```

Start the add-on while music is already playing. The selected source is applied before calibration opens its ALSA stream.

After a successful switch between USB and onboard hardware, perform a completely fresh calibration because input gain, noise floor, channel arrangement and frequency response may all differ.


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
