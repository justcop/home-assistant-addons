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
