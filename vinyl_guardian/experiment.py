"""Experimental harness around Vinyl Guardian's production detector.

Nothing in this module is allowed to decide the live turntable state. It
observes production frames, runs shadow detectors, captures interesting audio,
records an event timeline, monitors the input hardware, and tracks record-side
sessions. This separation lets us collect evidence before promoting any new
algorithm into production.
"""

import json
import math
import os
import threading
import time
from diagnostic_audio import write_flac
from collections import deque

import numpy as np

from detector import GuardianDetector
from diagnostic_monitor import DiagnosticMonitor
from telemetry import FeatureExtractor, pcm16_channels, stereo_features
from transition_latency import TransitionLatencyCollector
from safety_monitor import SafetyMonitor


TRUSTED_LABELS = {
    "actually_off",
    "motor_on_needle_up",
    "playing",
    "between_tracks",
    "runout",
    "needle_lifted",
    "wrong_state",
    "ignore",
}


def _atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _median(values, default=0.0):
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return float(np.median(clean)) if clean else float(default)


class EventTimeline:
    def __init__(self, share_dir):
        self.root = os.path.join(share_dir, "experiments")
        os.makedirs(self.root, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(self.root, f"events_{stamp}.jsonl")
        self.lock = threading.Lock()
        self.sequence = 0

    def record(self, event_type, now=None, **details):
        now = float(time.time() if now is None else now)
        with self.lock:
            self.sequence += 1
            item = {
                "sequence": self.sequence,
                "unix_time": now,
                "local_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                "event": str(event_type),
            }
            item.update(details)
            with open(self.path, "a") as handle:
                handle.write(json.dumps(item, sort_keys=True) + "\n")
        return item


class ShadowDetectorSuite:
    """Run alternative detector configurations without controlling anything."""

    def __init__(self, thresholds, rate, channels):
        base = dict(thresholds or {})
        self.detectors = {}

        sensitive = dict(base)
        sensitive["music_threshold"] = float(base.get("music_threshold", 0.002)) * 0.75
        sensitive["music_hold_threshold"] = float(
            base.get("music_hold_threshold", sensitive["music_threshold"] * 0.6)
        ) * 0.80
        self.detectors["music_sensitive"] = GuardianDetector(
            sensitive, rate=rate, channels=channels
        )

        profile_heavy = dict(base)
        profile_heavy["motor_profile_weight"] = 0.60
        self.detectors["profile_heavy"] = GuardianDetector(
            profile_heavy, rate=rate, channels=channels
        )

        conservative = dict(base)
        conservative["motor_on_confidence"] = 0.82
        conservative["motor_on_seconds"] = 1.8
        conservative["music_power_assist_confidence"] = 0.93
        self.detectors["power_conservative"] = GuardianDetector(
            conservative, rate=rate, channels=channels
        )

        self.last_frames = {}

    def update(self, data, now, force_music_active=False):
        frames = {}
        for name, detector in self.detectors.items():
            frames[name] = detector.update_pcm(
                data,
                now,
                force_music_active=force_music_active,
            )
        self.last_frames = frames
        return frames

    def compact(self):
        result = {}
        for name, frame in self.last_frames.items():
            result[name] = {
                "status": frame.get("status"),
                "turntable_on": bool(frame.get("turntable_on")),
                "music_active": bool(frame.get("music_active")),
                "motor_confidence": float(frame.get("motor_confidence", 0.0)),
                "music_confidence": float(frame.get("music_confidence", 0.0)),
                "runout_locked": bool(frame.get("runout_locked")),
                "runout_rpm": frame.get("runout_rpm"),
            }
        return result


class EventAudioRecorder:
    def __init__(
        self,
        share_dir,
        rate,
        channels,
        chunk,
        enabled=True,
        pre_roll_sec=20.0,
        post_roll_sec=5.0,
        max_files=100,
    ):
        self.enabled = bool(enabled)
        self.rate = int(rate)
        self.channels = max(1, int(channels or 1))
        self.chunk = int(chunk)
        self.pre_chunks = max(1, int(self.rate / self.chunk * pre_roll_sec))
        self.post_chunks = max(1, int(self.rate / self.chunk * post_roll_sec))
        self.ring = deque(maxlen=self.pre_chunks)
        self.context_ring = deque(maxlen=self.pre_chunks)
        self.pending = []
        self.root = os.path.join(share_dir, "experiments", "event_audio")
        self.max_files = max(10, int(max_files))
        self.last_trigger = {}

    def feed(self, data, context=None):
        self.ring.append(bytes(data))
        self.context_ring.append(dict(context or {}))
        if not self.enabled:
            return []

        completed = []
        still_pending = []
        for capture in self.pending:
            capture["chunks"].append(bytes(data))
            capture["contexts"].append(dict(context or {}))
            capture["remaining"] -= 1
            if capture["remaining"] <= 0:
                path = self._write_capture(capture)
                if path:
                    completed.append((capture, path))
            else:
                still_pending.append(capture)
        self.pending = still_pending
        return completed

    def flush(self):
        completed = []
        for capture in self.pending:
            path = self._write_capture(capture)
            if path:
                completed.append((capture, path))
        self.pending.clear()
        return completed

    def trigger(self, kind, now, label=None, details=None, min_gap_sec=30.0, force=False):
        if not self.enabled:
            return False
        kind = str(kind)
        last = self.last_trigger.get(kind, -1e12)
        if not force and float(now) - last < float(min_gap_sec):
            return False

        # One growing clip can contain multiple related transitions. Bound memory
        # when noise causes many simultaneous observations.
        if len(self.pending) >= 8:
            return False
        self.last_trigger[kind] = float(now)
        self.pending.append({
            "kind": kind,
            "label": label,
            "details": details or {},
            "trigger_time": float(now),
            "chunks": list(self.ring),
            "contexts": list(self.context_ring),
            "remaining": self.post_chunks,
            "initial_pre_chunks": len(self.ring),
        })
        return True

    def _write_capture(self, capture):
        try:
            os.makedirs(self.root, exist_ok=True)
            stamp = time.strftime(
                "%Y%m%d_%H%M%S",
                time.localtime(capture["trigger_time"]),
            )
            safe_kind = "".join(
                c if c.isalnum() or c in "-_" else "_"
                for c in capture["kind"]
            )[:50]
            path = os.path.join(self.root, f"{stamp}_{safe_kind}.flac")
            suffix = 1
            while any(os.path.exists(os.path.splitext(path)[0] + ext) for ext in (".wav", ".flac", ".json")):
                path = os.path.join(
                    self.root,
                    f"{stamp}_{safe_kind}_{suffix:02d}.flac",
                )
                suffix += 1
            write_flac(path, b"".join(capture["chunks"]), self.rate, self.channels)

            trace_path = os.path.splitext(path)[0] + '.frames.jsonl'
            with open(trace_path, 'w') as trace:
                for context in capture['contexts']:
                    trace.write(json.dumps(context) + '\n')
            sidecar = os.path.splitext(path)[0] + ".json"
            details = capture.get("details") or {}
            human_reviewed = bool(details.get("human_reviewed"))
            review = {
                "status": "reviewed" if human_reviewed else "pending",
            }
            if human_reviewed and capture.get("label"):
                review.update({
                    "reviewed_label": capture.get("label"),
                    "reviewed_unix": capture["trigger_time"],
                    "source": "explicit_manual_mark",
                })
            else:
                suggested = details.get("suggested_label")
                if suggested:
                    review["suggested_label"] = suggested
            _atomic_json(sidecar, {
                "event": capture["kind"],
                "label": capture.get("label") if human_reviewed else None,
                "trigger_time": capture["trigger_time"],
                "details": details,
                "review": review,
                "pre_roll_sec": capture['initial_pre_chunks'] * self.chunk / self.rate,
                "trace": os.path.basename(trace_path),
                "post_roll_sec": (len(capture['chunks'])-capture['initial_pre_chunks']) * self.chunk / self.rate,
                "audio": os.path.basename(path),
                "audio_format": "flac",
            })
            self._prune()
            return path
        except Exception as error:
            print(f"🚨 Diagnostic audio save failed in {self.root}: {error}", flush=True)
            return None

    def _prune(self):
        try:
            pending = []
            for name in os.listdir(self.root):
                if not name.lower().endswith((".wav", ".flac")):
                    continue
                path = os.path.join(self.root, name)
                sidecar = os.path.splitext(path)[0] + ".json"
                reviewed = False
                try:
                    if os.path.exists(sidecar):
                        with open(sidecar, "r") as handle:
                            metadata = json.load(handle)
                        review = metadata.get("review") or {}
                        reviewed = review.get("status") == "reviewed"
                except (OSError, ValueError, TypeError):
                    reviewed = False
                # Human-reviewed clips form the permanent regression library.
                # Retention limits apply only to unreviewed automatic evidence.
                if not reviewed:
                    pending.append(path)

            pending.sort(key=os.path.getmtime)
            for path in pending[:-self.max_files]:
                try:
                    os.remove(path)
                    sidecar = os.path.splitext(path)[0] + ".json"
                    if os.path.exists(sidecar):
                        os.remove(sidecar)
                    trace = os.path.splitext(path)[0] + '.frames.jsonl'
                    if os.path.exists(trace):
                        os.remove(trace)
                except Exception:
                    pass
        except Exception:
            pass


class HardwareHealthMonitor:
    def __init__(self, share_dir, rate, channels):
        self.rate = int(rate)
        self.channels = max(1, int(channels or 1))
        self.extractor = FeatureExtractor(self.rate)
        self.snapshots = deque(maxlen=240)
        self.last_sample = -1e12
        self.last_write = -1e12
        self.path = os.path.join(share_dir, "experiments", "hardware_health.json")
        self.report = {
            "channel_mode": "unknown",
            "configured_channels": self.channels,
        }

    def observe(self, data, now, trusted_label=None):
        if float(now) - self.last_sample < 0.5:
            return self.report
        self.last_sample = float(now)

        channel_data = pcm16_channels(data, self.channels)
        if channel_data.shape[0] == 0:
            return self.report

        stereo = stereo_features(channel_data)
        mono = np.mean(channel_data, axis=1)
        spectral = self.extractor.extract(mono)

        left_dc = float(np.mean(channel_data[:, 0]))
        right_dc = (
            float(np.mean(channel_data[:, 1]))
            if channel_data.shape[1] >= 2 else 0.0
        )

        snap = {
            **stereo,
            "rms": spectral.get("rms", 0.0),
            "spectral_centroid_hz": spectral.get("spectral_centroid_hz", 0.0),
            "rolloff_95_hz": spectral.get("rolloff_95_hz", 0.0),
            "clipping_fraction": spectral.get("clipping_fraction", 0.0),
            "left_dc": left_dc,
            "right_dc": right_dc,
            "trusted_label": trusted_label,
        }
        self.snapshots.append(snap)
        self.report = self._summarise(now)

        if float(now) - self.last_write >= 15.0:
            self.last_write = float(now)
            try:
                _atomic_json(self.path, self.report)
            except Exception:
                pass
        return self.report

    def _summarise(self, now):
        recent = list(self.snapshots)
        if not recent:
            return self.report

        left_rms = _median([x.get("left_rms") for x in recent])
        right_rms = _median([x.get("right_rms") for x in recent])
        correlation = _median([x.get("stereo_correlation") for x in recent])
        side_mid = _median([x.get("stereo_side_mid_ratio") for x in recent])
        identical = _median([x.get("stereo_identical_fraction") for x in recent])
        imbalance = _median([x.get("left_right_rms_db") for x in recent])
        clipping = _median([x.get("clipping_fraction") for x in recent])
        left_dc = _median([x.get("left_dc") for x in recent])
        right_dc = _median([x.get("right_dc") for x in recent])
        bandwidth = _median([x.get("rolloff_95_hz") for x in recent])

        if self.channels < 2:
            mode = "mono"
        elif min(left_rms, right_rms) < max(left_rms, right_rms, 1e-12) * 0.04:
            mode = "one_channel_weak"
        elif identical >= 0.98 or (correlation >= 0.998 and side_mid <= 0.01):
            mode = "probable_dual_mono"
        elif correlation >= -0.99 and side_mid > 0.01:
            mode = "stereo_or_independent_channels"
        else:
            mode = "unknown"

        off_rms = [
            x.get("rms", 0.0)
            for x in recent
            if x.get("trusted_label") == "actually_off"
        ]

        return {
            "updated_unix": float(now),
            "channel_mode": mode,
            "configured_channels": self.channels,
            "left_rms": left_rms,
            "right_rms": right_rms,
            "left_right_correlation": correlation,
            "left_right_imbalance_db": imbalance,
            "side_mid_ratio": side_mid,
            "identical_sample_fraction": identical,
            "clipping_fraction": clipping,
            "left_dc_offset": left_dc,
            "right_dc_offset": right_dc,
            "effective_rolloff_95_hz": bandwidth,
            "trusted_off_noise_floor_rms": _median(off_rms) if off_rms else None,
            "samples_summarised": len(recent),
        }


class SideSessionTracker:
    def __init__(self, share_dir, timeline):
        self.root = os.path.join(share_dir, "experiments")
        os.makedirs(self.root, exist_ok=True)
        self.history_path = os.path.join(self.root, "side_sessions.jsonl")
        self.latest_path = os.path.join(self.root, "latest_side_session.json")
        self.timeline = timeline
        self.active = None
        self.previous_runout = False

    def observe(self, frame, now):
        now = float(now)
        if self.active is None and frame.get("turntable_on") and frame.get("music_active"):
            self.active = {
                "started_unix": now,
                "tracks": [],
                "runout_detected": False,
                "runout_rpm": None,
                "runout_estimated_rpm": None,
                "ended_unix": None,
                "end_reason": None,
            }
            self.timeline.record("side_started", now=now)

        if self.active is not None:
            if frame.get("runout_locked"):
                self.active["runout_detected"] = True
                self.active["runout_rpm"] = frame.get("runout_rpm")
                self.active["runout_estimated_rpm"] = frame.get("runout_estimated_rpm")

            reason = None
            if not frame.get("turntable_on"):
                reason = "turntable_power_off"
            elif (
                self.previous_runout
                and not frame.get("runout_locked")
                and not frame.get("music_active")
                and frame.get("status") == "Motor Idle"
            ):
                reason = "needle_lift_after_runout"

            if reason:
                self._finish(now, reason)

        self.previous_runout = bool(frame.get("runout_locked"))

    def track_identified(self, track, now=None):
        if self.active is None or not isinstance(track, dict):
            return
        identity = f"{track.get('title', '')} - {track.get('artist', '')}".strip(" -")
        if not identity:
            return
        if self.active["tracks"] and self.active["tracks"][-1].get("id") == identity:
            return
        entry = {
            "id": identity,
            "title": track.get("title", ""),
            "artist": track.get("artist", ""),
            "identified_unix": float(time.time() if now is None else now),
        }
        self.active["tracks"].append(entry)
        self.timeline.record("side_track_identified", now=entry["identified_unix"], **entry)

    def _finish(self, now, reason):
        if self.active is None:
            return
        self.active["ended_unix"] = float(now)
        self.active["duration_sec"] = max(0.0, float(now) - self.active["started_unix"])
        self.active["track_count"] = len(self.active["tracks"])
        self.active["end_reason"] = reason
        payload = dict(self.active)
        with open(self.history_path, "a") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        _atomic_json(self.latest_path, payload)
        self.timeline.record(
            "side_ended",
            now=now,
            duration_sec=payload["duration_sec"],
            track_count=payload["track_count"],
            runout_detected=payload["runout_detected"],
            runout_rpm=payload["runout_rpm"],
            end_reason=reason,
        )
        self.active = None

    def summary(self, now=None):
        if self.active is None:
            return {
                "state": "Idle",
                "active": False,
                "track_count": 0,
            }
        now = float(time.time() if now is None else now)
        return {
            "state": "Playing side",
            "active": True,
            "started_unix": self.active["started_unix"],
            "elapsed_sec": max(0.0, now - self.active["started_unix"]),
            "track_count": len(self.active["tracks"]),
            "runout_detected": self.active["runout_detected"],
            "runout_rpm": self.active["runout_rpm"],
        }


class TrustedBaselineLearner:
    """Learn only from explicit/session labels; never from detector guesses."""

    def __init__(self, share_dir):
        self.path = os.path.join(share_dir, "experiments", "trusted_baselines.json")
        self.samples = {
            "actually_off": deque(maxlen=5000),
            "motor_on_needle_up": deque(maxlen=5000),
            "playing": deque(maxlen=5000),
            "runout": deque(maxlen=5000),
        }
        self.last_write = -1e12

    def observe(self, label, feature_snapshot, now):
        if label not in self.samples or not feature_snapshot:
            return
        keep = {
            name: feature_snapshot.get(name)
            for name in (
                "rms",
                "spectral_centroid_hz",
                "spectral_flatness",
                "spectral_flux",
                "band_low_ratio",
                "band_mid_ratio",
                "band_high_ratio",
                "stereo_side_mid_ratio",
            )
        }
        self.samples[label].append(keep)
        if float(now) - self.last_write >= 60.0:
            self.last_write = float(now)
            self._save(now)

    def _save(self, now):
        output = {"updated_unix": float(now), "labels": {}}
        for label, rows in self.samples.items():
            rows = list(rows)
            if not rows:
                continue
            stats = {"samples": len(rows)}
            for name in rows[0].keys():
                values = [
                    float(row[name])
                    for row in rows
                    if row.get(name) is not None and math.isfinite(float(row[name]))
                ]
                if values:
                    stats[name] = {
                        "median": float(np.median(values)),
                        "p10": float(np.percentile(values, 10)),
                        "p90": float(np.percentile(values, 90)),
                    }
            output["labels"][label] = stats
        try:
            _atomic_json(self.path, output)
        except Exception:
            pass


class ExperimentHarness:
    """Top-level observer. Safe to disable without affecting live detection."""

    def __init__(
        self,
        share_dir,
        thresholds,
        rate,
        channels,
        chunk,
        enabled=True,
        auto_capture=True,
        session_label="unlabelled",
        diagnostic_mode='normal',
        addon_version='',
        audio_source='',
    ):
        self.enabled = bool(enabled)
        self.share_dir = share_dir
        self.rate = int(rate)
        self.channels = int(channels)
        self.chunk = int(chunk)
        self.session_label = str(session_label or "unlabelled").strip().lower()
        self.timeline = EventTimeline(share_dir)
        self.shadows = ShadowDetectorSuite(thresholds, rate, channels)
        self.audio = EventAudioRecorder(
            share_dir,
            rate,
            channels,
            chunk,
            enabled=bool(auto_capture),
        )
        self.auto_capture = bool(auto_capture)
        self.monitor = DiagnosticMonitor(share_dir, thresholds, self.audio.trigger,
                                         diagnostic_mode, addon_version, audio_source)
        if self.monitor.mode != 'normal':
            self.audio.enabled = True
        self.health = HardwareHealthMonitor(share_dir, rate, channels)
        self.side = SideSessionTracker(share_dir, self.timeline)
        self.baselines = TrustedBaselineLearner(share_dir)
        self.feature_extractor = FeatureExtractor(rate)
        self.transitions = TransitionLatencyCollector(
            share_dir,
            self.audio.trigger,
            self.timeline,
            rate,
            channels,
            chunk,
        )
        self.safety = SafetyMonitor(share_dir, timeline=self.timeline)

        self.previous_frame = None
        self.previous_shadow = {}
        self.current_manual_label = None
        self.manual_label_until = -1e12
        self.motor_uncertain_since = None
        self.music_uncertain_since = None
        self.disagreement_since = {}
        self.last_feature_snapshot = None

        self.timeline.record(
            "experiment_harness_started",
            enabled=self.enabled,
            auto_capture=bool(auto_capture),
            session_label=self.session_label,
            shadows=list(self.shadows.detectors.keys()),
        )

    def set_diagnostic_mode(self, mode, now=None):
        self.audio.flush()
        self.audio.ring.clear()
        self.audio.context_ring.clear()
        accepted = self.monitor.set_mode(mode, time.time() if now is None else now)
        self.audio.enabled = self.auto_capture or self.monitor.mode != 'normal'
        if accepted:
            self.timeline.record('diagnostic_mode_changed', mode=mode)
        return accepted

    def trusted_label(self, now):
        # Only an explicit one-shot human mark is trusted live. Persistent
        # modes and dataset labels are capture hints only because they can be
        # forgotten and become stale while the physical state changes.
        if float(now) <= self.manual_label_until and self.current_manual_label:
            return self.current_manual_label
        return None

    def manual_label(self, label, now=None):
        now = float(time.time() if now is None else now)
        label = str(label or "").strip().lower()
        if label not in TRUSTED_LABELS:
            return False
        self.current_manual_label = label
        self.manual_label_until = now + 10.0
        self.timeline.record(
            "manual_ground_truth",
            now=now,
            label=label,
            applies_from_unix=now - 20.0,
            applies_until_unix=now + 10.0,
        )
        self.audio.trigger(
            "manual_label",
            now,
            label=label,
            details={"ground_truth": label, "human_reviewed": True},
            min_gap_sec=0,
            force=True,
        )
        return True

    def track_identified(self, track, now=None):
        self.side.track_identified(track, now=now)
        self.monitor.notify("confirmed_track", track, now)
        if isinstance(track, dict):
            self.timeline.record(
                "track_identified",
                now=now,
                title=track.get("title", ""),
                artist=track.get("artist", ""),
                album=track.get("album", ""),
            )

    def observe(self, data, now, production_frame, engine_state, force_music_active=False):
        if not self.enabled:
            return {}

        now = float(now)
        transient = self.transitions.measure(data, now)
        context = {'unix_time': now, 'production': dict(production_frame),
                   'engine_state': engine_state, 'extended_features': self.last_feature_snapshot,
                   'transient_features': transient,
                   'diagnostic_mode': self.monitor.mode, 'audio_source': self.monitor.source,
                   'confirmed_track': self.monitor.track}
        completed = self.audio.feed(data, context)
        diagnostic = self.monitor.observe(production_frame, now)
        self.audio.enabled = self.auto_capture or self.monitor.mode != 'normal'
        for capture, path in completed:
            self.timeline.record(
                "event_audio_saved",
                now=now,
                event_kind=capture.get("kind"),
                label=capture.get("label"),
                path=path,
            )

        shadow_frames = self.shadows.update(
            data,
            now,
            force_music_active=force_music_active,
        )

        trusted = self.trusted_label(now)
        health = self.health.observe(data, now, trusted_label=trusted)

        # Candidate feature snapshot for trusted-baseline learning. This is
        # intentionally separate from production detection.
        if (
            self.last_feature_snapshot is None
            or now - self.last_feature_snapshot.get("_time", -1e12) >= 0.5
        ):
            channels = pcm16_channels(data, self.channels)
            if channels.shape[0]:
                mono = np.mean(channels, axis=1)
                snapshot = self.feature_extractor.extract(mono)
                snapshot.update(stereo_features(channels))
                snapshot["_time"] = now
                self.last_feature_snapshot = snapshot
                self.baselines.observe(trusted, snapshot, now)

        latency_records = self.transitions.observe(
            production_frame,
            now,
            transient,
            diagnostic_mode=self.monitor.mode,
        )
        safety = self.safety.observe(
            production_frame,
            shadow_frames,
            now,
            diagnostic_mode=self.monitor.mode,
        )
        self._record_transitions(production_frame, shadow_frames, now, engine_state)
        self._detect_interesting(production_frame, shadow_frames, now, trusted)
        self.side.observe(production_frame, now)

        self.previous_frame = dict(production_frame)
        self.previous_shadow = {
            name: dict(frame) for name, frame in shadow_frames.items()
        }

        return {
            "shadows": self.shadows.compact(),
            "hardware": health,
            "side": self.side.summary(now),
            "trusted_label": trusted,
            "diagnostics": diagnostic,
            "transition_latency": latency_records,
            "safety": safety,
        }

    def _record_transitions(self, frame, shadows, now, engine_state):
        previous = self.previous_frame
        if previous is None:
            self.timeline.record(
                "production_initial_state",
                now=now,
                status=frame.get("status"),
                turntable_on=bool(frame.get("turntable_on")),
                music_active=bool(frame.get("music_active")),
                engine_state=engine_state,
            )
        else:
            for field in ("turntable_on", "music_active", "runout_locked", "status"):
                if previous.get(field) != frame.get(field):
                    details = {
                        "field": field,
                        "before": previous.get(field),
                        "after": frame.get(field),
                        "status": frame.get("status"),
                        "motor_confidence": frame.get("motor_confidence"),
                        "music_confidence": frame.get("music_confidence"),
                        "runout_rpm": frame.get("runout_rpm"),
                        "runout_estimated_rpm": frame.get("runout_estimated_rpm"),
                        "runout_phase_jitter_ms": frame.get("runout_phase_jitter_ms"),
                        "runout_support": frame.get("runout_support"),
                        "engine_state": engine_state,
                    }
                    self.timeline.record("production_transition", now=now, **details)

        if frame.get("runout_candidate_accepted"):
            self.timeline.record(
                "runout_candidate",
                now=now,
                interval_sec=frame.get("runout_last_interval_sec"),
                recent_intervals_sec=frame.get("runout_candidate_intervals_sec") or [],
                support=frame.get("runout_support"),
                confidence=frame.get("runout_confidence"),
                rpm_label=frame.get("runout_rpm"),
                estimated_rpm=frame.get("runout_estimated_rpm"),
                phase_jitter_ms=frame.get("runout_phase_jitter_ms"),
            )

        for name, shadow in shadows.items():
            prior = self.previous_shadow.get(name)
            if prior is None:
                continue
            if prior.get("status") != shadow.get("status"):
                self.timeline.record(
                    "shadow_transition",
                    now=now,
                    shadow=name,
                    before=prior.get("status"),
                    after=shadow.get("status"),
                )

    def _detect_interesting(self, frame, shadows, now, trusted):
        prod_signature = (
            bool(frame.get("turntable_on")),
            bool(frame.get("music_active")),
            bool(frame.get("runout_locked")),
        )

        for name, shadow in shadows.items():
            shadow_signature = (
                bool(shadow.get("turntable_on")),
                bool(shadow.get("music_active")),
                bool(shadow.get("runout_locked")),
            )
            if shadow_signature != prod_signature:
                if name not in self.disagreement_since:
                    self.disagreement_since[name] = now
                elif now - self.disagreement_since[name] >= 2.0:
                    if self.audio.trigger(
                        "shadow_disagreement",
                        now,
                        label=trusted,
                        details={
                            "shadow": name,
                            "production": prod_signature,
                            "shadow_state": shadow_signature,
                        },
                        min_gap_sec=60.0,
                    ):
                        self.timeline.record(
                            "shadow_disagreement_capture",
                            now=now,
                            shadow=name,
                            production=prod_signature,
                            shadow_state=shadow_signature,
                        )
                    self.disagreement_since[name] = now
            else:
                self.disagreement_since.pop(name, None)

        motor_conf = float(frame.get("motor_confidence", 0.0))
        if 0.35 <= motor_conf <= 0.65:
            if self.motor_uncertain_since is None:
                self.motor_uncertain_since = now
            elif now - self.motor_uncertain_since >= 8.0:
                self.audio.trigger(
                    "motor_uncertain",
                    now,
                    label=trusted,
                    details={"motor_confidence": motor_conf},
                    min_gap_sec=120.0,
                )
                self.motor_uncertain_since = now
        else:
            self.motor_uncertain_since = None

        music_conf = float(frame.get("music_confidence", 0.0))
        if 0.35 <= music_conf <= 0.65:
            if self.music_uncertain_since is None:
                self.music_uncertain_since = now
            elif now - self.music_uncertain_since >= 5.0:
                self.audio.trigger(
                    "music_uncertain",
                    now,
                    label=trusted,
                    details={"music_confidence": music_conf},
                    min_gap_sec=120.0,
                )
                self.music_uncertain_since = now
        else:
            self.music_uncertain_since = None

        previous = self.previous_frame or {}
        if not previous.get("runout_locked") and frame.get("runout_locked"):
            self.audio.trigger(
                "runout_lock",
                now,
                label=trusted,
                details={
                    "rpm": frame.get("runout_rpm"),
                    "estimated_rpm": frame.get("runout_estimated_rpm"),
                    "phase_jitter_ms": frame.get("runout_phase_jitter_ms"),
                    "support": frame.get("runout_support"),
                },
                min_gap_sec=30.0,
            )


    def status(self, now=None):
        now = float(time.time() if now is None else now)
        return {
            "enabled": self.enabled,
            "session_label": self.session_label,
            "trusted_label": self.trusted_label(now),
            "hardware": self.health.report,
            "shadows": self.shadows.compact(),
            "side": self.side.summary(now),
            "event_log": self.timeline.path,
            "diagnostics": self.monitor.summary(),
            "transition_latency": self.transitions.summary(),
            "safety": self.safety.summary(now),
        }
