import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from detector import RUNOUT_SPEEDS, RunoutRhythmDetector, pcm16_to_mono


class StereoHandlingTests(unittest.TestCase):
    def test_interleaved_stereo_is_downmixed_before_analysis(self):
        left = np.array([1000, 2000, 3000, 4000], dtype=np.int16)
        right = np.array([-1000, 0, 1000, 2000], dtype=np.int16)
        interleaved = np.column_stack((left, right)).reshape(-1).tobytes()

        mono = pcm16_to_mono(interleaved, channels=2)
        expected = ((left.astype(np.float32) + right.astype(np.float32)) / 2.0) / 32768.0

        np.testing.assert_allclose(mono, expected, rtol=0, atol=1e-7)


class RunoutRhythmTests(unittest.TestCase):
    def _feed_rhythm(self, label, jitters=None):
        detector = RunoutRhythmDetector()
        period = RUNOUT_SPEEDS[label]
        jitters = jitters or [0.0, 0.02, -0.025, 0.03]

        t = 5.0
        for jitter in jitters:
            t += period + jitter
            detector.update(
                t,
                is_candidate=True,
                peak=0.08,
                recently_played=True,
                music_active=False,
            )
        return detector, t, period

    def test_locks_to_33_rpm_after_four_aligned_revolutions(self):
        detector, _, _ = self._feed_rhythm("33⅓")
        self.assertTrue(detector.locked)
        self.assertEqual(detector.rpm_label, "33⅓")
        self.assertGreaterEqual(detector.last_support, 4)
        self.assertIsNotNone(detector.estimated_rpm)
        self.assertLess(abs(detector.estimated_rpm - (100.0 / 3.0)), 1.5)
        self.assertIsNotNone(detector.phase_jitter_ms)
        self.assertLess(detector.phase_jitter_ms, 50.0)

    def test_locks_to_45_rpm_after_four_aligned_revolutions(self):
        detector, _, _ = self._feed_rhythm("45")
        self.assertTrue(detector.locked)
        self.assertEqual(detector.rpm_label, "45")
        self.assertGreaterEqual(detector.last_support, 4)

    def test_will_not_acquire_runout_without_recent_music(self):
        detector = RunoutRhythmDetector()
        period = RUNOUT_SPEEDS["33⅓"]
        t = 0.0
        for _ in range(8):
            t += period
            detector.update(
                t,
                is_candidate=True,
                peak=0.08,
                recently_played=False,
                music_active=False,
            )

        self.assertFalse(detector.locked)
        self.assertIsNone(detector.rpm_label)

    def test_existing_lock_survives_one_missed_revolution(self):
        detector, t, period = self._feed_rhythm("33⅓")
        self.assertTrue(detector.locked)

        # No click on the next revolution. The following click arrives two
        # periods later and should extend the established phase lock.
        t += period * 2.0
        detector.update(
            t,
            is_candidate=True,
            peak=0.075,
            recently_played=True,
            music_active=False,
        )

        self.assertTrue(detector.locked)
        self.assertEqual(detector.rpm_label, "33⅓")
        self.assertGreater(detector.hold_until, t)


if __name__ == "__main__":
    unittest.main()
