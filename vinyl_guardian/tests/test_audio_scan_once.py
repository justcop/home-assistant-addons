import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audio_scan_once import StartupScanGate, reset_scan_options


class ScanOnceTests(unittest.TestCase):
    def test_success_is_remembered_and_off_rearms_it(self):
        with tempfile.TemporaryDirectory() as root:
            gate = StartupScanGate(root)
            self.assertTrue(gate.should_run(True))
            gate.complete('old_input')
            restored = StartupScanGate(root)
            self.assertFalse(restored.should_run(True))
            self.assertEqual(restored.effective_source('old_input'), 'auto')
            self.assertEqual(restored.effective_source('new_manual_input'), 'new_manual_input')
            self.assertFalse(restored.should_run(False))
            self.assertTrue(restored.should_run(True))

    def test_options_reset_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'options.json'
            path.write_text(json.dumps({'recording_directory': '/media/records', 'code_branch': 'experiment', 'lastfm_password': 'private', 'audio_scan_on_start': True, 'audio_source': 'old'}))
            with patch.dict(os.environ, {'SUPERVISOR_TOKEN': 'token'}), patch('audio_scan_once.urllib.request.urlopen', return_value=io.BytesIO(b'{"result":"ok"}')) as request:
                self.assertTrue(reset_scan_options(path))
                body = json.loads(request.call_args.args[0].data)['options']
                self.assertFalse(body['audio_scan_on_start'])
                self.assertEqual(body['audio_source'], 'auto')
                self.assertEqual(body['lastfm_password'], 'private')
                self.assertNotIn('code_branch', body)
                self.assertEqual(body['recording_directory'], '/media/records')

    def test_missing_supervisor_token_leaves_persistent_gate_as_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(reset_scan_options('/does/not/exist'))
