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

Set `code_branch` to `main` for normal operation. To test another development branch, enter that branch name and restart the Add-on, for example:

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
