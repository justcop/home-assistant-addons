import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from telemetry import DatasetCollector, FeatureExtractor, pcm16_channels, stereo_features


class StereoTelemetryTests(unittest.TestCase):
    def test_dual_mono_is_obvious_in_channel_metrics(self):
        mono = np.array([1000, -2000, 3000, -4000, 2500, -1500], dtype=np.int16)
        interleaved = np.column_stack((mono, mono)).reshape(-1).tobytes()
        channels = pcm16_channels(interleaved, 2)
        features = stereo_features(channels)

        self.assertAlmostEqual(features["stereo_correlation"], 1.0, places=6)
        self.assertAlmostEqual(features["stereo_side_rms"], 0.0, places=8)
        self.assertAlmostEqual(features["stereo_difference_rms"], 0.0, places=8)
        self.assertAlmostEqual(features["stereo_identical_fraction"], 1.0, places=6)

    def test_different_channels_show_side_energy(self):
        left = np.array([1000, -1000, 2000, -2000], dtype=np.int16)
        right = np.array([-1000, 1000, -2000, 2000], dtype=np.int16)
        interleaved = np.column_stack((left, right)).reshape(-1).tobytes()
        channels = pcm16_channels(interleaved, 2)
        features = stereo_features(channels)

        self.assertGreater(features["stereo_side_rms"], 0.0)
        self.assertGreater(features["stereo_difference_rms"], 0.0)


class SpectralTelemetryTests(unittest.TestCase):
    def test_sine_wave_reports_expected_dominant_frequency(self):
        rate = 44100
        n = 2048
        frequency = 440.0
        t = np.arange(n, dtype=np.float32) / rate
        audio = 0.2 * np.sin(2.0 * np.pi * frequency * t)

        features = FeatureExtractor(rate).extract(audio)

        self.assertLess(abs(features["dominant_frequency_hz"] - frequency), 30.0)
        self.assertGreater(features["autocorr_periodicity"], 0.5)
        self.assertLess(abs(features["autocorr_frequency_hz"] - frequency), 30.0)

    def test_noise_has_broader_flatter_spectrum_than_sine(self):
        rate = 44100
        rng = np.random.default_rng(123)
        n = 2048
        t = np.arange(n, dtype=np.float32) / rate
        sine = 0.2 * np.sin(2.0 * np.pi * 440.0 * t)
        noise = rng.normal(0.0, 0.2, n).astype(np.float32)

        sine_features = FeatureExtractor(rate).extract(sine)
        noise_features = FeatureExtractor(rate).extract(noise)

        self.assertGreater(
            noise_features["spectral_flatness"],
            sine_features["spectral_flatness"],
        )
        self.assertGreater(
            noise_features["spectral_entropy"],
            sine_features["spectral_entropy"],
        )


class DatasetCollectorTests(unittest.TestCase):
    def test_collector_writes_feature_csv(self):
        rate = 44100
        channels = 2
        chunk = 2048
        samples = np.zeros((chunk, channels), dtype=np.int16).tobytes()

        with tempfile.TemporaryDirectory() as tmp:
            collector = DatasetCollector(
                tmp,
                rate=rate,
                channels=channels,
                chunk=chunk,
                enabled=True,
                label="known_off",
                raw_audio=False,
                feature_interval_sec=0.05,
            )
            frame = {
                "status": "Powered Off",
                "turntable_on": False,
                "music_active": False,
                "motor_confidence": 0.0,
                "music_confidence": 0.0,
                "runout_locked": False,
                "runout_confidence": 0.0,
                "runout_support": 0,
                "is_pop_candidate": False,
                "hfer": 0.0,
                "music_rms": 0.0,
            }
            collector.observe(samples, 1000.0, frame, "IDLE", None)
            collector.close()

            self.assertEqual(collector.rows_written, 1)
            self.assertTrue(
                os.path.exists(os.path.join(collector.session_dir, "features.csv"))
            )


if __name__ == "__main__":
    unittest.main()
