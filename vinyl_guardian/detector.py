"""
Vinyl Guardian detection core.

One detector is used for both live audio and calibration replay.  The first
operation on live PCM is always an explicit stereo -> mono downmix, so the
features measured during calibration are the same features measured in
production.
"""

import math
from collections import deque

import numpy as np


RUNOUT_SPEEDS = {
    "33⅓": 60.0 / (100.0 / 3.0),  # 1.8 s/revolution
    "45": 60.0 / 45.0,            # 1.333... s/revolution
}


def pcm16_to_mono(data, channels=2):
    """Convert interleaved signed 16-bit PCM bytes to mono float32 [-1, 1]."""
    samples = np.frombuffer(data, dtype=np.int16)
    if samples.size == 0:
        return np.zeros(0, dtype=np.float32)

    channels = max(1, int(channels or 1))
    usable = samples.size - (samples.size % channels)
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)

    samples = samples[:usable].astype(np.float32).reshape(-1, channels)
    if channels == 1:
        mono = samples[:, 0]
    else:
        mono = np.mean(samples, axis=1)
    return mono / 32768.0


def extract_features(mono, rate=44100):
    """Extract deliberately cheap, stable features from one mono chunk."""
    x = np.asarray(mono, dtype=np.float32)
    if x.size < 2:
        return {
            "rms": 0.0,
            "music_rms": 0.0,
            "hfer": 0.0,
            "crest": 1.0,
            "peak": 0.0,
            "zcr": 0.0,
        }

    rms = float(np.sqrt(np.mean(x * x)))
    peak = float(np.max(np.abs(x)))
    crest = peak / rms if rms > 1e-12 else 1.0

    # Pre-emphasis is intentionally retained from the original detector, but
    # is now applied to consecutive MONO time samples rather than L/R samples.
    pre = x[1:] - 0.95 * x[:-1]
    music_rms = float(np.sqrt(np.mean(pre * pre)))

    diff = x[1:] - x[:-1]
    hf_rms = float(np.sqrt(np.mean(diff * diff)))
    hfer = hf_rms / rms if rms > 1e-4 else 0.0

    signs = np.signbit(x)
    zcr = float(np.mean(signs[1:] != signs[:-1]))

    return {
        "rms": rms,
        "music_rms": music_rms,
        "hfer": hfer,
        "crest": crest,
        "peak": peak,
        "zcr": zcr,
    }


def _ema(current, target, dt, rise_tau, fall_tau):
    tau = rise_tau if target > current else fall_tau
    tau = max(1e-3, tau)
    alpha = 1.0 - math.exp(-max(0.0, dt) / tau)
    return current + alpha * (target - current)


def _soft_window(value, low, high):
    """0..1 membership score with soft shoulders instead of cliff edges."""
    low = float(low)
    high = float(high)
    if high < low:
        low, high = high, low

    span = max(high - low, abs(high) * 0.20, abs(low) * 0.20, 1e-6)
    shoulder = max(span * 0.45, 1e-6)

    if low <= value <= high:
        # Full credit through the calibrated region.
        return 1.0
    if value < low:
        return math.exp(-(low - value) / shoulder)
    return math.exp(-(value - high) / shoulder)


