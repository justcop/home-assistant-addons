import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from gain_search import find_input_gain


class GainSearchTests(unittest.TestCase):
    def search(self, model):
        volumes = []
        def measure(seconds):
            return model(volumes[-1], seconds)
        result = find_input_gain(volumes.append, measure, log=lambda _: None)
        return result, volumes

    def test_midpoints_jump_directly_to_working_gain(self):
        gain, volumes = self.search(lambda volume, seconds: volume * 0.009)
        self.assertEqual(gain, 75)
        self.assertEqual(volumes, [50, 75])

    def test_can_reach_both_physical_endpoints(self):
        for slope, expected in ((0.005, 100), (0.6, 1)):
            gain, volumes = self.search(lambda volume, seconds: volume * slope)
            self.assertEqual(gain, expected)
            self.assertLessEqual(len(volumes), 7)

    def test_verification_spike_uses_midpoint_not_one_percent_backoff(self):
        gain, volumes = self.search(lambda volume, seconds: volume * (0.012 if seconds == 3 else 0.018))
        self.assertEqual(volumes[:2], [50, 25])
        self.assertLessEqual(len(volumes), 7)
        self.assertTrue(0.50 <= gain * 0.018 <= 0.85)

    def test_silent_and_clipped_inputs_fail_within_seven_settings(self):
        for peak, message in ((0, 'too quiet'), (1, 'too loud')):
            volumes = []
            with self.assertRaisesRegex(RuntimeError, message):
                find_input_gain(volumes.append, lambda seconds: peak, log=lambda _: None)
            self.assertLessEqual(len(volumes), 7)
            self.assertEqual(volumes[-1], 100 if peak == 0 else 1)

    def test_invalid_audio_measurement_is_rejected(self):
        for peak in (float('nan'), float('inf'), -1):
            with self.assertRaisesRegex(RuntimeError, 'Invalid audio level'):
                self.search(lambda volume, seconds: peak)

    def test_reported_24_to_25_percent_gap_accepts_verified_24(self):
        peaks = {50: 1.000, 25: 0.879, 12: 0.030, 18: 0.099,
                 21: 0.160, 23: 0.210, 24: 0.234}
        durations = []
        volumes = []
        def measure(seconds):
            durations.append(seconds)
            return peaks[volumes[-1]]
        gain = find_input_gain(volumes.append, measure, log=lambda _: None)
        self.assertEqual(gain, 24)
        self.assertEqual(volumes, [50, 25, 12, 18, 21, 23, 24])
        self.assertEqual(durations, [3.0] * 7 + [10.0])

    def test_fallback_clipping_uses_midpoints_of_previously_safe_candidates(self):
        peaks = {50: 1.000, 25: 0.879, 12: 0.030, 18: 0.099,
                 21: 0.160, 23: 0.210, 24: 0.234}
        checks = []
        def model(volume, seconds):
            if seconds == 10:
                checks.append(volume)
                return 0.95 if volume >= 23 else 0.4
            return peaks[volume]
        gain, volumes = self.search(model)
        self.assertEqual(gain, 21)
        self.assertEqual(checks, [24, 18, 21, 23])
        self.assertLessEqual(len(set(volumes)), 7)
        self.assertEqual(volumes[-1], gain)

    def test_low_but_useful_input_at_maximum_gain_is_accepted(self):
        gain, volumes = self.search(lambda volume, seconds: volume * 0.002)
        self.assertEqual(gain, 100)
        self.assertLessEqual(len(set(volumes)), 7)

    def test_fallback_is_not_accepted_without_a_long_verification(self):
        peaks = {50: 1.000, 25: 0.879, 12: 0.030, 18: 0.099,
                 21: 0.160, 23: 0.210, 24: 0.234}
        with self.assertRaisesRegex(RuntimeError, 'too quiet during verification'):
            self.search(lambda volume, seconds: peaks[volume] if seconds == 3 else 0)
