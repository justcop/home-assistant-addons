import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import prepare_recording_directory, storage_diagnostics


class RecordingStorageTests(unittest.TestCase):
    def test_creates_recording_folder_and_removes_write_probe(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'recordings'
            self.assertEqual(prepare_recording_directory(str(target), (root,)), str(target))
            self.assertEqual(list(target.iterdir()), [target / "calibration_data"])
            self.assertEqual(list((target / "calibration_data").iterdir()), [])

    def test_does_not_create_missing_external_storage_parent(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'missing-drive' / 'recordings'
            with self.assertRaisesRegex(ValueError, 'Mount the storage first'):
                prepare_recording_directory(str(target), (root,))
            self.assertFalse(target.parent.exists())

    def test_rejects_relative_and_unmapped_paths(self):
        for target in ('recordings', '', '/tmp/recordings'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                prepare_recording_directory(target)

    def test_unwritable_storage_fails_without_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            with patch('storage.tempfile.NamedTemporaryFile', side_effect=PermissionError('read only')):
                with self.assertRaisesRegex(ValueError, 'Storage check failed'):
                    prepare_recording_directory(str(Path(root) / 'recordings'), (root,))

    def test_existing_calibration_folder_is_checked_independently(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'recordings'
            target.mkdir()
            (target / 'calibration_data').write_text('blocking file')
            with self.assertRaisesRegex(ValueError, 'calibration_data'):
                prepare_recording_directory(str(target), (root,))

    def test_readback_and_rename_failures_stop_startup_and_clean_probes(self):
        for operation in ('read', 'rename'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as root:
                target = Path(root) / 'recordings'
                patcher = patch('storage.Path.read_bytes', return_value=b'corrupt') if operation == 'read' else patch('storage.os.replace', side_effect=PermissionError('rename forbidden'))
                with patcher, self.assertRaisesRegex(ValueError, 'Storage check failed'):
                    prepare_recording_directory(str(target), (root,))
                self.assertEqual(list(target.iterdir()), [])

    def test_diagnostics_include_requested_resolved_paths_and_free_space(self):
        with tempfile.TemporaryDirectory() as root:
            diagnostics = '\n'.join(storage_diagnostics(str(Path(root) / 'recordings')))
            self.assertIn('Requested recording folder:', diagnostics)
            self.assertIn('Calibration WAV folder:', diagnostics)
            self.assertIn('Filesystem free space:', diagnostics)
