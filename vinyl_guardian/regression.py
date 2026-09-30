"""Offline regression evaluation for Vinyl Guardian detector profiles."""

import glob
import json
import os
import wave

import numpy as np

from detector import GuardianDetector


DEFAULT_CHUNK = 2048


LABEL_EXPECTATIONS = {
    "actually_off": "off",
    "motor_on_needle_up": "motor",
    "playing": "music",
}


def collect_labelled_event_clips(share_dir):
    """Return event-audio WAVs with explicit ground-truth expectations."""
    root = os.path.join(share_dir, "experiments", "event_audio")
    clips = []
    for sidecar in sorted(glob.glob(os.path.join(root, "*.json"))):
        try:
            with open(sidecar, "r") as handle:
                metadata = json.load(handle)
            label = str(metadata.get("label") or "").strip().lower()
            expectation = LABEL_EXPECTATIONS.get(label)
            if not expectation:
                continue
            wav_name = metadata.get("wav")
            if wav_name:
                wav_path = os.path.join(root, os.path.basename(str(wav_name)))
            else:
                wav_path = os.path.splitext(sidecar)[0] + ".wav"
            if os.path.exists(wav_path):
                clips.append((wav_path, expectation, label))
        except Exception:
            continue
    return clips


def _read_wav(path):
    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        rate = wf.getframerate()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())

    if width != 2:
        raise ValueError(f"Unsupported WAV sample width {width} in {path}")
    return frames, rate, channels


def _run_wav(path, thresholds, expectation, chunk=DEFAULT_CHUNK):
    raw, rate, channels = _read_wav(path)
    frame_bytes = chunk * channels * 2
    detector = GuardianDetector(thresholds, rate=rate, channels=channels)

    elapsed = 0.0
    dt = chunk / float(rate)
    frames_seen = 0
    on_frames = 0
    music_frames = 0
    runout_frames = 0
    max_motor = 0.0
    max_music = 0.0
    seen_statuses = set()
    last_frame = None

    for offset in range(0, len(raw) - frame_bytes + 1, frame_bytes):
        payload = raw[offset:offset + frame_bytes]
        elapsed += dt
        last_frame = detector.update_pcm(payload, elapsed)
        frames_seen += 1
        on_frames += int(bool(last_frame.get("turntable_on")))
        music_frames += int(bool(last_frame.get("music_active")))
        runout_frames += int(bool(last_frame.get("runout_locked")))
        max_motor = max(max_motor, float(last_frame.get("motor_confidence", 0.0)))
        max_music = max(max_music, float(last_frame.get("music_confidence", 0.0)))
        seen_statuses.add(last_frame.get("status"))

    if frames_seen == 0:
        return {
            "path": path,
            "expectation": expectation,
            "frames": 0,
            "penalty": 1000.0,
            "error": "empty fixture",
        }

    on_fraction = on_frames / frames_seen
    music_fraction = music_frames / frames_seen
    runout_fraction = runout_frames / frames_seen
    final_status = last_frame.get("status") if last_frame else None

    penalty = 0.0
    if expectation == "off":
        penalty = (on_fraction * 120.0) + (music_fraction * 180.0) + (runout_fraction * 120.0)
    elif expectation == "music":
        # Manual missed-music clips contain pre-roll, so require evidence of
        # music rather than insisting that every frame be classified as music.
        if max_music < 0.68 and music_fraction < 0.05:
            penalty += 100.0
        penalty += max(0.0, 0.10 - music_fraction) * 100.0
    elif expectation == "motor":
        if max_motor < 0.72 and not detector.turntable_on:
            penalty += 100.0
        penalty += music_fraction * 80.0
    elif expectation == "transition":
        if "Playing" not in seen_statuses:
            penalty += 100.0
        if "Runout Groove" not in seen_statuses:
            penalty += 100.0
    elif expectation == "powerdown":
        if final_status != "Powered Off":
            penalty += 100.0

    return {
        "path": path,
        "expectation": expectation,
        "frames": frames_seen,
        "duration_sec": frames_seen * dt,
        "on_fraction": on_fraction,
        "music_fraction": music_fraction,
        "runout_fraction": runout_fraction,
        "max_motor_confidence": max_motor,
        "max_music_confidence": max_music,
        "final_status": final_status,
        "seen_statuses": sorted(x for x in seen_statuses if x),
        "penalty": float(penalty),
    }


