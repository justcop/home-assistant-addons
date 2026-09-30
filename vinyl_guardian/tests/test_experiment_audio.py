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
