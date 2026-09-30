import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from detector import GuardianDetector, extract_features
from telemetry import FeatureExtractor
from feature_combinations import score


class MotorCombinationLiveTests(unittest.TestCase):
    def test_pcm_and_exported_features_have_identical_motor_evidence(self):
        fields=['band_60_120','autocorr_periodicity','subframe_rms_cv']
        model=dict(features=fields, centre=[.1,.2,.3],scale=[.1,.2,.3],weight=[1,2,-1])
        thresholds=dict(motor_combination_model=model, motor_negative_profiles=[{'profile':{}}])
        live=GuardianDetector(thresholds,channels=2)
        replay=GuardianDetector(thresholds,channels=2)
        extractor=FeatureExtractor(44100)
        rng=np.random.default_rng(24)
        for i in range(20):
            samples=rng.integers(-500,500,size=2048,dtype=np.int16)
            mono=samples.astype(np.float32)/32768
            row=extractor.extract(mono)
            row.update(extract_features(mono,44100))
            raw=np.column_stack([samples,samples]).astype(np.int16).tobytes()
            actual=live.update_pcm(raw,(i+1)*2048/44100)
            expected=replay.update_features(row,(i+1)*2048/44100)
            self.assertAlmostEqual(live._profile_motor_score(live.last_features),score(model,row),places=10)
            self.assertEqual(actual['status'],expected['status'])
            self.assertAlmostEqual(actual['motor_confidence'],expected['motor_confidence'],places=10)

    def test_old_profiles_do_not_require_extended_measurements(self):
        detector=GuardianDetector({})
        self.assertIsNone(detector.motor_feature_extractor)
        frame=detector.update_pcm(b'\0\0'*4096,1)
        self.assertEqual(frame['status'],'Powered Off')