def collect_fixtures(share_dir, calibration_files=None):
    fixtures = []

    def add_many(pattern, expectation, limit=30):
        paths = sorted(glob.glob(pattern))[-limit:]
        fixtures.extend((path, expectation) for path in paths)

    add_many(os.path.join(share_dir, "ghost_trigger*.wav"), "off", limit=30)
    add_many(os.path.join(share_dir, "missed_music_*.wav"), "music", limit=30)

    event_audio = os.path.join(share_dir, "experiments", "event_audio")
    add_many(os.path.join(event_audio, "*known_off_power_on*.wav"), "off", limit=30)

    # Explicit Home Assistant ground-truth marks become permanent regression
    # fixtures. Ambiguous labels such as wrong_state/ignore are retained in
    # the dataset but deliberately not assigned an expected detector state.
    for wav_path, expectation, _label in collect_labelled_event_clips(share_dir):
        fixtures.append((wav_path, expectation))

    if calibration_files:
        mapping = {
            "floor": "off",
            "disturbance": "off",
            "spinup": "motor",
            "transition": "transition",
            "powerdown": "powerdown",
        }
        for key, expectation in mapping.items():
            path = calibration_files.get(key)
            if path and os.path.exists(path):
                fixtures.append((path, expectation))

    # Remove duplicates while preserving order.
    unique = []
    seen = set()
    for item in fixtures:
        key = (os.path.abspath(item[0]), item[1])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def evaluate_profile(thresholds, share_dir, calibration_files=None):
    fixtures = collect_fixtures(share_dir, calibration_files=calibration_files)
    results = []
    for path, expectation in fixtures:
        try:
            results.append(_run_wav(path, thresholds, expectation))
        except Exception as exc:
            results.append({
                "path": path,
                "expectation": expectation,
                "frames": 0,
                "penalty": 250.0,
                "error": str(exc),
            })

    total_penalty = float(sum(item.get("penalty", 0.0) for item in results))
    severe_failures = sum(1 for item in results if item.get("penalty", 0.0) >= 100.0)
    return {
        "fixture_count": len(results),
        "total_penalty": total_penalty,
        "severe_failures": severe_failures,
        "fixtures": results,
    }


def compare_profiles(candidate, baseline, share_dir, calibration_files=None):
    candidate_result = evaluate_profile(
        candidate,
        share_dir,
        calibration_files=calibration_files,
    )
    if not isinstance(baseline, dict) or not baseline:
        return {
            "can_compare": False,
            "accepted": True,
            "reason": "No previous active profile to compare.",
            "candidate": candidate_result,
            "baseline": None,
        }

    baseline_result = evaluate_profile(
        baseline,
        share_dir,
        calibration_files=calibration_files,
    )

    fixture_count = candidate_result["fixture_count"]
    if fixture_count < 3:
        return {
            "can_compare": False,
            "accepted": True,
            "reason": "Too few regression fixtures; candidate retained and may be promoted.",
            "candidate": candidate_result,
            "baseline": baseline_result,
        }

    cand_penalty = candidate_result["total_penalty"]
    base_penalty = baseline_result["total_penalty"]
    cand_severe = candidate_result["severe_failures"]
    base_severe = baseline_result["severe_failures"]

    allowed_penalty = (base_penalty * 1.10) + 2.0
    accepted = (
        cand_penalty <= allowed_penalty
        and cand_severe <= base_severe
    )

    if accepted:
        reason = (
            f"Candidate regression penalty {cand_penalty:.2f} is within the "
            f"allowed {allowed_penalty:.2f}; severe failures "
            f"{cand_severe} vs {base_severe}."
        )
    else:
        reason = (
            f"Candidate regression penalty {cand_penalty:.2f} exceeds safe "
            f"comparison against {base_penalty:.2f}, or introduces severe "
            f"failures ({cand_severe} vs {base_severe})."
        )

    return {
        "can_compare": True,
        "accepted": bool(accepted),
        "reason": reason,
        "candidate": candidate_result,
        "baseline": baseline_result,
    }


def save_regression_report(path, result):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w") as handle:
        json.dump(result, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
