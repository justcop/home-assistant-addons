"""Transition-latency instrumentation for Vinyl Guardian.

This module is observational only. It measures how long production takes to
confirm state changes, estimates the likely physical/evidence onset from recent
frames, and captures slow transitions for offline improvement. Nothing here may
change the live detector state.
"""

import json
import math
import os
import statistics
from collections import deque

import numpy as np

from telemetry import pcm16_channels


TARGET_LATENCY_SECONDS = {
    "playing_on": 1.0,
    "playing_off": 1.5,
    "power_on": 1.5,
    "power_off": 2.0,
    "runout_on": 3.5,
    "runout_off": 2.0,
}

HISTORY_SECONDS = 20.0
SUMMARY_EVENTS = 300


def _atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _safe(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except (TypeError, ValueError):
        return float(default)


def _percentile(values, fraction):
    values = sorted(float(value) for value in values)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    position = max(0.0, min(1.0, float(fraction))) * (len(values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def fast_transient_features(data, channels=2):
    """Cheap per-chunk measurements intended to catch a stylus-contact impulse."""
    channel_data = pcm16_channels(data, channels)
    if channel_data.shape[0] == 0:
        return {
            "transient_rms": 0.0,
            "transient_peak": 0.0,
            "transient_crest": 1.0,
            "derivative_rms": 0.0,
            "derivative_peak": 0.0,
            "derivative_crest": 1.0,
            "impulsive_sample_fraction": 0.0,
        }

    mono = np.mean(channel_data, axis=1)
    rms = float(np.sqrt(np.mean(mono * mono)))
    peak = float(np.max(np.abs(mono)))
    crest = peak / max(rms, 1e-12)

    if mono.size >= 2:
        derivative = np.diff(mono)
        derivative_rms = float(np.sqrt(np.mean(derivative * derivative)))
        derivative_peak = float(np.max(np.abs(derivative)))
        derivative_crest = derivative_peak / max(derivative_rms, 1e-12)
    else:
        derivative_rms = derivative_peak = 0.0
        derivative_crest = 1.0

    impulse_gate = max(rms * 5.0, 4.0 / 32768.0)
    impulsive_fraction = float(np.mean(np.abs(mono) >= impulse_gate))

    return {
        "transient_rms": rms,
        "transient_peak": peak,
        "transient_crest": crest,
        "derivative_rms": derivative_rms,
        "derivative_peak": derivative_peak,
        "derivative_crest": derivative_crest,
        "impulsive_sample_fraction": impulsive_fraction,
    }


class TransitionLatencyCollector:
    """Measure and preserve detector transition latency without influencing it."""

    def __init__(self, share_dir, capture, timeline, rate, channels, chunk):
        self.root = os.path.join(share_dir, "experiments")
        os.makedirs(self.root, exist_ok=True)
        self.events_path = os.path.join(self.root, "transition_latency_events.jsonl")
        self.contact_path = os.path.join(self.root, "needle_drop_candidates.jsonl")
        self.summary_path = os.path.join(self.root, "transition_latency.json")
        self.capture = capture
        self.timeline = timeline
        self.rate = int(rate)
        self.channels = max(1, int(channels or 1))
        self.chunk = int(chunk)
        self.history = deque()
        self.previous_frame = None
        self.recent_events = deque(maxlen=SUMMARY_EVENTS)
        self.contact_outcomes = deque(maxlen=SUMMARY_EVENTS)
        self.pending_contacts = deque()
        self.contact_sequence = 0
        self.last_contact_candidate = -1e12
        self.control_last = {}
        self._load_recent()

    def _load_recent(self):
        def tail(path, limit):
            try:
                with open(path, "r") as handle:
                    lines = deque(handle, maxlen=limit)
                return [json.loads(line) for line in lines if line.strip()]
            except (OSError, ValueError, TypeError):
                return []

        for item in tail(self.events_path, SUMMARY_EVENTS):
            if item.get("transition_type") in TARGET_LATENCY_SECONDS:
                self.recent_events.append(item)
        for item in tail(self.contact_path, SUMMARY_EVENTS * 2):
            if item.get("event") == "needle_drop_candidate_resolved":
                self.contact_outcomes.append(item)

    def measure(self, data, now):
        measured = fast_transient_features(data, self.channels)
        recent = [
            row["transient"]
            for row in self.history
            if float(now) - row["unix_time"] <= 4.0
        ]
        peak_baseline = statistics.median(
            [row.get("transient_peak", 0.0) for row in recent]
        ) if recent else 0.0
        derivative_baseline = statistics.median(
            [row.get("derivative_peak", 0.0) for row in recent]
        ) if recent else 0.0

        peak_ratio = measured["transient_peak"] / max(peak_baseline, 2.0 / 32768.0)
        derivative_ratio = measured["derivative_peak"] / max(
            derivative_baseline, 2.0 / 32768.0
        )
        shape = max(
            measured["transient_crest"] / 4.0,
            measured["derivative_crest"] / 5.0,
        )
        contact_score = min(20.0, max(peak_ratio, derivative_ratio) * min(2.0, shape))
        contact_candidate = bool(
            measured["transient_crest"] >= 3.5
            and measured["derivative_crest"] >= 4.0
            and derivative_ratio >= 4.0
            and peak_ratio >= 2.0
        )
        measured.update({
            "peak_vs_recent_median": float(peak_ratio),
            "derivative_vs_recent_median": float(derivative_ratio),
            "stylus_contact_score": float(contact_score),
            "stylus_contact_candidate": contact_candidate,
        })
        return measured

    def observe(self, frame, now, transient, diagnostic_mode="normal"):
        now = float(now)
        observation = {
            "unix_time": now,
            "frame": dict(frame),
            "transient": dict(transient or {}),
        }
        self.history.append(observation)
        while self.history and now - self.history[0]["unix_time"] > HISTORY_SECONDS:
            self.history.popleft()

        previous = self.previous_frame
        self.previous_frame = dict(frame)
        if previous is None:
            return []

        self._observe_needle_drop_candidates(previous, frame, now, transient or {})

        transition_types = self._transition_types(previous, frame)
        records = []
        for transition_type in transition_types:
            record = self._record_transition(
                transition_type,
                previous,
                frame,
                now,
                diagnostic_mode,
            )
            records.append(record)
        return records

    def _write_contact(self, payload):
        with open(self.contact_path, "a") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        self.timeline.record(payload["event"], now=payload["unix_time"], **{
            key: value for key, value in payload.items()
            if key not in {"event", "unix_time"}
        })

    def _resolve_contact(self, candidate, now, outcome, frame):
        resolved = dict(candidate)
        resolved.update({
            "event": "needle_drop_candidate_resolved",
            "unix_time": float(now),
            "resolved_unix": float(now),
            "outcome": outcome,
            "seconds_to_resolution": max(0.0, float(now) - candidate["candidate_unix"]),
            "confirmed_playing": bool(frame.get("music_active")),
            "resolved_status": frame.get("status"),
        })
        if outcome == "music_confirmed":
            seconds = resolved["seconds_to_resolution"]
            if seconds <= 2.0:
                resolved["timing_bucket"] = "within_2s"
            elif seconds <= 5.0:
                resolved["timing_bucket"] = "within_5s"
            else:
                resolved["timing_bucket"] = "within_10s"
        else:
            resolved["timing_bucket"] = "no_music_within_10s"
        self.contact_outcomes.append(resolved)
        self._write_contact(resolved)
        self._save_summary(now)

    def _observe_needle_drop_candidates(self, before, frame, now, transient):
        # Resolve older candidates first. Production confirmation is deliberately
        # used only as a later label; the transient itself never changes state.
        remaining = deque()
        for candidate in self.pending_contacts:
            age = now - candidate["candidate_unix"]
            if frame.get("music_active"):
                self._resolve_contact(candidate, now, "music_confirmed", frame)
            elif not frame.get("turntable_on"):
                self._resolve_contact(candidate, now, "turntable_stopped", frame)
            elif frame.get("runout_locked"):
                self._resolve_contact(candidate, now, "runout_confirmed", frame)
            elif age >= 10.0:
                self._resolve_contact(candidate, now, "no_music_within_10s", frame)
            else:
                remaining.append(candidate)
        self.pending_contacts = remaining

        motor_idle_before = (
            bool(before.get("turntable_on"))
            and not bool(before.get("music_active"))
            and not bool(before.get("runout_locked"))
            and before.get("status") == "Motor Idle"
        )
        if not motor_idle_before or not transient.get("stylus_contact_candidate"):
            return
        if now - self.last_contact_candidate < 1.0:
            return

        self.contact_sequence += 1
        candidate_id = "%d-%04d" % (int(now * 1000.0), self.contact_sequence)
        candidate = {
            "event": "needle_drop_candidate",
            "unix_time": float(now),
            "candidate_unix": float(now),
            "candidate_id": candidate_id,
            "precondition": "motor_on_music_off_motor_idle",
            "status_before": before.get("status"),
            "motor_confidence_before": before.get("motor_confidence"),
            "music_confidence_before": before.get("music_confidence"),
            "motor_evidence_before": before.get("motor_evidence"),
            "music_evidence_before": before.get("music_evidence"),
            "transient": dict(transient),
        }
        captured = self.capture(
            "needle_drop_candidate",
            now,
            label=None,
            details={
                "candidate_id": candidate_id,
                "precondition": candidate["precondition"],
                "transient": candidate["transient"],
                "purpose": "Learn whether motor-idle contact transients predict imminent music.",
            },
            min_gap_sec=3.0,
        )
        candidate["capture_requested"] = bool(captured)
        self.pending_contacts.append(candidate)
        self.last_contact_candidate = now
        self._write_contact(candidate)

    def _transition_types(self, before, after):
        result = []
        before_status = before.get("status")
        after_status = after.get("status")

        if before_status != "Playing" and after_status == "Playing":
            result.append("playing_on")
        if before_status == "Playing" and after_status != "Playing":
            result.append("playing_off")
        if before_status == "Powered Off" and after_status != "Powered Off":
            result.append("power_on")
        if before_status != "Powered Off" and after_status == "Powered Off":
            result.append("power_off")
        if not before.get("runout_locked") and after.get("runout_locked"):
            result.append("runout_on")
        if before.get("runout_locked") and not after.get("runout_locked"):
            result.append("runout_off")
        return result

    def _rows(self, seconds):
        if not self.history:
            return []
        end = self.history[-1]["unix_time"]
        return [row for row in self.history if end - row["unix_time"] <= seconds]

    @staticmethod
    def _segment_start(rows, predicate, tolerated_misses=2):
        start = rows[-1]["unix_time"] if rows else None
        misses = 0
        seen = False
        for row in reversed(rows):
            if predicate(row):
                seen = True
                misses = 0
                start = row["unix_time"]
            elif seen:
                misses += 1
                if misses > tolerated_misses:
                    break
        return start

    def _best_contact(self, start, end):
        candidates = []
        for row in self.history:
            stamp = row["unix_time"]
            if start <= stamp <= end and row["transient"].get("stylus_contact_candidate"):
                candidates.append(row)
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda row: row["transient"].get("stylus_contact_score", 0.0),
        )

    def _reference(self, transition_type, now, before):
        if transition_type in {"playing_on", "power_on"}:
            rows = self._rows(8.0 if transition_type == "playing_on" else 6.0)
            if transition_type == "playing_on":
                onset = self._segment_start(
                    rows,
                    lambda row: (
                        _safe(row["frame"].get("music_evidence")) >= 0.20
                        or _safe(row["frame"].get("music_confidence")) >= 0.15
                    ),
                )
                source = "music_evidence"
            else:
                onset = self._segment_start(
                    rows,
                    lambda row: (
                        _safe(row["frame"].get("motor_evidence")) >= 0.42
                        or _safe(row["frame"].get("music_evidence")) >= 0.35
                    ),
                )
                source = "power_evidence"

            onset = float(now if onset is None else onset)
            contact = self._best_contact(max(now - 4.0, onset - 2.0), min(now, onset + 0.6))
            if contact is not None and contact["unix_time"] <= onset + 0.6:
                return {
                    "estimated_onset_unix": min(onset, contact["unix_time"]),
                    "reference_source": source + "+stylus_contact_candidate",
                    "reference_quality": "medium",
                    "contact_candidate_unix": contact["unix_time"],
                    "contact_score": contact["transient"].get("stylus_contact_score"),
                }
            return {
                "estimated_onset_unix": onset,
                "reference_source": source,
                "reference_quality": "medium",
                "contact_candidate_unix": None,
                "contact_score": None,
            }

        if transition_type == "playing_off":
            rows = self._rows(5.0)
            last_strong = None
            for index, row in enumerate(rows[:-1]):
                if _safe(row["frame"].get("music_evidence")) >= 0.28:
                    last_strong = index
            if last_strong is not None and last_strong + 1 < len(rows):
                onset = rows[last_strong + 1]["unix_time"]
            else:
                onset = now
            return {
                "estimated_onset_unix": onset,
                "reference_source": "music_evidence_fall",
                "reference_quality": "medium",
                "contact_candidate_unix": None,
                "contact_score": None,
            }

        if transition_type == "power_off":
            rows = self._rows(12.0)
            last_supported = None
            for index, row in enumerate(rows[:-1]):
                f = row["frame"]
                if (
                    _safe(f.get("motor_confidence")) > 0.26
                    or _safe(f.get("music_evidence")) > 0.20
                    or bool(f.get("runout_locked"))
                ):
                    last_supported = index
            if last_supported is not None and last_supported + 1 < len(rows):
                onset = rows[last_supported + 1]["unix_time"]
            else:
                onset = max(now - 5.0, rows[0]["unix_time"] if rows else now)
            return {
                "estimated_onset_unix": onset,
                "reference_source": "sustained_low_power_evidence",
                "reference_quality": "medium",
                "contact_candidate_unix": None,
                "contact_score": None,
            }

        if transition_type == "runout_on":
            rows = [
                row for row in self._rows(12.0)
                if row["frame"].get("runout_candidate_accepted")
            ]
            if rows:
                chain = [rows[-1]]
                for row in reversed(rows[:-1]):
                    if chain[-1]["unix_time"] - row["unix_time"] <= 2.25:
                        chain.append(row)
                    else:
                        break
                onset = chain[-1]["unix_time"]
            else:
                onset = now
            return {
                "estimated_onset_unix": onset,
                "reference_source": "first_coherent_runout_candidate",
                "reference_quality": "medium",
                "contact_candidate_unix": None,
                "contact_score": None,
            }

        if transition_type == "runout_off":
            rows = self._rows(8.0)
            last_candidate = None
            rpm = before.get("runout_rpm")
            for row in rows:
                if row["frame"].get("runout_candidate_accepted"):
                    last_candidate = row["unix_time"]
                    rpm = row["frame"].get("runout_rpm") or rpm
            period = 1.333333333 if str(rpm) == "45" else 1.8
            onset = min(now, (last_candidate + period) if last_candidate is not None else now)
            return {
                "estimated_onset_unix": onset,
                "reference_source": "first_missing_expected_runout_click",
                "reference_quality": "low",
                "contact_candidate_unix": None,
                "contact_score": None,
            }

        return {
            "estimated_onset_unix": now,
            "reference_source": "confirmation_only",
            "reference_quality": "low",
            "contact_candidate_unix": None,
            "contact_score": None,
        }

    def _record_transition(self, transition_type, before, after, now, diagnostic_mode):
        reference = self._reference(transition_type, now, before)
        onset = min(float(now), float(reference["estimated_onset_unix"]))
        latency = max(0.0, float(now) - onset)
        target = TARGET_LATENCY_SECONDS[transition_type]
        slow = latency > target

        record = {
            "transition_type": transition_type,
            "confirmed_unix": float(now),
            "estimated_onset_unix": onset,
            "latency_sec": latency,
            "target_latency_sec": target,
            "slow": bool(slow),
            "before_status": before.get("status"),
            "after_status": after.get("status"),
            "reference_source": reference["reference_source"],
            "reference_quality": reference["reference_quality"],
            "contact_candidate_unix": reference.get("contact_candidate_unix"),
            "contact_score": reference.get("contact_score"),
            "diagnostic_mode": diagnostic_mode,
            "confirmation": {
                "motor_evidence": after.get("motor_evidence"),
                "motor_confidence": after.get("motor_confidence"),
                "music_evidence": after.get("music_evidence"),
                "music_confidence": after.get("music_confidence"),
                "runout_support": after.get("runout_support"),
                "runout_confidence": after.get("runout_confidence"),
                "runout_rpm": after.get("runout_rpm"),
            },
        }

        with open(self.events_path, "a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        self.recent_events.append(record)
        self._save_summary(now)
        self.timeline.record("transition_latency_measured", now=now, **record)

        details = dict(record)
        details["transition_reference_is_ground_truth"] = False
        if slow:
            self.capture(
                "transition_latency_" + transition_type,
                now,
                label=None,
                details=details,
                min_gap_sec=15.0,
            )
        else:
            last_control = self.control_last.get(transition_type, -1e12)
            if now - last_control >= 900.0:
                if self.capture(
                    "transition_control_" + transition_type,
                    now,
                    label=None,
                    details=details,
                    min_gap_sec=900.0,
                ):
                    self.control_last[transition_type] = now
        return record

    @staticmethod
    def _contact_stats(items):
        items = list(items)
        result = {"samples": len(items)}
        if not items:
            return result
        timings = [
            item.get("seconds_to_resolution")
            for item in items
            if item.get("outcome") == "music_confirmed"
            and item.get("seconds_to_resolution") is not None
        ]
        if timings:
            result["median_seconds_to_music"] = statistics.median(timings)
            result["p90_seconds_to_music"] = _percentile(timings, 0.90)
        for name in (
            "transient_peak",
            "transient_crest",
            "derivative_peak",
            "derivative_crest",
            "impulsive_sample_fraction",
            "peak_vs_recent_median",
            "derivative_vs_recent_median",
            "stylus_contact_score",
        ):
            values = [
                _safe(item.get("transient", {}).get(name), float("nan"))
                for item in items
            ]
            values = [value for value in values if math.isfinite(value)]
            if values:
                result[name] = {
                    "median": statistics.median(values),
                    "p10": _percentile(values, 0.10),
                    "p90": _percentile(values, 0.90),
                }
        return result

    def _save_summary(self, now):
        by_type = {}
        for transition_type, target in TARGET_LATENCY_SECONDS.items():
            events = [
                event for event in self.recent_events
                if event["transition_type"] == transition_type
            ]
            latencies = [event["latency_sec"] for event in events]
            by_type[transition_type] = {
                "target_latency_sec": target,
                "samples": len(events),
                "slow_samples": sum(1 for event in events if event["slow"]),
                "median_latency_sec": statistics.median(latencies) if latencies else None,
                "p90_latency_sec": _percentile(latencies, 0.90),
                "max_latency_sec": max(latencies) if latencies else None,
                "latest_latency_sec": latencies[-1] if latencies else None,
            }

        positive_contacts = [
            item for item in self.contact_outcomes
            if item.get("outcome") == "music_confirmed"
        ]
        negative_contacts = [
            item for item in self.contact_outcomes
            if item.get("outcome") != "music_confirmed"
        ]

        _atomic_json(self.summary_path, {
            "updated_unix": float(now),
            "window": "most recent %d confirmed transitions" % SUMMARY_EVENTS,
            "reference_warning": (
                "Estimated onsets are retrospective evidence markers, not ground truth. "
                "Use saved WAV/trace samples for algorithm development."
            ),
            "targets": TARGET_LATENCY_SECONDS,
            "by_type": by_type,
            "needle_drop_candidates": {
                "resolved_samples": len(self.contact_outcomes),
                "music_confirmed": sum(
                    1 for item in self.contact_outcomes
                    if item.get("outcome") == "music_confirmed"
                ),
                "within_2s": sum(
                    1 for item in self.contact_outcomes
                    if item.get("timing_bucket") == "within_2s"
                ),
                "within_5s": sum(
                    1 for item in self.contact_outcomes
                    if item.get("timing_bucket") == "within_5s"
                ),
                "no_music_within_10s": sum(
                    1 for item in self.contact_outcomes
                    if item.get("timing_bucket") == "no_music_within_10s"
                ),
                "pending": len(self.pending_contacts),
                "positive_feature_profile": self._contact_stats(positive_contacts),
                "negative_feature_profile": self._contact_stats(negative_contacts),
            },
        })

    def summary(self):
        try:
            with open(self.summary_path, "r") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {
                "targets": dict(TARGET_LATENCY_SECONDS),
                "by_type": {},
            }
