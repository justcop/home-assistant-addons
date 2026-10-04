# Custom Home Assistant add-ons

- [AudioShelf](audioshelf/README.md): a mobile album collection, chronological studio-album Record Store and exact-track Spotify playback. Collection data persists in `/share/audioshelf`.
- [Work Audit](work_audit/README.md): collect and review work activity in Home Assistant.
- Vinyl Guardian: audio detection, recognition and vinyl listening diagnostics, described below.

# 🎵 Vinyl Guardian

Vinyl Guardian is a custom Home Assistant Add-on that bridges the gap between your analog record player and your digital smart home. By listening to the audio output of your turntable, Vinyl Guardian automatically detects when the needle drops, records a short snippet, identifies the song using Shazam, and publishes the track metadata natively to Home Assistant via MQTT. It even includes bulletproof, native Last.fm scrobbling that perfectly mimics digital media players.

## ✨ Features

- **Zero-Key Shazam Recognition**: Uses the shazamio library to fingerprint and identify tracks completely free, with no API keys or rate limits to worry about.
- **Reliable Last.fm Scrobbling**: Tracks longer than 30 seconds become eligible after 50% of their duration or four minutes. Delivery runs outside the audio loop, persists across restarts and retries transient Last.fm failures without losing the original play timestamp.
- **Smart Needle-Lift Detection**: Needle lifts and pauses stop physical playtime; eligible scrobbles are retained safely while incomplete plays are not submitted.
- **Resilient MQTT Auto-Discovery**: Home Assistant entities use availability/LWT, reconnect automatically after broker outages, and restore live state and subscriptions without restarting the add-on.
- **Audio Health Monitoring**: Actively monitors the audio stream and warns you in the Add-on logs if your audio is clipping or too quiet.
- **Calibration-Owned Tuning**: Input gain and detector thresholds come from measured calibration profiles rather than manual threshold overrides.

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

Track recognition is time-aware rather than a longest-sample-wins ladder. Initial identification uses overlapping **3 → 5 → 10 second** Shazam windows and never submits more than ten seconds. Matching 3s and 5s answers can confirm a normal track immediately. If those early answers are weak or contradictory, Guardian keeps the provisional identity but checks fresh **10–20s** and **20–30s** windows; those later windows verify or correct a weak identity rather than silently overwriting confirmed history. Once a track is established, its metadata duration predicts the next boundary rather than forcing one: around the expected end Guardian checks 3/5/10 seconds of post-boundary audio, and a real pause/recovery can start the same check early. If catalogue duration is unavailable, sparse non-overlapping ten-second probes require the same new identity twice consecutively before declaring a gapless successor. Same-track evidence means an internal pause or late-running track; mixed mashup-like evidence stays ambiguous. When the runout groove locks, the physical status remains **Runout Groove** and stylus wear continues to count, but **Vinyl Current Track becomes Not Playing immediately**. Eligible Last.fm scrobbles with unresolved identity are held until later evidence resolves them; confirmed scrobbles enter a persistent asynchronous delivery queue and retain their original physical start timestamp.

Automatic diagnostic capture has three modes, selectable live on the Home Assistant device using **Automatic Diagnostic Capture Mode**. The selection persists across restarts. **Normal** keeps the existing observational harness. **Known off** is an explicit statement that no record is playing and the motor is off: false power, music or runout activations automatically save a ghost report. **Listening session** covers a whole listening session, including side changes, and flags early music drops, power losses, early runout and quick recoveries. Confirmed track duration is used as a hint, with an eight-second end tolerance and a 45-second side-change grace period after an expected ending. Unknown track timings still produce review candidates. Scrobble requests are included in the timeline, not treated as proof of playback. No diagnostic mode changes production detection or scrobbling.

Choose Listening session before playing, then **Finish Listening Session Report** when done; it closes the session and returns to Normal. **Mark Intentional Flip or Pause** records an annotation covering the previous 20 seconds and next 45 seconds. Natural expected track endings do not require button presses. A listening session is not labelled music throughout: gaps, flips and runout remain ambiguous until reviewed. Automatic captures never become regression fixtures automatically. Every persistent mode and detector-selected capture is observational evidence until you review its audio in **Open Web UI → Review captured samples**. Only a human-reviewed sample is eligible as ground truth for historical regression. One-shot **Mark Ground Truth Now** remains an explicit human confirmation and is recorded as such.

