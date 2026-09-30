# 🎵 Vinyl Guardian

Vinyl Guardian is a custom Home Assistant Add-on that bridges the gap between your analog record player and your digital smart home. By listening to the audio output of your turntable, Vinyl Guardian automatically detects when the needle drops, records a short snippet, identifies the song using Shazam, and publishes the track metadata natively to Home Assistant via MQTT. It even includes bulletproof, native Last.fm scrobbling that perfectly mimics digital media players.

## ✨ Features

- **Zero-Key Shazam Recognition**: Uses the shazamio library to fingerprint and identify tracks completely free, with no API keys or rate limits to worry about.
- **Native Last.fm Scrobbling**: Built-in Last.fm integration that strictly follows official scrobbling rules (waits for 50% of the track duration or 4 minutes of continuous physical playtime).
- **Smart Needle-Lift Detection**: If you lift the needle halfway through a song, the Add-on detects the silence and instantly aborts the scrobble to prevent false logs.
- **MQTT Auto-Discovery**: Automatically creates beautiful, dedicated sensors in your Home Assistant dashboard without any manual YAML configuration.
- **Audio Health Monitoring**: Actively monitors the audio stream and warns you in the Add-on logs if your audio is clipping or too quiet.
- **UI Volume Control**: Adjust your physical soundcard's input volume directly from the Home Assistant Add-on configuration screen.

## 🛠️ Prerequisites

- **Hardware**: A USB soundcard, audio capture device, or direct line-in connected to your Home Assistant host machine. You will need to route your turntable/pre-amp output into this input.
- **Software**: An active MQTT Broker (like the official Mosquitto broker Add-on) running in Home Assistant.

## 📦 Installation

1. Navigate to Settings > Add-ons > Add-on Store in Home Assistant.
2. Click the three dots (⋮) in the top right corner and select Repositories.
3. Add the URL to your custom GitHub repository.
4. Close the modal, scroll down (or refresh), and look for Vinyl Guardian.
5. Click Install.

## ⚙️ Configuration

Before starting the Add-on, configure your settings in the UI.
### Reanalysing calibration recordings

Set **Calibration record speed** to the physical speed of the recorded side (33⅓ RPM by default, or 45 RPM). Both speeds remain supported during normal listening. A calibration rhythm at the wrong speed fails the quality gate.

To analyse existing captures after an update, leave **Reuse calibration audio** on (the default), enable **Calibration mode**, and restart. A complete valid recording set is analysed automatically, without opening the UI or pressing Continue. Missing or invalid recordings start the physical recording wizard. The recordings keep their original input gain, including recordings from a rejected candidate. The detector learns separate quiet, shutdown and disturbance profiles; it must distinguish motor evidence from each off class. An existing profile is retained if the candidate fails quality or regression checks.

Use **Download calibration measurements** on the calibration screen after capture or analysis finishes. The ZIP includes per-chunk features, recording checksums, profiles and reports, without raw audio or app options. These measurements can replay the live detector exactly, so a failed calibration can be investigated without recording the same stages again.

### Full calibration replay checks

Calibration now checks nine behaviours across all six recordings with detector state carried between files. These include continuous power from music into runout, stable motor detection after settling, shutdown reaching and staying off, and disturbances staying off after shutdown. Failed sequence checks block replacement of an existing profile even if the older individual-file regression suite passes.

Profile-backed motor evidence uses a 1.5-second median and balanced confidence averaging to reject isolated off-state hum. A developing three-hit rhythm can support an already powered turntable until the full six-hit runout lock forms; it cannot turn power on or declare runout itself. Existing profiles without separate negative classes retain their previous motor evidence handling.

For offline comparison with saved recordings:

```sh
python calibration_replay.py /path/to/calibration_data candidate.json --baseline auto_calibration.json
```

The candidate may be a complete `profiles/profile_*.json` file or a thresholds dictionary. The command emits all checks, state transitions and per-stage metrics as JSON and exits unsuccessfully if any candidate check fails. No Home Assistant or audio hardware is needed for replay; NumPy is required.

### Extended calibration measurements