class RunoutRhythmDetector:
    """Detect one physical click per revolution at 33⅓ or 45 RPM.

    A click is only useful when it lines up in phase with multiple earlier
    clicks.  Random pops can therefore be abundant without creating a lock.
    """

    def __init__(self):
        self.events = deque(maxlen=32)
        self.last_candidate_time = -1e9
        self.locked = False
        self.rpm_label = None
        self.confidence = 0.0
        self.hold_until = -1e9
        self.last_support = 0
        self.last_interval = None
        self.phase_jitter_ms = None
        self.estimated_rpm = None
        self.last_candidate_accepted = False
        self.candidate_intervals = deque(maxlen=8)

    def reset(self):
        self.events.clear()
        self.last_candidate_time = -1e9
        self.locked = False
        self.rpm_label = None
        self.confidence = 0.0
        self.hold_until = -1e9
        self.last_support = 0
        self.last_interval = None
        self.phase_jitter_ms = None
        self.estimated_rpm = None
        self.last_candidate_accepted = False
        self.candidate_intervals.clear()

    def _find_phase_match(self, target, tolerance, peak):
        best = None
        for event_time, event_peak in self.events:
            error = abs(event_time - target)
            if error > tolerance:
                continue
            amp_ratio = max(peak, event_peak) / max(min(peak, event_peak), 1e-9)
            if amp_ratio > 3.5:
                continue
            if best is None or error < best[0]:
                best = (error, event_time, event_peak)
        return best

    def _score_period(self, now, peak, period, label):
        # Acquisition requires a genuinely consecutive rotational chain.
        # This is much harder for random dust pops or room transients to fake
        # than simply finding several historical events near integer multiples.
        tolerance = max(0.09, period * 0.07)
        support = 1
        quality = 1.0
        cursor_time = now
        cursor_peak = peak
        used_skip = False
        observed_periods = []

        for _ in range(5):
            skip_revolutions = 1
            match = self._find_phase_match(
                cursor_time - period,
                tolerance,
                cursor_peak,
            )

            # Once a rhythm is already proven, tolerate one missed revolution
            # so a weak click does not unnecessarily destroy the lock.
            if (
                match is None
                and self.locked
                and self.rpm_label == label
                and not used_skip
            ):
                match = self._find_phase_match(
                    cursor_time - (2.0 * period),
                    tolerance * 1.15,
                    cursor_peak,
                )
                if match is not None:
                    used_skip = True
                    skip_revolutions = 2

            if match is None:
                break

            error, event_time, event_peak = match
            observed_period = (cursor_time - event_time) / float(skip_revolutions)
            if observed_period > 0:
                observed_periods.append(observed_period)
            support += 1
            quality += 1.0 - min(1.0, error / tolerance)
            cursor_time = event_time
            cursor_peak = event_peak

        return support, quality / max(1, support), observed_periods

    def update(self, now, is_candidate, peak, recently_played, music_active):
        self.last_candidate_accepted = False

        # Expire ancient events. Six 33⅓ revolutions is already generous.
        while self.events and now - self.events[0][0] > 11.5:
            self.events.popleft()

        if self.locked and now > self.hold_until:
            self.locked = False
            self.rpm_label = None
            self.confidence *= 0.5
            self.last_support = 0

        # Never build a runout rhythm while ordinary music is confidently
        # present. A runout lock is a post-music phenomenon.
        accepted = (
            is_candidate
            and not music_active
            and (recently_played or self.locked)
            and (now - self.last_candidate_time) >= 0.24
        )

        if not accepted:
            self.confidence = max(0.0, self.confidence - 0.002)
            return

        best_label = None
        best_support = 0
        best_quality = 0.0
        best_period = None
        best_observed_periods = []

        for label, period in RUNOUT_SPEEDS.items():
            support, quality, observed_periods = self._score_period(now, peak, period, label)
            if (support, quality) > (best_support, best_quality):
                best_label = label
                best_support = support
                best_quality = quality
                best_period = period
                best_observed_periods = observed_periods

        self.last_candidate_accepted = True
        if self.events:
            self.last_interval = float(now - self.events[-1][0])
            self.candidate_intervals.append(self.last_interval)
        self.events.append((now, float(peak)))
        self.last_candidate_time = now
        self.last_support = best_support

        if best_observed_periods:
            period_arr = np.asarray(best_observed_periods, dtype=float)
            median_period = float(np.median(period_arr))
            self.estimated_rpm = 60.0 / median_period if median_period > 0 else None
            self.phase_jitter_ms = float(np.std(period_arr) * 1000.0)
        elif not self.locked:
            self.estimated_rpm = None
            self.phase_jitter_ms = None

        # Four consecutive revolutions are required to acquire a lock.
        # Once proven, a phase-correct hit after one missed revolution is
        # enough to maintain it and extend the hold window.
        maintaining_same_rhythm = self.locked and self.rpm_label == best_label
        required = 2 if maintaining_same_rhythm else 4
        new_conf = min(1.0, (best_support / 4.0) * (0.65 + 0.35 * best_quality))
        self.confidence = max(self.confidence * 0.82, new_conf)

        if best_support >= required and best_period is not None:
            self.locked = True
            self.rpm_label = best_label
            # Long enough to bridge to the next expected click even if one is
            # missed, but short enough to release promptly after needle lift.
            self.hold_until = now + max(3.2, best_period * 2.25)


