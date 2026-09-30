"""Calibration quality diagnostics for Vinyl Guardian."""

import math
import os
import wave

import numpy as np

from detector import GuardianDetector, extract_features
from telemetry import pcm16_channels, stereo_features


def _load_mono(path):
    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        if wf.getsampwidth() != 2:
            raise ValueError("Calibration quality expects 16-bit PCM")
        raw = wf.readframes(wf.getnframes())
    channel_data = pcm16_channels(raw, channels)
    mono = np.mean(channel_data, axis=1) if channel_data.size else np.zeros(0, dtype=np.float32)
    return mono, rate, channels, raw


def _feature_rows(mono, rate, chunk=2048):
    rows = []
    for offset in range(0, len(mono) - chunk + 1, chunk):
        rows.append(extract_features(mono[offset:offset + chunk], rate))
    return rows


def _array(rows, key):
    return np.asarray([float(row.get(key, 0.0)) for row in rows], dtype=float)


def _robust_distance(a, b, floor=1e-9):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if not len(a) or not len(b):
        return 0.0
    a_med = float(np.median(a))
    b_med = float(np.median(b))
    a_mad = float(np.median(np.abs(a - a_med)))
    b_mad = float(np.median(np.abs(b - b_med)))
    scale = max((1.4826 * a_mad + 1.4826 * b_mad) / 2.0, floor)
    return abs(a_med - b_med) / scale


def _db_ratio(a, b):
    return 20.0 * math.log10((float(a) + 1e-12) / (float(b) + 1e-12))


def _replay(path, thresholds, chunk=2048, initial_on=False):
    mono, rate, channels, raw = _load_mono(path)
    detector = GuardianDetector(thresholds, rate=rate, channels=channels)
    if initial_on:
        detector.turntable_on = True
        detector.motor_confidence = 1.0
    frame_bytes = chunk * channels * 2
    dt = chunk / float(rate)
    elapsed = 0.0
    on = music = runout = total = 0
    runout_samples = []

    for offset in range(0, len(raw) - frame_bytes + 1, frame_bytes):
        data = raw[offset:offset + frame_bytes]
        elapsed += dt
        frame = detector.update_pcm(data, elapsed)
        total += 1
        on += int(bool(frame.get("turntable_on")))
        music += int(bool(frame.get("music_active")))
        runout += int(bool(frame.get("runout_locked")))
        if frame.get("runout_locked"):
            runout_samples.append({
                "label": frame.get("runout_rpm"),
                "estimated_rpm": frame.get("runout_estimated_rpm"),
                "phase_jitter_ms": frame.get("runout_phase_jitter_ms"),
                "support": frame.get("runout_support"),
            })

    return {
        "frames": total,
        "on_fraction": on / total if total else 0.0,
        "music_fraction": music / total if total else 0.0,
        "runout_fraction": runout / total if total else 0.0,
        "final_status": detector.last_frame.get("status") if detector.last_frame else None,
        "runout_samples": runout_samples,
    }


def _channel_quality(path, max_chunks=100):
    _, rate, channels, raw = _load_mono(path)
    frame_bytes = 2048 * channels * 2
    rows = []
    for offset in range(0, min(len(raw), frame_bytes * max_chunks), frame_bytes):
        data = raw[offset:offset + frame_bytes]
        if len(data) < frame_bytes:
            break
        rows.append(stereo_features(pcm16_channels(data, channels)))

    if not rows:
        return {"configured_channels": channels, "mode": "unknown"}

    def med(name):
        return float(np.median([float(row.get(name, 0.0)) for row in rows]))

    corr = med("stereo_correlation")
    side = med("stereo_side_mid_ratio")
    identical = med("stereo_identical_fraction")
    left = med("left_rms")
    right = med("right_rms")

    if channels < 2:
        mode = "mono"
    elif min(left, right) < max(left, right, 1e-12) * 0.04:
        mode = "one_channel_weak"
    elif identical >= 0.98 or (corr >= 0.998 and side <= 0.01):
        mode = "probable_dual_mono"
    else:
        mode = "stereo_or_independent_channels"

    return {
        "configured_channels": channels,
        "mode": mode,
        "median_correlation": corr,
        "median_side_mid_ratio": side,
        "median_identical_fraction": identical,
        "median_left_rms": left,
        "median_right_rms": right,
    }


