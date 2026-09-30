"""Independent physical sequences, including intermittent off-state hum."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calibration_replay import evaluate_sequence
from detector import GuardianDetector, RunoutRhythmDetector


def fixture():
    motor = {'rms': {'median': .0011547, 'scale': .00019478},
             'hfer': {'median': .07395499, 'scale': .01086575},
             'crest': {'median': 2.52766371, 'scale': .302385}}
    off = {'rms': {'median': .00063209, 'scale': .00044468},
           'hfer': {'median': .17615133, 'scale': .09076257},
           'crest': {'median': 3.06660521, 'scale': .58826499}}
    thresholds = dict(rms_min=.000967, rms_max=.007503, hfer_min=.04036,
                      hfer_max=.15616, crest_min=1.796, crest_max=6.533,
                      music_threshold=.007585, music_hold_threshold=.000774,
                      motor_profile=motor, motor_negative_profiles=[{'profile': off}],
                      pop_amplitude_threshold=.008, runout_crest_threshold=3,
                      calibration_expected_runout_rpm='33⅓')
    def feature(profile):
        row = {k: v['median'] for k,v in profile.items()}
        row.update(music_rms=.0001, peak=.002, zcr=.01)
        return row
    return thresholds, feature(motor), feature(off)


class SequenceTests(unittest.TestCase):
    def recordings(self):
        thresholds, motor, off = fixture()
        def stage(duration, generate):
            return dict(rate=1000, chunk=50, features=[generate(i, i/20) for i in range(duration*20)])
        def transition(index, time):
            if time < 25:
                return dict(motor, music_rms=.02, peak=.08, rms=.03, crest=2.7)
            # Quiet runout background differs from needle-up motor; one click
            # every 36 chunks is a physical 33⅓ RPM revolution.
            if (index-500) % 36 == 0:
                return dict(off, peak=.012, crest=12, rms=.001, music_rms=.0001)
            return off
        recordings = dict(
            floor=stage(30, lambda i,t: motor if i%20 == 0 else off),
            spinup=stage(35, lambda i,t: off if t<4 else motor),
            transition=stage(55, transition), lift=stage(30, lambda i,t: motor),
            powerdown=stage(35, lambda i,t: motor if t<2 or i%20==0 else off),
            disturbance=stage(30, lambda i,t: off))
        return thresholds, recordings

    def test_all_physical_stages_pass_with_sparse_off_state_motor_clones(self):
        thresholds, recordings = self.recordings()
        result = evaluate_sequence(recordings, thresholds)
        self.assertTrue(result['passed'], result['checks'])
        self.assertEqual(len(result['checks']), 9)
        self.assertLess(result['stages']['powerdown']['first_off_seconds'], 12)

    def test_sequence_gate_catches_power_interruption_even_with_final_runout(self):
        thresholds, recordings = self.recordings()
        thresholds['runout_power_support'] = False
        result = evaluate_sequence(recordings, thresholds)
        self.assertFalse(result['checks']['power_continuous_after_music'])
        self.assertTrue(result['checks']['runout_matches_record_speed'])
        self.assertFalse(result['passed'])

    def test_provisional_rhythm_cannot_switch_on_an_off_turntable(self):
        thresholds, motor, off = fixture()
        detector = GuardianDetector(thresholds, rate=1000)
        detector.last_music_time = 0
        detector.runout.last_support = 3
        detector.runout.last_candidate_time = 0
        for i in range(1, 40):
            frame = detector.update_features(off, i/20, 50)
            self.assertFalse(frame['turntable_on'])
            self.assertFalse(frame['runout_locked'])

    def test_provisional_support_expires_after_clicks_stop(self):
        thresholds, motor, off = fixture()
        detector = GuardianDetector(thresholds, rate=1000)
        detector.turntable_on = True
        detector.motor_confidence = 1
        detector.last_music_time = 0
        detector.runout.last_support = 3
        detector.runout.last_candidate_time = 0
        for i in range(1, 301):
            frame = detector.update_features(off, i/20, 50)
        self.assertFalse(frame['turntable_on'])
        self.assertFalse(frame['runout_locked'])

    def test_isolated_loud_off_state_spikes_do_not_activate_music_or_power(self):
        thresholds, motor, off = fixture()
        detector = GuardianDetector(thresholds, rate=1000)
        for i in range(600):
            row = dict(off, rms=.005, peak=.05, crest=10, music_rms=.01) if i%20==0 else off
            frame = detector.update_features(row, i/20, 50)
            self.assertFalse(frame['turntable_on'])
            self.assertFalse(frame['music_active'])

    def test_both_rpm_locks_tolerate_one_missing_click_and_small_phase_jitter(self):
        for label, period in (('33⅓', 1.8), ('45', 60/45)):
            detector = RunoutRhythmDetector()
            for i in range(12):
                if i == 3:
                    continue
                detector.update(5+i*period+(.02 if i%2 else -.02), True, .01, True, False)
            self.assertTrue(detector.locked)
            self.assertEqual(detector.rpm_label, label)
