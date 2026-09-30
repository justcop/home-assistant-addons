"""Staged recognition ordering and isolated PCM uploads.

Call session methods while holding the application's state lock.
"""
import os
import tempfile
import wave
import numpy as np


class RecognitionSession:
    def __init__(self):
        self.generation = 0
        self.preview_sent = False
        self.long_returned = False

    def begin(self):
        self.generation += 1
        self.preview_sent = self.long_returned = False
        return self.generation

    def invalidate(self):
        self.generation += 1

    def valid(self, token, state):
        return token == self.generation and state in ('RECORDING', 'PROCESSING')

    def preview_due(self, token, seconds, full_seconds):
        if token != self.generation or self.preview_sent or full_seconds <= 5 or seconds < 5:
            return False
        self.preview_sent = True
        return True

    def accept(self, token, state, preview):
        if not self.valid(token, state) or (preview and self.long_returned):
            return False
        if not preview:
            self.long_returned = True
        return True


def recognize_fragment(raw, directory, rate, channels, onset, minimum_seconds, recognize):
    """Trim on complete frames and delete a unique upload even on API failure."""
    samples = np.frombuffer(raw, dtype=np.int16)
    usable = len(samples) - len(samples) % channels
    frames = samples[:usable].reshape(-1, channels)
    peaks = np.max(np.abs(frames.astype(np.int32)), axis=1) if len(frames) else np.array([])
    hits = np.flatnonzero(peaks > onset)
    start = int(hits[0]) if len(hits) else 0
    start = min(start, max(0, len(frames) - int(rate * minimum_seconds)))
    fd, path = tempfile.mkstemp(prefix='recognition_', suffix='.wav', dir=directory)
    os.close(fd)
    try:
        with wave.open(path, 'wb') as recording:
            recording.setnchannels(channels)
            recording.setsampwidth(2)
            recording.setframerate(rate)
            recording.writeframes(frames[start:].tobytes())
        return recognize(path), start / rate
    finally:
        os.unlink(path)
