import json
import os
import sys
import tempfile
import unittest
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from experiment import HardwareHealthMonitor, ShadowDetectorSuite
from replay_lab import replay
from audio_source import choose_best_candidate, score_pcm




class AudioSourceScoringTests(unittest.TestCase):
    def test_silence_is_not_a_usable_source(self):
        raw = np.zeros((44100, 2), dtype=np.int16).reshape(-1).tobytes()
        result = score_pcm(raw, rate=44100, channels=2)
        self.assertFalse(result["usable"])
        self.assertLess(result["score"], 0.1)

    def test_strong_changing_audio_is_usable(self):
        rate = 44100
        duration = 1.5
        t = np.arange(int(rate * duration), dtype=np.float32) / rate
        signal = (
            0.12 * np.sin(2 * np.pi * 220.0 * t)
            + 0.08 * np.sin(2 * np.pi * 523.25 * t)
            + 0.05 * np.sin(2 * np.pi * 1100.0 * t)
        )
        envelope = 0.65 + 0.35 * np.sin(2 * np.pi * 2.7 * t)
        mono = np.clip(signal * envelope, -0.9, 0.9)
        pcm = (mono * 32767).astype(np.int16)
        raw = np.column_stack((pcm, pcm)).reshape(-1).tobytes()

        result = score_pcm(raw, rate=rate, channels=2)
        self.assertTrue(result["usable"])
        self.assertGreater(result["score"], 0.35)

    def test_best_usable_candidate_wins(self):
        candidates = [
            {"source": "silent", "metrics": {"usable": False, "score": 0.02}},
            {"source": "weak", "metrics": {"usable": True, "score": 0.41}},
            {"source": "music", "metrics": {"usable": True, "score": 0.78}},
        ]
        winner = choose_best_candidate(candidates)
        self.assertEqual(winner["source"], "music")
        self.assertEqual(winner["confidence"], "high")


class HardwareHealthTests(unittest.TestCase):
    def test_probable_dual_mono_is_identified(self):
        rate = 44100
        chunk = 2048
        t = np.arange(chunk, dtype=np.float32) / rate
        mono = (0.1 * np.sin(2 * np.pi * 440.0 * t) * 32767).astype(np.int16)
        data = np.column_stack((mono, mono)).reshape(-1).tobytes()

        with tempfile.TemporaryDirectory() as tmp:
            monitor = HardwareHealthMonitor(tmp, rate=rate, channels=2)
            report = {}
            for i in range(12):
                report = monitor.observe(data, 1000.0 + i * 0.55)
            self.assertEqual(report["channel_mode"], "probable_dual_mono")
            self.assertGreater(report["left_right_correlation"], 0.99)
            self.assertLess(report["side_mid_ratio"], 0.01)


class ShadowSuiteTests(unittest.TestCase):
    def test_expected_shadow_detectors_exist(self):
        suite = ShadowDetectorSuite(
            {"music_threshold": 0.002, "music_hold_threshold": 0.001},
            rate=44100,
            channels=2,
        )
        self.assertEqual(
            set(suite.detectors),
            {"music_sensitive", "profile_heavy", "power_conservative"},
        )


class ReplayLabTests(unittest.TestCase):
    def test_replay_creates_report(self):
        rate = 44100
        chunk = 2048
        with tempfile.TemporaryDirectory() as tmp:
            dataset = os.path.join(tmp, "dataset")
            os.makedirs(dataset)
            with open(os.path.join(dataset, "metadata.json"), "w") as handle:
                json.dump({"label": "known_off"}, handle)

            wav_path = os.path.join(dataset, "audio_001.wav")
            samples = np.zeros((chunk * 5, 2), dtype=np.int16)
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(samples.reshape(-1).tobytes())

            result = replay(dataset, {
                "rms_min": 0.001,
                "rms_max": 0.01,
                "hfer_min": 0.0,
                "hfer_max": 2.0,
                "crest_min": 0.0,
                "crest_max": 20.0,
                "music_threshold": 0.002,
                "music_hold_threshold": 0.001,
            })

            self.assertTrue(os.path.exists(result["json_path"]))
            self.assertTrue(os.path.exists(result["transitions_csv"]))
            self.assertEqual(result["summary"]["channels"], 2)
            self.assertIn("known_off_false_on_sec", result["summary"])


if __name__ == "__main__":
    unittest.main()
