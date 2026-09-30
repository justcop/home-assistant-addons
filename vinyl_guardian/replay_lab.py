"""Fast offline replay lab for Vinyl Guardian datasets and WAV files."""

import csv
import glob
import json
import os
import time
import wave

from detector import GuardianDetector
from experiment import ShadowDetectorSuite


DEFAULT_CHUNK = 2048


def _load_thresholds(path):
    with open(path, "r") as handle:
        return json.load(handle)


def _dataset_audio_paths(target):
    if os.path.isfile(target) and target.lower().endswith(".wav"):
        return [target], {}
    if not os.path.isdir(target):
        raise FileNotFoundError(target)

    metadata = {}
    metadata_path = os.path.join(target, "metadata.json")
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r") as handle:
                metadata = json.load(handle)
        except Exception:
            metadata = {}

    wavs = sorted(glob.glob(os.path.join(target, "audio_*.wav")))
    if not wavs:
        wavs = sorted(glob.glob(os.path.join(target, "*.wav")))
    if not wavs:
        raise FileNotFoundError(f"No WAV files in {target}")
    return wavs, metadata


def find_latest_raw_dataset(share_dir):
    root = os.path.join(share_dir, "datasets")
    candidates = []
    for path in glob.glob(os.path.join(root, "*")):
        if os.path.isdir(path) and glob.glob(os.path.join(path, "audio_*.wav")):
            candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def replay(target, thresholds, output_dir=None, chunk=DEFAULT_CHUNK):
    wav_paths, metadata = _dataset_audio_paths(target)
    if not wav_paths:
        raise ValueError("No audio supplied")

    with wave.open(wav_paths[0], "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
    if sampwidth != 2:
        raise ValueError("Replay lab currently expects 16-bit PCM WAV audio")

    production = GuardianDetector(thresholds, rate=rate, channels=channels)
    shadows = ShadowDetectorSuite(thresholds, rate=rate, channels=channels)

    detector_names = ["production"] + list(shadows.detectors.keys())
    state_seconds = {
        name: {
            "Powered Off": 0.0,
            "Motor Idle": 0.0,
            "Playing": 0.0,
            "Between Tracks": 0.0,
            "Runout Groove": 0.0,
        }
        for name in detector_names
    }
    transitions = []
    last_status = {name: None for name in detector_names}
    disagreement_seconds = {name: 0.0 for name in shadows.detectors}
    runout_diagnostics = []

    frame_bytes = chunk * channels * 2
    dt = chunk / float(rate)
    elapsed = 0.0
    chunks = 0

    for wav_path in wav_paths:
        with wave.open(wav_path, "rb") as wf:
            if (
                wf.getframerate() != rate
                or wf.getnchannels() != channels
                or wf.getsampwidth() != 2
            ):
                raise ValueError(f"Inconsistent WAV format: {wav_path}")

            while True:
                data = wf.readframes(chunk)
                if len(data) < frame_bytes:
                    break
                elapsed += dt
                chunks += 1

                prod = production.update_pcm(data, elapsed)
                shadow_frames = shadows.update(data, elapsed)

                all_frames = {"production": prod}
                all_frames.update(shadow_frames)

                for name, frame in all_frames.items():
                    status = frame.get("status", "Unknown")
                    if status in state_seconds[name]:
                        state_seconds[name][status] += dt
                    if last_status[name] != status:
                        transitions.append({
                            "time_sec": elapsed,
                            "detector": name,
                            "from": last_status[name],
                            "to": status,
                            "motor_confidence": frame.get("motor_confidence"),
                            "music_confidence": frame.get("music_confidence"),
                            "runout_rpm": frame.get("runout_rpm"),
                            "runout_estimated_rpm": frame.get("runout_estimated_rpm"),
                            "runout_phase_jitter_ms": frame.get("runout_phase_jitter_ms"),
                            "runout_support": frame.get("runout_support"),
                        })
                        last_status[name] = status

                prod_signature = (
                    bool(prod.get("turntable_on")),
                    bool(prod.get("music_active")),
                    bool(prod.get("runout_locked")),
                )
                for name, frame in shadow_frames.items():
                    sig = (
                        bool(frame.get("turntable_on")),
                        bool(frame.get("music_active")),
                        bool(frame.get("runout_locked")),
                    )
                    if sig != prod_signature:
                        disagreement_seconds[name] += dt

                if prod.get("runout_locked"):
                    runout_diagnostics.append({
                        "time_sec": elapsed,
                        "rpm_label": prod.get("runout_rpm"),
                        "estimated_rpm": prod.get("runout_estimated_rpm"),
                        "phase_jitter_ms": prod.get("runout_phase_jitter_ms"),
                        "support": prod.get("runout_support"),
                        "confidence": prod.get("runout_confidence"),
                        "last_interval_sec": prod.get("runout_last_interval_sec"),
                    })

    summary = {
        "source": target,
        "metadata": metadata,
        "rate_hz": rate,
        "channels": channels,
        "chunk_frames": chunk,
        "duration_sec": elapsed,
        "chunks": chunks,
        "state_seconds": state_seconds,
        "shadow_disagreement_seconds": disagreement_seconds,
        "transition_count": len(transitions),
        "runout_locked_samples": len(runout_diagnostics),
    }

    label = str(metadata.get("label", "")).lower()
    if label in {"known_off", "off", "turntable_off", "known-off"}:
        summary["known_off_false_on_sec"] = sum(
            seconds
            for state, seconds in state_seconds["production"].items()
            if state != "Powered Off"
        )
    elif label in {"album_playback", "playing", "music"}:
        summary["album_playing_sec"] = state_seconds["production"]["Playing"]
        summary["album_music_coverage"] = (
            state_seconds["production"]["Playing"] / elapsed if elapsed > 0 else 0.0
        )

    if output_dir is None:
        if os.path.isdir(target):
            output_dir = target
        else:
            output_dir = os.path.dirname(target)
    os.makedirs(output_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(output_dir, f"replay_{stamp}.json")
    csv_path = os.path.join(output_dir, f"replay_transitions_{stamp}.csv")

    with open(json_path, "w") as handle:
        json.dump({
            "summary": summary,
            "transitions": transitions,
            "runout_diagnostics": runout_diagnostics,
        }, handle, indent=2)

    fields = [
        "time_sec", "detector", "from", "to",
        "motor_confidence", "music_confidence",
        "runout_rpm", "runout_estimated_rpm",
        "runout_phase_jitter_ms", "runout_support",
    ]
    with open(csv_path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in transitions:
            writer.writerow({key: item.get(key) for key in fields})

    return {
        "summary": summary,
        "json_path": json_path,
        "transitions_csv": csv_path,
    }


def replay_latest_dataset(share_dir, active_calibration_file):
    target = find_latest_raw_dataset(share_dir)
    if target is None:
        raise FileNotFoundError("No raw dataset found")
    thresholds = _load_thresholds(active_calibration_file)
    return replay(target, thresholds)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Replay Vinyl Guardian audio rapidly")
    parser.add_argument("target", help="Dataset directory or WAV file")
    parser.add_argument(
        "--profile",
        default="/share/vinyl_guardian/auto_calibration.json",
        help="Calibration/profile JSON",
    )
    args = parser.parse_args()
    result = replay(args.target, _load_thresholds(args.profile))
    print(json.dumps(result, indent=2))