class GuardianDetector:
    """Stateful evidence detector for power, music and runout groove."""

    def __init__(self, thresholds, rate=44100, channels=2):
        self.thresholds = dict(thresholds or {})
        self.rate = int(rate)
        self.channels = max(1, int(channels or 1))

        self.motor_confidence = 0.0
        self.music_confidence = 0.0
        self.turntable_on = False
        self.music_active = False
        self.has_played_music = False

        self.last_music_time = -1e9
        self.last_now = None
        self.on_candidate_since = None
        self.off_candidate_since = None
        self.music_candidate_since = None

        self.runout = RunoutRhythmDetector()
        self.last_features = extract_features(np.zeros(2, dtype=np.float32), self.rate)
        self.last_frame = {}

    def _threshold(self, name, default):
        try:
            return float(self.thresholds.get(name, default))
        except (TypeError, ValueError):
            return float(default)


    def _profile_distance(self, features, profile):
        if not isinstance(profile, dict):
            return None

        distances = []
        for name in ("rms", "hfer", "crest"):
            spec = profile.get(name)
            if not isinstance(spec, dict):
                continue
            try:
                centre = float(spec["median"])
                scale = max(float(spec["scale"]), 1e-8)
                z = (float(features[name]) - centre) / scale
                distances.append(min(36.0, z * z))
            except (KeyError, TypeError, ValueError):
                continue

        if not distances:
            return None
        return sum(distances) / len(distances)

    def _profile_motor_score(self, features):
        motor_d = self._profile_distance(features, self.thresholds.get("motor_profile"))
        noise_d = self._profile_distance(features, self.thresholds.get("negative_profile"))
        if motor_d is None or noise_d is None:
            return None

        # Positive when the chunk is closer to the motor distribution than to
        # the calibrated floor/disturbance/known-false-positive distribution.
        delta = max(-12.0, min(12.0, (noise_d - motor_d) / 3.0))
        return 1.0 / (1.0 + math.exp(-delta))

    def update_pcm(self, data, now, force_music_active=False):
        mono = pcm16_to_mono(data, self.channels)
        return self.update_mono(mono, now, force_music_active=force_music_active)

    def update_mono(self, mono, now, force_music_active=False):
        features = extract_features(mono, self.rate)
        self.last_features = features

        if self.last_now is None:
            dt = max(0.01, len(mono) / float(self.rate)) if len(mono) else 0.05
        else:
            dt = max(0.005, min(0.5, float(now) - float(self.last_now)))
        self.last_now = float(now)

        rms = features["rms"]
        hfer = features["hfer"]
        crest = features["crest"]
        peak = features["peak"]
        music_rms = features["music_rms"]

        r_min = self._threshold("rms_min", 0.0001)
        r_max = self._threshold("rms_max", max(r_min * 4.0, 0.01))
        h_min = self._threshold("hfer_min", 0.0)
        h_max = self._threshold("hfer_max", 2.0)
        c_min = self._threshold("crest_min", 0.0)
        c_max = self._threshold("crest_max", 20.0)
        motor_ceil = self._threshold("motor_power_ceiling", max(r_max, 0.02))

        m_trigger = self._threshold("music_threshold", 0.002)
        m_hold = self._threshold("music_hold_threshold", m_trigger * 0.6)
        pop_crest = self._threshold("runout_crest_threshold", 3.5)
        pop_amp = self._threshold("pop_amplitude_threshold", 0.002)

        since_music_before = float(now) - self.last_music_time
        recently_played = 0.0 <= since_music_before <= 45.0

        # A genuine runout click becomes easier to see immediately after a
        # track, but still needs phase-coherent repetition before locking.
        pop_amp_gate = pop_amp * (0.45 if recently_played else 0.90)
        pop_crest_gate = pop_crest * (0.88 if recently_played else 1.0)
        is_pop_candidate = (
            rms > 0.0
            and crest >= pop_crest_gate
            and peak >= pop_amp_gate
            and rms <= motor_ceil * 1.35
        )

        active_music_threshold = m_hold if (self.has_played_music and since_music_before <= 6.0) else m_trigger
        ratio = music_rms / max(active_music_threshold, 1e-9)

        # A smooth evidence score around the threshold is much less twitchy
        # than a boolean comparison on every ~46 ms chunk.
        music_evidence = max(0.0, min(1.0, (ratio - 0.72) / 0.58))
        if is_pop_candidate:
            music_evidence *= 0.10

        self.music_confidence = _ema(
            self.music_confidence,
            music_evidence,
            dt,
            rise_tau=0.22,
            fall_tau=0.80,
        )

        if force_music_active:
            self.music_confidence = max(self.music_confidence, 0.92)

        if self.music_active:
            if self.music_confidence < 0.28 and not force_music_active:
                self.music_active = False
        else:
            if self.music_confidence >= 0.68:
                self.music_active = True

        if self.music_active:
            self.last_music_time = float(now)
            self.has_played_music = True

        since_music = float(now) - self.last_music_time
        recently_played = 0.0 <= since_music <= 45.0

        self.runout.update(
            float(now),
            is_pop_candidate,
            peak,
            recently_played=recently_played,
            music_active=self.music_active,
        )

        # Motor evidence is a soft product. A brief out-of-window chunk hurts
        # confidence a little; it does not instantly veto the whole detector.
        rms_score = _soft_window(rms, r_min, r_max)
        hfer_score = _soft_window(hfer, h_min, h_max)
        crest_score = _soft_window(crest, c_min, c_max)
        window_evidence = (
            (rms_score ** 0.58)
            * (hfer_score ** 0.24)
            * (crest_score ** 0.18)
        )

        profile_score = self._profile_motor_score(features)
        if profile_score is None:
            motor_evidence = window_evidence
        else:
            # Keep the broad calibrated windows dominant by default for
            # tolerance to day-to-day drift. The weight is configurable so
            # shadow detectors can test alternatives without changing live
            # production behaviour.
            profile_weight = max(
                0.0,
                min(1.0, self._threshold("motor_profile_weight", 0.28)),
            )
            motor_evidence = (
                ((1.0 - profile_weight) * window_evidence)
                + (profile_weight * profile_score)
            )

        # Known needle-down states are supporting evidence, not an instant
        # "power = on" bypass.
        if self.music_active:
            motor_evidence = max(motor_evidence, 0.82)
        if self.runout.locked:
            motor_evidence = max(motor_evidence, 0.88)

        self.motor_confidence = _ema(
            self.motor_confidence,
            motor_evidence,
            dt,
            rise_tau=0.85,
            fall_tau=3.5,
        )

        motor_on_confidence = self._threshold("motor_on_confidence", 0.72)
        music_power_assist_confidence = self._threshold(
            "music_power_assist_confidence", 0.86
        )
        motor_on_seconds = self._threshold("motor_on_seconds", 1.35)
        music_power_on_seconds = self._threshold("music_power_on_seconds", 0.70)
        motor_off_confidence = self._threshold("motor_off_confidence", 0.22)
        motor_off_seconds = self._threshold("motor_off_seconds", 5.0)

        strong_on = (
            self.motor_confidence >= motor_on_confidence
            or self.music_confidence >= music_power_assist_confidence
        )
        if not self.turntable_on:
            if strong_on:
                if self.on_candidate_since is None:
                    self.on_candidate_since = float(now)
                required_on = (
                    music_power_on_seconds
                    if self.music_confidence >= music_power_assist_confidence
                    else motor_on_seconds
                )
                if float(now) - self.on_candidate_since >= required_on:
                    self.turntable_on = True
                    self.off_candidate_since = None
            else:
                self.on_candidate_since = None
        else:
            strong_off = (
                self.motor_confidence <= motor_off_confidence
                and not self.music_active
                and not self.runout.locked
            )
            if strong_off:
                if self.off_candidate_since is None:
                    self.off_candidate_since = float(now)
                if float(now) - self.off_candidate_since >= motor_off_seconds:
                    self.turntable_on = False
                    self.has_played_music = False
                    self.on_candidate_since = None
                    self.runout.reset()
            else:
                self.off_candidate_since = None

        if self.has_played_music and since_music > 18.0 and not self.runout.locked:
            self.has_played_music = False

        if not self.turntable_on:
            status = "Powered Off"
        elif self.runout.locked:
            status = "Runout Groove"
        elif self.music_active:
            status = "Playing"
        elif self.has_played_music and since_music <= 5.0:
            status = "Between Tracks"
        else:
            status = "Motor Idle"

        frame = {
            **features,
            "is_pop_candidate": bool(is_pop_candidate),
            "motor_evidence": float(motor_evidence),
            "motor_confidence": float(self.motor_confidence),
            "music_evidence": float(music_evidence),
            "music_confidence": float(self.music_confidence),
            "turntable_on": bool(self.turntable_on),
            "music_active": bool(self.music_active),
            "has_played_music": bool(self.has_played_music),
            "seconds_since_music": max(0.0, since_music) if self.last_music_time > -1e8 else 1e9,
            "runout_locked": bool(self.runout.locked),
            "runout_rpm": self.runout.rpm_label,
            "runout_confidence": float(self.runout.confidence),
            "runout_support": int(self.runout.last_support),
            "runout_last_interval_sec": self.runout.last_interval,
            "runout_candidate_accepted": bool(self.runout.last_candidate_accepted),
            "runout_candidate_intervals_sec": list(self.runout.candidate_intervals),
            "runout_estimated_rpm": self.runout.estimated_rpm,
            "runout_phase_jitter_ms": self.runout.phase_jitter_ms,
            "status": status,
        }
        self.last_frame = frame
        return frame
