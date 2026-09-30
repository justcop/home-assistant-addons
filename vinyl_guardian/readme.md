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


## Experimental dataset collection (v4.34 branch)

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
