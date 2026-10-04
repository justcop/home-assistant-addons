"""Fast initial Shazam recognition with explicit confidence.

Initial acquisition uses only overlapping 3, 5 and 10 second windows.  Longer
20/30 second evidence is handled later as fresh ten-second verification windows
by TrackMonitor; Shazam is never handed more than ten seconds by this module.
"""

import os
import tempfile
import wave
from collections import Counter

import numpy as np

from track_reasoning import identity_key


DEFAULT_STAGES = (3, 5, 10)
MAX_SHAZAM_WINDOW_SECONDS = 10.0


class RecognitionSession:
    def __init__(self, stages=DEFAULT_STAGES):
        cleaned = sorted({int(value) for value in stages if 0 < int(value) <= 10})
        self.stages = tuple(cleaned or DEFAULT_STAGES)
        self.generation = 0
        self.requested = set()
        self.pending = set()
        self.results = {}
        self.displayed_stage = 0
        self.finalized = False

    @property
    def final_stage(self):
        return self.stages[-1]

    def begin(self):
        self.generation += 1
        self.requested.clear()
        self.pending.clear()
        self.results.clear()
        self.displayed_stage = 0
        self.finalized = False
        return self.generation

    def invalidate(self):
        self.generation += 1
        self.pending.clear()

    def valid(self, token, state):
        return token == self.generation and state in ("RECORDING", "PROCESSING")

    def due_stages(self, token, seconds):
        if token != self.generation or self.finalized:
            return []
        due = []
        for stage in self.stages:
            if seconds >= stage and stage not in self.requested:
                self.requested.add(stage)
                self.pending.add(stage)
                due.append(stage)
        return due

    def _decision(self):
        matches = [
            (stage, row["match"], row["trimmed_seconds"])
            for stage, row in sorted(self.results.items())
            if row.get("match")
        ]
        if not matches:
            return {
                "match": None,
                "stage": 0,
                "trimmed_seconds": 0.0,
                "confidence": "low",
                "support": 0,
                "conflicts": 0,
            }

        keyed = [(stage, identity_key(match), match, trimmed) for stage, match, trimmed in matches]
        counts = Counter(key for _stage, key, _match, _trimmed in keyed if key)
        if not counts:
            stage, _key, match, trimmed = keyed[-1]
            return {
                "match": match,
                "stage": stage,
                "trimmed_seconds": trimmed,
                "confidence": "low",
                "support": 1,
                "conflicts": max(0, len(matches) - 1),
            }

        winner = max(
            counts,
            key=lambda key: (
                counts[key],
                max(stage for stage, item_key, _match, _trimmed in keyed if item_key == key),
            ),
        )
        winner_rows = [row for row in keyed if row[1] == winner]
        stage, _key, match, trimmed = winner_rows[-1]
        support = len(winner_rows)
        conflicts = sum(1 for _stage, key, _match, _trimmed in keyed if key != winner)
        confidence = "high" if support >= 2 and conflicts == 0 else "medium" if support >= 2 else "low"
        return {
            "match": match,
            "stage": stage,
            "trimmed_seconds": trimmed,
            "confidence": confidence,
            "support": support,
            "conflicts": conflicts,
        }

    def record_result(self, token, state, stage, match, trimmed_seconds=0.0):
        stage = int(stage)
        if not self.valid(token, state) or stage not in self.requested:
            return {"accepted": False, "display": False, "finalize": False}

        self.pending.discard(stage)
        self.results[stage] = {
            "match": dict(match) if match else None,
            "trimmed_seconds": float(trimmed_seconds),
        }

        display = bool(match and stage >= self.displayed_stage)
        if display:
            self.displayed_stage = stage

        decision = self._decision()

        # Normal case: the 3s and 5s fingerprints agree.  Confirm immediately
        # instead of waiting for 10s.
        early_pair_ready = 3 in self.results and 5 in self.results
        early_pair_same = (
            early_pair_ready
            and self.results[3].get("match")
            and self.results[5].get("match")
            and identity_key(self.results[3]["match"]) == identity_key(self.results[5]["match"])
        )

        final_ready = (
            self.final_stage in self.results
            and not self.pending
        )
        finalize = bool(early_pair_same or final_ready)
        if finalize:
            self.finalized = True

        return {
            "accepted": True,
            "display": display,
            "finalize": finalize,
            "best_match": dict(decision["match"]) if decision["match"] else None,
            "best_stage": int(decision["stage"]),
            "best_trimmed_seconds": float(decision["trimmed_seconds"]),
            "confidence": decision["confidence"],
            "support": int(decision["support"]),
            "conflicts": int(decision["conflicts"]),
        }


def recognize_fragment(raw, directory, rate, channels, onset, minimum_seconds, recognize):
    """Trim on complete frames, enforce <=10s, and clean up every temp WAV."""
    samples = np.frombuffer(raw, dtype=np.int16)
    usable = len(samples) - len(samples) % channels
    frames = samples[:usable].reshape(-1, channels)
    max_frames = int(float(rate) * MAX_SHAZAM_WINDOW_SECONDS)
    if len(frames) > max_frames:
        raise ValueError(
            f"Recognition window is {len(frames) / float(rate):.2f}s; "
            f"maximum is {MAX_SHAZAM_WINDOW_SECONDS:.0f}s"
        )

    peaks = np.max(np.abs(frames.astype(np.int32)), axis=1) if len(frames) else np.array([])
    hits = np.flatnonzero(peaks > onset)
    start = int(hits[0]) if len(hits) else 0
    start = min(start, max(0, len(frames) - int(rate * minimum_seconds)))

    fd, path = tempfile.mkstemp(prefix="recognition_", suffix=".wav", dir=directory)
    os.close(fd)
    try:
        with wave.open(path, "wb") as recording:
            recording.setnchannels(channels)
            recording.setsampwidth(2)
            recording.setframerate(rate)
            recording.writeframes(frames[start:].tobytes())
        return recognize(path), start / rate
    finally:
        os.unlink(path)