def assess_calibration(files, thresholds):
    floor, rate, _, _ = _load_mono(files["floor"])
    motor, motor_rate, _, _ = _load_mono(files["spinup"])
    transition, transition_rate, _, _ = _load_mono(files["transition"])

    floor_rows = _feature_rows(floor, rate)
    motor_stable = motor[int(20 * motor_rate):]
    motor_rows = _feature_rows(motor_stable, motor_rate)

    separability = {}
    for feature, floor_scale in (
        ("rms", 1e-6),
        ("hfer", 1e-4),
        ("crest", 1e-3),
    ):
        separability[feature] = _robust_distance(
            _array(floor_rows, feature),
            _array(motor_rows, feature),
            floor=floor_scale,
        )

    floor_music = _array(floor_rows, "music_rms")
    music_section = transition[
        int(25 * transition_rate):int(35 * transition_rate)
    ]
    music_rows = _feature_rows(music_section, transition_rate)
    music_values = _array(music_rows, "music_rms")
    floor_p95 = float(np.percentile(floor_music, 95)) if len(floor_music) else 0.0
    music_p10 = float(np.percentile(music_values, 10)) if len(music_values) else 0.0
    music_margin_db = _db_ratio(music_p10, floor_p95)

    baseline_replay = _replay(files["floor"], thresholds)
    shutdown_replay = _replay(files["powerdown"], thresholds, initial_on=True)
    disturbance = _replay(files["disturbance"], thresholds)
    transition_replay = _replay(files["transition"], thresholds)

    runout_samples = transition_replay.get("runout_samples", [])
    rpm_values = [
        float(x["estimated_rpm"])
        for x in runout_samples
        if x.get("estimated_rpm") is not None
    ]
    jitter_values = [
        float(x["phase_jitter_ms"])
        for x in runout_samples
        if x.get("phase_jitter_ms") is not None
    ]

    warnings = []
    critical = []

    if baseline_replay.get("on_fraction", 0.0) > 0.02:
        critical.append("Quiet baseline produces sustained false power evidence.")
    if shutdown_replay.get("final_status") != "Powered Off":
        critical.append("Power-down recording does not return the detector to Powered Off.")
    min_sep = min(separability.values()) if separability else 0.0
    if min_sep < 1.5:
        warnings.append("At least one motor/off feature overlaps heavily.")
    if music_margin_db < 6.0:
        warnings.append("Quiet calibrated music has less than 6 dB margin over the off floor.")
    if disturbance.get("on_fraction", 0.0) > 0.02:
        critical.append("Room disturbance produces sustained false power evidence.")
    if disturbance.get("music_fraction", 0.0) > 0.02:
        warnings.append("Room disturbance sometimes resembles music.")
    if transition_replay.get("music_fraction", 0.0) <= 0.05:
        critical.append("Calibration transition does not produce reliable music detection.")
    if transition_replay.get("runout_fraction", 0.0) <= 0.0:
        critical.append("Calibration transition never acquires a runout rhythm.")

    expected_rpm = thresholds.get("calibration_expected_runout_rpm")
    if expected_rpm and any(x.get("label") != expected_rpm for x in runout_samples):
        critical.append(f"Runout classification disagrees with the known {expected_rpm} RPM calibration record.")

    if critical:
        status = "weak"
    elif warnings:
        status = "warning"
    else:
        status = "strong"

    return {
        "status": status,
        "motor_off_separability_robust_z": separability,
        "music_floor_margin_db": music_margin_db,
        "disturbance_replay": {
            key: value
            for key, value in disturbance.items()
            if key != "runout_samples"
        },
        "transition_replay": {
            key: value
            for key, value in transition_replay.items()
            if key != "runout_samples"
        },
        "runout": {
            "samples": len(runout_samples),
            "estimated_rpm_median": float(np.median(rpm_values)) if rpm_values else None,
            "phase_jitter_ms_median": float(np.median(jitter_values)) if jitter_values else None,
        },
        "input_channels": _channel_quality(files["floor"]),
        "warnings": warnings,
        "critical": critical,
    }
