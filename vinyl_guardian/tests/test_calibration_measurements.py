import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
import zipfile
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calibration_measurements import NAMES, capture_gain, export_measurements, save_capture_gain
from calibration_quality import assess_calibration
from detector import GuardianDetector


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.raw = np.tile(np.arange(-1000, 1048, dtype=np.int16), 3).tobytes()
        for name in NAMES:
            with wave.open(str(self.root / name), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(44100)
                output.writeframes(self.raw)

    def tearDown(self):
        self.temp.cleanup()

    def test_export_replays_exact_live_frames_without_audio_or_options(self):
        (self.root / 'options.json').write_text('{"password":"secret"}')
        save_capture_gain(self.root, 31)
        archive = zipfile.ZipFile(io.BytesIO(export_measurements(self.root, self.root)))
        self.assertFalse(any(name.endswith('.wav') for name in archive.namelist()))
        self.assertNotIn('options.json', archive.namelist())
        self.assertEqual(json.loads(archive.read('measurements.json'))['capture_gain'], 31)
        live, replay = GuardianDetector({}, channels=1), GuardianDetector({}, channels=1)
        rows = csv.DictReader(io.StringIO(archive.read('calib_off_floor.csv').decode()))
        for index, row in enumerate(rows):
            time = float(row.pop('time'))
            count = int(row.pop('sample_count'))
            measured = {key: float(value) for key, value in row.items()}
            self.assertEqual(live.update_pcm(self.raw[index*4096:(index+1)*4096], time),
                             replay.update_features(measured, time, count))

    def test_capture_manifest_is_invalidated_when_a_recording_changes(self):
        save_capture_gain(self.root, 31)
        self.assertEqual(capture_gain(self.root, self.root), 31)
        with (self.root / NAMES[0]).open('ab') as handle:
            handle.write(b'changed')
        self.assertIsNone(capture_gain(self.root, self.root))

    def test_rejected_original_candidate_preserves_gain_not_later_reuse(self):
        profiles = self.root / 'profiles'
        profiles.mkdir()
        created = max((self.root / name).stat().st_mtime for name in NAMES) + 1
        for index, gain in enumerate((31, 8)):
            (profiles / f'profile_{index}.json').write_text(json.dumps({
                'created_unix': created + index, 'accepted': False,
                'thresholds': {'mic_volume': gain},
                'metadata': {'calibration_files': dict(enumerate(NAMES))}}))
        self.assertEqual(capture_gain(self.root, self.root), 31)

    def test_quality_gate_rejects_wrong_known_record_speed(self):
        files = dict(zip(('floor', 'spinup', 'transition', 'lift', 'powerdown', 'disturbance'),
                         (str(self.root / name) for name in NAMES)))
        quiet = dict(on_fraction=0, music_fraction=0, final_status='Powered Off')
        transition = dict(on_fraction=1, music_fraction=.5, runout_fraction=.1,
                          runout_samples=[dict(label='45', estimated_rpm=45, phase_jitter_ms=0)])
        with patch('calibration_quality._replay', side_effect=[quiet, quiet, quiet, transition]):
            quality = assess_calibration(files, {'calibration_expected_runout_rpm': '33⅓'})
        self.assertEqual(quality['status'], 'weak')
        self.assertTrue(any('known 33⅓' in message for message in quality['critical']))