Reports contain up to 20 seconds before and five seconds after the event, raw WAV, aligned frame trace, active detector thresholds and model coefficients, version, capture source, confirmed track timings and session annotations. Mode changes flush partial clips so audio from a later mode cannot contaminate an off-labelled report. Reports are under `<recording_directory>/experiments/event_audio` and `diagnostic_sessions`. Keep recording access enabled while monitoring; audio may include room sounds. **Open Web UI → Download diagnostic reports** exports the newest 20 complete clips and recent session summaries, bounded to 128 MiB before compression. Older clips remain until the existing 100-clip retention limit is reached. Archives exclude options and API credentials.


### Review-first ground truth

Guardian now separates **capture** from **truth**. Slow transitions, shadow disagreements, needle-drop candidates, uncertain states and legacy diagnostic-mode events may all save useful audio automatically, but their labels are only suggestions. The Open Web UI contains a **Review captured samples** queue with the saved audio, event type, detector state and any suggested label. Choose what was actually happening and save the review; only then can that clip enter the trusted regression library. This prevents a forgotten persistent mode such as Known off from poisoning the detector dataset when you later start playing a record.

### Transition-speed learning

The experiment harness now treats **detection latency** as a measurable failure mode rather than only checking whether the final state was correct. Every confirmed Playing, power and runout transition gets a retrospective evidence-onset estimate. The collector records confirmation latency, keeps rolling median/p90/max statistics by transition type, and automatically saves slow transitions with the same pre-roll audio and aligned per-chunk trace used by the diagnostic lab. Fast transitions are sampled occasionally as controls so future changes can be compared against both successes and failures.

A separate observational needle-drop experiment looks for short contact transients only when production is already in **Motor Idle**: motor on, music off and no runout. Candidate chunks record peak/crest and first-derivative impulse measurements at full detector chunk rate rather than the slower 0.5-second extended-feature cadence. Each candidate is then followed for up to ten seconds and resolved as music confirmed, runout, turntable stopped or no music. Music confirmations are bucketed by whether they followed within two, five or ten seconds. This lets the saved data answer whether a particular "stylus hit" signature is a reliable predictor of imminent music before it is ever allowed to influence production detection.

The rolling report is written to:

```text
/share/vinyl_guardian/experiments/transition_latency.json
/share/vinyl_guardian/experiments/transition_latency_events.jsonl
/share/vinyl_guardian/experiments/needle_drop_candidates.jsonl
```

Transition and needle-drop clips are included in **Download diagnostic reports** even when they are not part of a manually selected diagnostic session. Retrospective onset estimates and later production confirmations are analysis labels, not ground truth, and none of this instrumentation changes the live state machine.

Safety is treated as a hard constraint on latency work. The experiment harness now accumulates stable-state exposure and shadow-detector disagreement episodes in `/share/vinyl_guardian/experiments/safety_metrics.json`, including explicit false activations during Known off sessions. Offline regression comparison also counts false activation episodes on trusted off and motor-idle fixtures. A candidate profile that introduces an additional false activation is rejected even if its aggregate score or transition speed is better; latency improvements are only considered after the safety gate passes.

Recognition uses one Shazam client per concurrent request and supports installed versions without asynchronous context managers. Clients with an explicit close method are cleaned up after each request. If confirmation fails, each retry gathers a fresh full recording instead of immediately submitting the same buffer again.

**Stylus Use** is an always-on cumulative Home Assistant sensor in hours, counting captured audio time while production detection reports Playing or Runout Groove. Separate music and runout seconds are available as sensor attributes. Motor Idle, Powered Off, Between Tracks, explicit Known off observation and offline calibration/replay do not add time. It starts at zero on first installation of this feature, without estimating historic wear. The hard-coded counter is stored at `/data/stylus_usage.json` inside the app's persistent data, with `/data/stylus_usage.backup.json` as a second copy. It is independent of the configurable recording folder and Home Assistant's history. It saves every minute during use, when leaving a counted state, and on graceful shutdown. The Home Assistant sensor publishes saved totals, so a power loss cannot make its displayed cumulative value move backwards. Abrupt power loss may discard up to the last minute. Restarts and updates preserve the count; removing the app's data removes it. Damaged storage is recovered from a valid copy; if both saved copies are invalid, startup stops rather than resetting the accumulated total.

Listening sessions classify gaps lasting at least 30 seconds as expected long breaks, including pauses between record sides. Audio is captured at the initial suspected dropout so its lead-in is available if playback resumes quickly. If the gap becomes a long break, the session marks it expected and those initial clips are excluded from the suspicious-event download. Short off periods followed by resumed music remain review candidates. Periods without detected music or runout add no stylus time.
