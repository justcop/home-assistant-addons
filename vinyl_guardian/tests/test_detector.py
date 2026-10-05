import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from detector import RUNOUT_SPEEDS, RunoutRhythmDetector, GuardianDetector, pcm16_to_mono, extract_features


class StereoHandlingTests(unittest.TestCase):
    def test_interleaved_stereo_is_downmixed_before_analysis(self):
        left = np.array([1000, 2000, 3000, 4000], dtype=np.int16)
        right = np.array([-1000, 0, 1000, 2000], dtype=np.int16)
        interleaved = np.column_stack((left, right)).reshape(-1).tobytes()

        mono = pcm16_to_mono(interleaved, channels=2)
        expected = ((left.astype(np.float32) + right.astype(np.float32)) / 2.0) / 32768.0

        np.testing.assert_allclose(mono, expected, rtol=0, atol=1e-7)


class RunoutRhythmTests(unittest.TestCase):
    def test_music_immediately_clears_existing_lock_and_old_candidates(self):
        detector, t, period = self._feed_rhythm("33⅓")
        detector.update(t + .05, True, .08, True, True)
        self.assertFalse(detector.locked)
        self.assertIsNone(detector.rpm_label)
        self.assertEqual(len(detector.events), 0)
        for index in range(4):
            detector.update(t + (index + 1) * period, True, .08, True, False)
        self.assertFalse(detector.locked)

    def _feed_rhythm(self, label, jitters=None):
        detector = RunoutRhythmDetector()
        period = RUNOUT_SPEEDS[label]
        jitters = jitters or [0.0, 0.02, -0.025, 0.03, -0.01, 0.01]

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

    def test_locks_to_33_rpm_after_six_aligned_revolutions(self):
        detector, _, _ = self._feed_rhythm("33⅓")
        self.assertTrue(detector.locked)
        self.assertEqual(detector.rpm_label, "33⅓")
        self.assertGreaterEqual(detector.last_support, 6)
        self.assertIsNotNone(detector.estimated_rpm)
        self.assertLess(abs(detector.estimated_rpm - (100.0 / 3.0)), 1.5)
        self.assertIsNotNone(detector.phase_jitter_ms)
        self.assertLess(detector.phase_jitter_ms, 50.0)

    def test_locks_to_45_rpm_after_six_aligned_revolutions(self):
        detector, _, _ = self._feed_rhythm("45")
        self.assertTrue(detector.locked)
        self.assertEqual(detector.rpm_label, "45")
        self.assertGreaterEqual(detector.last_support, 6)

    def test_weak_competing_chain_cannot_corrupt_locked_rpm_estimate(self):
        detector, time, _ = self._feed_rhythm("45")
        original = detector.estimated_rpm
        detector._score_period = lambda now, peak, period, label: (
            (3, .95, [1.8, 1.8]) if label == "33⅓" else (1, 0, []))
        detector.update(time + .5, True, .08, True, False)
        self.assertTrue(detector.locked)
        self.assertEqual(detector.rpm_label, "45")
        self.assertEqual(detector.estimated_rpm, original)

    def test_four_accidental_45_hits_do_not_acquire(self):
        detector, _, _ = self._feed_rhythm("45", [0.0] * 4)
        self.assertFalse(detector.locked)
        self.assertIsNone(detector.estimated_rpm)

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


class MotorProfileTests(unittest.TestCase):
    def test_reported_profiles_distinguish_motor_and_settled_off(self):
        motor = {'rms': {'median': .0011547, 'scale': .00019478},
                 'hfer': {'median': .07395499, 'scale': .01086575},
                 'crest': {'median': 2.52766371, 'scale': .302385}}
        off = {'rms': {'median': .00063209, 'scale': .00044468},
               'hfer': {'median': .17615133, 'scale': .09076257},
               'crest': {'median': 3.06660521, 'scale': .58826499}}
        thresholds = dict(rms_min=.000967, rms_max=.007503, hfer_min=.04036,
                          hfer_max=.15616, crest_min=1.796, crest_max=6.533,
                          music_threshold=.007585, music_hold_threshold=.000774,
                          motor_profile=motor, motor_negative_profiles=[{'profile': off}])
        detector = GuardianDetector(thresholds)
        def feed(profile, start, duration):
            features = {key: value['median'] for key, value in profile.items()}
            features.update(music_rms=.0001, peak=.002, zcr=.01)
            for tick in range(duration * 20):
                detector.update_features(features, start + tick / 20)
        feed(off, 0, 30)
        self.assertFalse(detector.turntable_on)
        feed(motor, 30, 15)
        self.assertTrue(detector.turntable_on)
        feed(off, 45, 30)
        self.assertFalse(detector.turntable_on)


class QuietMusicTests(unittest.TestCase):
    def test_high_crest_sustained_music_cannot_become_runout(self):
        detector = GuardianDetector(dict(music_threshold=.002, music_hold_threshold=.001,
                                        motor_power_ceiling=.02))
        features = dict(rms=.004, music_rms=.003, sustained_music_rms=.003,
                        crest=10, peak=.04, hfer=.2, zcr=.1)
        for tick in range(400):
            frame = detector.update_features(features, tick / 20)
            self.assertFalse(frame["runout_locked"])
        self.assertTrue(frame["music_active"])
        self.assertEqual(frame["status"], "Playing")

    def test_quiet_music_recovers_after_ten_second_internal_rest(self):
        detector = GuardianDetector(dict(music_threshold=.01, music_hold_threshold=.001))
        music = dict(rms=.02, music_rms=.02, crest=2, peak=.04, hfer=.2, zcr=.1)
        silence = dict(music, rms=.001, music_rms=.0001, peak=.002)
        for tick in range(60):
            detector.update_features(music, tick / 20)
        for tick in range(60, 260):
            frame = detector.update_features(silence, tick / 20)
            self.assertFalse(frame["runout_locked"])
        for tick in range(260, 280):
            frame = detector.update_features(dict(music, music_rms=.0015), tick / 20)
        self.assertTrue(frame["music_active"])
        self.assertFalse(frame["runout_locked"])

    def test_sustained_measurement_rejects_isolated_click_energy(self):
        click = np.zeros(2048, dtype=np.float32)
        click[100] = .5
        features = extract_features(click)
        self.assertGreater(features["music_rms"], .001)
        self.assertEqual(features["sustained_music_rms"], 0)
        music = .01 * np.sin(np.arange(2048) * .5)
        self.assertGreater(extract_features(music)["sustained_music_rms"], .001)


if __name__ == "__main__":
    unittest.main()
