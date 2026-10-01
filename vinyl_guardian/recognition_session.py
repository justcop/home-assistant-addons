"""Staged Shazam recognition session state.

The live detector keeps recording while increasingly long snapshots are sent to
Shazam. Early successful matches may be displayed immediately; later successful
stages supersede earlier ones. Final track/scrobble state is committed only
after the longest stage and all outstanding earlier requests have returned.
"""

import os
import tempfile
import wave
import numpy as np


DEFAULT_STAGES = (3, 5, 10, 20, 30)


class RecognitionSession:
    def __init__(self, stages=DEFAULT_STAGES):
        cleaned = sorted({int(value) for value in stages if int(value) > 0})
        self.stages = tuple(cleaned or DEFAULT_STAGES)
        self.generation = 0
        self.requested = set()
        self.pending = set()
        self.returned = set()
        self.best_stage = 0
        self.best_match = None
        self.best_trimmed_seconds = 0.0
        self.finalized = False

    @property
    def final_stage(self):
        return self.stages[-1]

    def begin(self):
        self.generation += 1
        self.requested.clear()
        self.pending.clear()
        self.returned.clear()
        self.best_stage = 0
        self.best_match = None
        self.best_trimmed_seconds = 0.0
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

    def record_result(self, token, state, stage, match, trimmed_seconds=0.0):
        stage = int(stage)
        if not self.valid(token, state) or stage not in self.requested:
            return {
                "accepted": False,
                "display": False,
                "finalize": False,
                "best_match": None,
                "best_stage": 0,
                "best_trimmed_seconds": 0.0,
            }

        self.pending.discard(stage)
        self.returned.add(stage)

        display = False
        if match and stage >= self.best_stage:
            self.best_stage = stage
            self.best_match = dict(match)
            self.best_trimmed_seconds = float(trimmed_seconds)
            display = True

        finalize = (
            not self.finalized
            and self.final_stage in self.requested
            and not self.pending
        )
        if finalize:
            self.finalized = True

        return {
            "accepted": True,
            "display": display,
            "finalize": finalize,
            "best_match": dict(self.best_match) if self.best_match else None,
            "best_stage": self.best_stage,
            "best_trimmed_seconds": self.best_trimmed_seconds,
        }


def recognize_fragment(raw, directory, rate, channels, onset, minimum_seconds, recognize):
    """Trim on complete frames and delete a unique upload even on API failure."""
    samples = np.frombuffer(raw, dtype=np.int16)
    usable = len(samples) - len(samples) % channels
    frames = samples[:usable].reshape(-1, channels)
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
