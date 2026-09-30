"""Feature and dataset collection for Vinyl Guardian.

FeatureExtractor is shared by observational datasets and the promoted three-feature
motor model. Dataset collection itself does not alter detector settings.
"""

import csv
import json
import math
import os
import time
import wave
from collections import deque

import numpy as np


BANDS = (
    ("band_20_60", 20.0, 60.0),
    ("band_60_120", 60.0, 120.0),
    ("band_120_250", 120.0, 250.0),
    ("band_250_500", 250.0, 500.0),
    ("band_500_1k", 500.0, 1000.0),
    ("band_1k_2k", 1000.0, 2000.0),
    ("band_2k_4k", 2000.0, 4000.0),
    ("band_4k_8k", 4000.0, 8000.0),
    ("band_8k_16k", 8000.0, 16000.0),
)

BASE_FIELDS = (
    "rms", "peak", "crest", "zcr", "dc_offset",
    "spectral_centroid_hz", "spectral_bandwidth_hz",
    "spectral_flatness", "spectral_entropy",
    "rolloff_85_hz", "rolloff_95_hz",
    "spectral_flux", "dominant_frequency_hz",
    "dominant_frequency_share", "low_frequency_peak_hz",
    "low_frequency_peak_share",
    "autocorr_periodicity", "autocorr_frequency_hz",
    "subframe_rms_mean", "subframe_rms_std", "subframe_rms_cv",
    "subframe_rms_range", "clipping_fraction",
    "band_low_ratio", "band_mid_ratio", "band_high_ratio",
    "high_low_ratio_db", "mid_low_ratio_db",
) + tuple(name for name, _, _ in BANDS)

ROLLING_FIELDS = (
    "rms", "spectral_centroid_hz", "spectral_flux",
    "spectral_flatness", "band_low_ratio", "band_mid_ratio",
    "band_high_ratio", "stereo_side_mid_ratio",
)


def _safe_float(value, default=0.0):
    try:
        value = float(value)
        if math.isfinite(value):
            return value
    except (TypeError, ValueError):
        pass
    return float(default)


def pcm16_channels(data, channels):
    samples = np.frombuffer(data, dtype=np.int16)
    channels = max(1, int(channels or 1))
    usable = samples.size - (samples.size % channels)
    if usable <= 0:
        return np.zeros((0, channels), dtype=np.float32)
    return samples[:usable].astype(np.float32).reshape(-1, channels) / 32768.0


def _rms(x):
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(x * x)))


def _ratio_db(a, b):
    return float(20.0 * np.log10((float(a) + 1e-12) / (float(b) + 1e-12)))


