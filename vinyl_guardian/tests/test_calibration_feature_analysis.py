import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
import zipfile
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calibration_feature_analysis import measurement_rows, rank_pair, analyse_features
from calibration_measurements import NAMES, export_measurements


class ExtendedMeasurementTests(unittest.TestCase):
    def test_frequency_band_and_stereo_measurements_come_from_audio(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'tone.wav')
            x = (np.sin(2*np.pi*100*np.arange(4096)/44100)*1000).astype(np.int16)
            with wave.open(path, 'wb') as output:
                output.setnchannels(2); output.setsampwidth(2); output.setframerate(44100)
                output.writeframes(np.column_stack((x,x)).tobytes())
            row = next(measurement_rows(path))
            self.assertGreater(row['band_60_120'], .5)
            self.assertGreater(row['stereo_correlation'], .99)
            self.assertIn('spectral_flux', row)
            self.assertIn('autocorr_periodicity', row)
            self.assertIn('subframe_rms_cv', row)
            self.assertIn('detector_hfer', row)

    def test_threshold_training_cannot_peek_at_later_validation_blocks(self):
        positive = {'motor': [{'signal': 1.0}]*6 + [{'signal': 0.0}]*4}
        negative = {'off': [{'signal': 0.0}]*10}
        ranking = rank_pair(positive, negative)[0]
        self.assertEqual(ranking['threshold'], .5)
        self.assertEqual(ranking['held_out_balanced_accuracy'], .5)
        self.assertEqual(ranking['worst_recording_accuracy'], 0)

    def test_extended_report_and_csv_are_included_in_measurements_download(self):
        with tempfile.TemporaryDirectory() as root:
            for name in NAMES:
                with wave.open(str(Path(root)/name), 'wb') as output:
                    output.setnchannels(1); output.setsampwidth(2); output.setframerate(44100)
                    output.writeframes(b'\0\0'*4096)
            files = dict(zip(('floor','spinup','transition','lift','powerdown','disturbance'),
                             (str(Path(root)/name) for name in NAMES)))
            report = analyse_features(files, Path(root)/'calibration_measurements')
            self.assertTrue(report['observational_only'])
            self.assertEqual(report['measurement_count'], 51)
            archive = zipfile.ZipFile(io.BytesIO(export_measurements(root,root)))
            exported = json.loads(archive.read('extended/calibration_feature_analysis.json'))
            self.assertEqual(exported['measurement_count'],51)
            self.assertIn('extended/extended_calib_power_down.csv', archive.namelist())