Every calibration, including automatic reuse, extracts 51 measurements per chunk from the saved WAVs. These cover frequency bands, spectral shape and change, dominant frequencies, within-chunk periodicity and amplitude variation, clipping and stereo relationships. The results are saved under `/share/vinyl_guardian/calibration_measurements` and included in **Download calibration measurements**.

The feature report compares motor with off, motor with runout, runout with off, and music with runout when a sufficiently long music interval exists. Each feature threshold is fitted on the first 60% of the instructed steady intervals and checked on the remaining 40%. Rankings include balanced accuracy and the worst recording, so a strong overall score cannot hide a poor off-state result. These are observational comparisons from one session, rather than proof of generalisation. They do not automatically replace live detection with a different classifier.

For ongoing collection during normal listening, enable **Collect detector dataset** (`data_collection_enabled`), leave **Experiment lab** (`experiment_harness_enabled`) on, disable **Calibration mode**, and restart. Full feature CSVs, rolling statistics and detector state are saved in `<recording_directory>/datasets/`. Raw audio collection is optional; captured calibration WAVs already allow extended offline analysis without any additional live recording.

Calibration also tests 298 fixed single-feature, pair and triple models for each state comparison. Shrunk-covariance linear models account for correlations between measurements. The first 60% of each steady block fits a model, the next 20% selects it, and the final 20% evaluates the selected model. Detailed coefficients and results are in `calibration_feature_analysis.json` under `combination_analysis`. The tested motor/off feature set, `band_60_120`, `autocorr_periodicity` and `subframe_rms_cv`, is now fitted during calibration and saved in detector version 5. It replaces the previous motor profile score only after whole-sequence quality and historical regression checks pass. Music and runout logic retain their existing rules. The other combinations remain observational experiments. Offline `feature_combinations.replay_candidate` can inject a selected motor model into the identical detector state machine and run all nine sequence checks, without modifying live detection.

Track recognition sends a tentative five-second preview while continuing to capture the full **Recording seconds** interval (ten seconds by default). The early result displays the normal track name and artwork, without qualification. A successful full-recording result replaces it if needed, even if the preview response arrives later. Only confirmed matches enter the scrobbling timer. If confirmation fails, the normal retry policy applies; a preview is not treated as confirmed. Recording intervals of five seconds or less use a single request. Preview recognition adds one API request per recognition session and is not guaranteed to find a match sooner.

Automatic diagnostic capture has three modes, selectable live on the Home Assistant device using **Automatic Diagnostic Capture Mode**. The selection persists across restarts. **Normal** keeps the existing observational harness. **Known off** is an explicit statement that no record is playing and the motor is off: false power, music or runout activations automatically save a ghost report. **Listening session** covers a whole listening session, including side changes, and flags early music drops, power losses, early runout and quick recoveries. Confirmed track duration is used as a hint, with an eight-second end tolerance and a 45-second side-change grace period after an expected ending. Unknown track timings still produce review candidates. Scrobble requests are included in the timeline, not treated as proof of playback. No diagnostic mode changes production detection or scrobbling.

Choose Listening session before playing, then **Finish Listening Session Report** when done; it closes the session and returns to Normal. **Mark Intentional Flip or Pause** records an annotation covering the previous 20 seconds and next 45 seconds. Natural expected track endings do not require button presses. A listening session is not labelled music throughout: gaps, flips and runout remain ambiguous until reviewed. Known-off reports become regression fixtures automatically.

Reports contain up to 20 seconds before and five seconds after the event, raw WAV, aligned frame trace, active detector thresholds and model coefficients, version, capture source, confirmed track timings and session annotations. Mode changes flush partial clips so audio from a later mode cannot contaminate an off-labelled report. Reports are under `<recording_directory>/experiments/event_audio` and `diagnostic_sessions`. Keep recording access enabled while monitoring; audio may include room sounds. **Open Web UI → Download diagnostic reports** exports the newest 20 complete clips and recent session summaries, bounded to 128 MiB before compression. Older clips remain until the existing 100-clip retention limit is reached. Archives exclude options and API credentials.

Recognition uses one Shazam client per concurrent request and supports installed versions without asynchronous context managers. Clients with an explicit close method are cleaned up after each request. If confirmation fails, each retry gathers a fresh full recording instead of immediately submitting the same buffer again.