class FeatureExtractor:
    """Stateful spectral extractor; state is used only for spectral flux."""

    def __init__(self, rate):
        self.rate = int(rate)
        self.previous_spectrum = None

    def extract(self, mono):
        x = np.asarray(mono, dtype=np.float32)
        if x.size < 8:
            return {field: 0.0 for field in BASE_FIELDS}

        rms = _rms(x)
        peak = float(np.max(np.abs(x)))
        crest = peak / max(rms, 1e-12)
        dc_offset = float(np.mean(x))
        zcr = float(np.mean(np.signbit(x[1:]) != np.signbit(x[:-1])))

        window = np.hanning(x.size).astype(np.float32)
        spectrum = np.abs(np.fft.rfft((x - dc_offset) * window)).astype(np.float64)
        power = spectrum * spectrum
        freqs = np.fft.rfftfreq(x.size, d=1.0 / self.rate)

        # DC is deliberately excluded from spectral descriptors.
        if power.size:
            power[0] = 0.0
        total_power = float(np.sum(power)) + 1e-24
        prob = power / total_power

        centroid = float(np.sum(freqs * prob))
        bandwidth = float(np.sqrt(np.sum(((freqs - centroid) ** 2) * prob)))

        positive_power = power[1:] + 1e-24
        flatness = float(
            np.exp(np.mean(np.log(positive_power))) /
            max(np.mean(positive_power), 1e-24)
        )

        nz = prob[prob > 0]
        entropy = float(-np.sum(nz * np.log2(nz)) / max(np.log2(len(prob)), 1.0))

        cumulative = np.cumsum(power)
        def rolloff(fraction):
            if cumulative.size == 0 or cumulative[-1] <= 0:
                return 0.0
            idx = int(np.searchsorted(cumulative, cumulative[-1] * fraction))
            idx = min(idx, len(freqs) - 1)
            return float(freqs[idx])

        norm_spectrum = spectrum / max(float(np.linalg.norm(spectrum)), 1e-24)
        if self.previous_spectrum is None or self.previous_spectrum.shape != norm_spectrum.shape:
            flux = 0.0
        else:
            delta = norm_spectrum - self.previous_spectrum
            flux = float(np.sqrt(np.mean(np.square(np.maximum(delta, 0.0)))))
        self.previous_spectrum = norm_spectrum

        dominant_idx = int(np.argmax(power)) if power.size else 0
        dominant_frequency = float(freqs[dominant_idx]) if freqs.size else 0.0
        dominant_share = float(power[dominant_idx] / total_power) if power.size else 0.0

        # FFT autocorrelation gives us a cheap periodicity candidate without
        # assuming that a dominant spectral peak necessarily means a periodic
        # waveform. Search roughly 40-1000 Hz; slower rotational phenomena are
        # better analysed later from the multi-second feature stream.
        centred = x - dc_offset
        fft_size = 1 << int(math.ceil(math.log2(max(16, centred.size * 2))))
        ac = np.fft.irfft(
            np.abs(np.fft.rfft(centred, n=fft_size)) ** 2,
            n=fft_size,
        )[:centred.size]
        if ac.size and ac[0] > 1e-20:
            ac = ac / ac[0]
            min_lag = max(1, int(self.rate / 1000.0))
            max_lag = min(ac.size - 1, int(self.rate / 40.0))
            if max_lag > min_lag:
                lag_slice = ac[min_lag:max_lag + 1]
                best_rel = int(np.argmax(lag_slice))
                best_lag = min_lag + best_rel
                autocorr_periodicity = float(max(0.0, lag_slice[best_rel]))
                autocorr_frequency = float(self.rate / best_lag)
            else:
                autocorr_periodicity = 0.0
                autocorr_frequency = 0.0
        else:
            autocorr_periodicity = 0.0
            autocorr_frequency = 0.0

        subframes = np.array_split(x, min(8, max(1, x.size // 64)))
        sub_rms = np.asarray([_rms(part) for part in subframes if part.size], dtype=np.float64)
        if sub_rms.size:
            sub_mean = float(np.mean(sub_rms))
            sub_std = float(np.std(sub_rms))
            sub_cv = float(sub_std / max(sub_mean, 1e-12))
            sub_range = float(np.max(sub_rms) - np.min(sub_rms))
        else:
            sub_mean = sub_std = sub_cv = sub_range = 0.0

        clipping_fraction = float(np.mean(np.abs(x) >= 0.999))

        low_mask = (freqs >= 20.0) & (freqs <= 250.0)
        if np.any(low_mask):
            low_indices = np.flatnonzero(low_mask)
            local_idx = int(np.argmax(power[low_mask]))
            low_idx = int(low_indices[local_idx])
            low_peak_hz = float(freqs[low_idx])
            low_peak_share = float(power[low_idx] / max(float(np.sum(power[low_mask])), 1e-24))
        else:
            low_peak_hz = 0.0
            low_peak_share = 0.0

        band_values = {}
        for name, lo, hi in BANDS:
            mask = (freqs >= lo) & (freqs < hi)
            band_values[name] = float(np.sum(power[mask]) / total_power) if np.any(mask) else 0.0

        low = sum(band_values[k] for k in ("band_20_60", "band_60_120", "band_120_250"))
        mid = sum(band_values[k] for k in ("band_250_500", "band_500_1k", "band_1k_2k"))
        high = sum(band_values[k] for k in ("band_2k_4k", "band_4k_8k", "band_8k_16k"))

        result = {
            "rms": rms,
            "peak": peak,
            "crest": crest,
            "zcr": zcr,
            "dc_offset": dc_offset,
            "spectral_centroid_hz": centroid,
            "spectral_bandwidth_hz": bandwidth,
            "spectral_flatness": flatness,
            "spectral_entropy": entropy,
            "rolloff_85_hz": rolloff(0.85),
            "rolloff_95_hz": rolloff(0.95),
            "spectral_flux": flux,
            "dominant_frequency_hz": dominant_frequency,
            "dominant_frequency_share": dominant_share,
            "low_frequency_peak_hz": low_peak_hz,
            "low_frequency_peak_share": low_peak_share,
            "autocorr_periodicity": autocorr_periodicity,
            "autocorr_frequency_hz": autocorr_frequency,
            "subframe_rms_mean": sub_mean,
            "subframe_rms_std": sub_std,
            "subframe_rms_cv": sub_cv,
            "subframe_rms_range": sub_range,
            "clipping_fraction": clipping_fraction,
            "band_low_ratio": low,
            "band_mid_ratio": mid,
            "band_high_ratio": high,
            "high_low_ratio_db": _ratio_db(high, low),
            "mid_low_ratio_db": _ratio_db(mid, low),
        }
        result.update(band_values)
        return result


def stereo_features(channel_data):
    result = {
        "input_channels": int(channel_data.shape[1]) if channel_data.ndim == 2 else 0,
        "left_rms": 0.0,
        "right_rms": 0.0,
        "left_peak": 0.0,
        "right_peak": 0.0,
        "left_right_rms_db": 0.0,
        "stereo_correlation": 0.0,
        "stereo_difference_rms": 0.0,
        "stereo_mid_rms": 0.0,
        "stereo_side_rms": 0.0,
        "stereo_side_mid_ratio": 0.0,
        "stereo_identical_fraction": 0.0,
    }

    if channel_data.ndim != 2 or channel_data.shape[0] == 0:
        return result

    left = channel_data[:, 0]
    result["left_rms"] = _rms(left)
    result["left_peak"] = float(np.max(np.abs(left)))

    if channel_data.shape[1] < 2:
        return result

    right = channel_data[:, 1]
    l_rms = result["left_rms"]
    r_rms = _rms(right)
    result["right_rms"] = r_rms
    result["right_peak"] = float(np.max(np.abs(right)))
    result["left_right_rms_db"] = _ratio_db(l_rms, r_rms)

    l_std = float(np.std(left))
    r_std = float(np.std(right))
    if l_std > 1e-9 and r_std > 1e-9:
        result["stereo_correlation"] = float(np.corrcoef(left, right)[0, 1])

    difference = left - right
    mid = (left + right) * 0.5
    side = difference * 0.5
    mid_rms = _rms(mid)
    side_rms = _rms(side)

    result["stereo_difference_rms"] = _rms(difference)
    result["stereo_mid_rms"] = mid_rms
    result["stereo_side_rms"] = side_rms
    result["stereo_side_mid_ratio"] = float(side_rms / max(mid_rms, 1e-12))
    result["stereo_identical_fraction"] = float(np.mean(np.abs(difference) <= (1.0 / 32768.0)))
    return result


class DatasetCollector:
    """Write labelled feature streams and optional segmented raw WAV audio."""

    def __init__(
        self,
        share_dir,
        rate,
        channels,
        chunk,
        enabled=False,
        label="unlabelled",
        raw_audio=False,
        feature_interval_sec=0.25,
        raw_segment_minutes=30,
    ):
        self.enabled = bool(enabled)
        self.share_dir = share_dir
        self.rate = int(rate)
        self.channels = max(1, int(channels or 1))
        self.chunk = int(chunk)
        self.label = str(label or "unlabelled").strip() or "unlabelled"
        self.raw_audio = bool(raw_audio)
        self.feature_interval_sec = max(0.05, float(feature_interval_sec))
        self.raw_segment_sec = max(60.0, float(raw_segment_minutes) * 60.0)

        self.session_dir = None
        self.csv_file = None
        self.writer = None
        self.wav_file = None
        self.wav_segment_index = 0
        self.wav_segment_started = None
        self.session_started = None
        self.last_feature_time = None
        self.extractor = FeatureExtractor(self.rate)
        self.history = deque()
        self.rows_written = 0

        if self.enabled:
            self._open_session()

    def _open_session(self):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.label)[:60]
        self.session_dir = os.path.join(
            self.share_dir,
            "datasets",
            f"{stamp}_{safe_label}",
        )
        os.makedirs(self.session_dir, exist_ok=True)
        self.session_started = time.time()

        metadata = {
            "format_version": 1,
            "created_unix": self.session_started,
            "label": self.label,
            "rate_hz": self.rate,
            "configured_channels": self.channels,
            "chunk_frames": self.chunk,
            "feature_interval_sec": self.feature_interval_sec,
            "raw_audio_enabled": self.raw_audio,
            "raw_segment_minutes": self.raw_segment_sec / 60.0,
            "note": (
                "Observational dataset only. Detector outputs are recorded as "
                "columns but candidate features do not influence decisions."
            ),
        }
        with open(os.path.join(self.session_dir, "metadata.json"), "w") as f:
            json.dump(metadata, f, indent=2)

        csv_path = os.path.join(self.session_dir, "features.csv")
        self.csv_file = open(csv_path, "w", newline="", buffering=1)

        fields = [
            "unix_time", "elapsed_sec", "label", "engine_state",
            "detector_status", "track_title", "track_artist",
            "detector_turntable_on", "detector_music_active",
            "detector_motor_confidence", "detector_music_confidence",
            "detector_runout_locked", "detector_runout_rpm",
            "detector_runout_confidence", "detector_runout_support",
            "detector_is_pop_candidate", "detector_hfer", "detector_music_rms",
        ]
        fields += list(BASE_FIELDS)
        fields += [
            "input_channels", "left_rms", "right_rms",
            "left_peak", "right_peak", "left_right_rms_db",
            "stereo_correlation", "stereo_difference_rms",
            "stereo_mid_rms", "stereo_side_rms",
            "stereo_side_mid_ratio", "stereo_identical_fraction",
            "trusted_label", "hardware_channel_mode",
            "audio_source", "audio_source_description",
            "audio_card", "audio_profile",
        ]
        for shadow_name in (
            "music_sensitive",
            "profile_heavy",
            "power_conservative",
        ):
            fields += [
                f"shadow_{shadow_name}_status",
                f"shadow_{shadow_name}_turntable_on",
                f"shadow_{shadow_name}_music_active",
                f"shadow_{shadow_name}_runout_locked",
                f"shadow_{shadow_name}_motor_confidence",
                f"shadow_{shadow_name}_music_confidence",
            ]
        for window_name in ("1s", "3s", "5s"):
            for name in ROLLING_FIELDS:
                fields.append(f"{name}_mean_{window_name}")
                fields.append(f"{name}_std_{window_name}")

        self.writer = csv.DictWriter(self.csv_file, fieldnames=fields)
        self.writer.writeheader()

        if self.raw_audio:
            self._open_wav()

    def _open_wav(self):
        if self.wav_file is not None:
            self.wav_file.close()
        self.wav_segment_index += 1
        path = os.path.join(
            self.session_dir,
            f"audio_{self.wav_segment_index:03d}.wav",
        )
        self.wav_file = wave.open(path, "wb")
        self.wav_file.setnchannels(self.channels)
        self.wav_file.setsampwidth(2)
        self.wav_file.setframerate(self.rate)
        self.wav_segment_started = time.time()

    def _rolling_stats(self, now, current_features):
        self.history.append((now, dict(current_features)))
        while self.history and now - self.history[0][0] > 5.25:
            self.history.popleft()

        output = {}
        for seconds, suffix in ((1.0, "1s"), (3.0, "3s"), (5.0, "5s")):
            subset = [f for ts, f in self.history if now - ts <= seconds]
            for name in ROLLING_FIELDS:
                values = np.asarray(
                    [_safe_float(item.get(name, 0.0)) for item in subset],
                    dtype=np.float64,
                )
                if values.size:
                    output[f"{name}_mean_{suffix}"] = float(np.mean(values))
                    output[f"{name}_std_{suffix}"] = float(np.std(values))
                else:
                    output[f"{name}_mean_{suffix}"] = 0.0
                    output[f"{name}_std_{suffix}"] = 0.0
        return output

    def observe(
        self,
        data,
        now,
        detector_frame,
        engine_state,
        current_track=None,
        experiment_snapshot=None,
    ):
        if not self.enabled:
            return

        if self.raw_audio:
            if (
                self.wav_file is None
                or now - self.wav_segment_started >= self.raw_segment_sec
            ):
                self._open_wav()
            self.wav_file.writeframesraw(data)

        if (
            self.last_feature_time is not None
            and now - self.last_feature_time < self.feature_interval_sec
        ):
            return
        self.last_feature_time = now

        channels = pcm16_channels(data, self.channels)
        if channels.shape[0] == 0:
            return

        mono = np.mean(channels, axis=1)
        spectral = self.extractor.extract(mono)
        stereo = stereo_features(channels)
        combined = dict(spectral)
        combined.update(stereo)
        rolling = self._rolling_stats(now, combined)

        track = current_track if isinstance(current_track, dict) else {}
        frame = detector_frame or {}

        row = {
            "unix_time": float(now),
            "elapsed_sec": float(now - self.session_started),
            "label": self.label,
            "engine_state": str(engine_state or ""),
            "detector_status": str(frame.get("status", "")),
            "track_title": str(track.get("title", "")),
            "track_artist": str(track.get("artist", "")),
            "detector_turntable_on": int(bool(frame.get("turntable_on", False))),
            "detector_music_active": int(bool(frame.get("music_active", False))),
            "detector_motor_confidence": _safe_float(frame.get("motor_confidence")),
            "detector_music_confidence": _safe_float(frame.get("music_confidence")),
            "detector_runout_locked": int(bool(frame.get("runout_locked", False))),
            "detector_runout_rpm": str(frame.get("runout_rpm") or ""),
            "detector_runout_confidence": _safe_float(frame.get("runout_confidence")),
            "detector_runout_support": int(frame.get("runout_support", 0) or 0),
            "detector_is_pop_candidate": int(bool(frame.get("is_pop_candidate", False))),
            "detector_hfer": _safe_float(frame.get("hfer")),
            "detector_music_rms": _safe_float(frame.get("music_rms")),
        }
        row.update(combined)
        row.update(rolling)

        experiment_snapshot = experiment_snapshot or {}
        row["trusted_label"] = str(experiment_snapshot.get("trusted_label") or "")
        hardware = experiment_snapshot.get("hardware") or {}
        row["hardware_channel_mode"] = str(hardware.get("channel_mode") or "")
        row["audio_source"] = str(experiment_snapshot.get("audio_source") or "")
        row["audio_source_description"] = str(
            experiment_snapshot.get("audio_source_description") or ""
        )
        row["audio_card"] = str(experiment_snapshot.get("audio_card") or "")
        row["audio_profile"] = str(experiment_snapshot.get("audio_profile") or "")
        shadows = experiment_snapshot.get("shadows") or {}
        for shadow_name in (
            "music_sensitive",
            "profile_heavy",
            "power_conservative",
        ):
            shadow = shadows.get(shadow_name) or {}
            prefix = f"shadow_{shadow_name}"
            row[f"{prefix}_status"] = str(shadow.get("status") or "")
            row[f"{prefix}_turntable_on"] = int(bool(shadow.get("turntable_on", False)))
            row[f"{prefix}_music_active"] = int(bool(shadow.get("music_active", False)))
            row[f"{prefix}_runout_locked"] = int(bool(shadow.get("runout_locked", False)))
            row[f"{prefix}_motor_confidence"] = _safe_float(shadow.get("motor_confidence"))
            row[f"{prefix}_music_confidence"] = _safe_float(shadow.get("music_confidence"))

        self.writer.writerow(row)
        self.rows_written += 1

    def close(self):
        if self.wav_file is not None:
            try:
                self.wav_file.close()
            except Exception:
                pass
            self.wav_file = None
        if self.csv_file is not None:
            try:
                self.csv_file.flush()
                self.csv_file.close()
            except Exception:
                pass
            self.csv_file = None
