import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import calibration_control as control
from calibration_capture import capture_bytes


class CalibrationFlowTests(unittest.TestCase):
    def tearDown(self):
        control.configure(None)

    def test_requires_new_confirmation_at_each_stage(self):
        published = threading.Event()
        control.configure(lambda message: published.set())
        self.assertFalse(control.confirm())
        for step in ('motor on', 'needle lift'):
            published.clear()
            worker = threading.Thread(target=control.wait_for_confirmation, args=(step, lambda _: None))
            worker.start()
            self.assertTrue(published.wait(1))
            self.assertTrue(worker.is_alive())
            self.assertTrue(control.confirm())
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertFalse(control.confirm())

    def test_standalone_calibration_fails_with_actionable_message(self):
        control.configure(None)
        with self.assertRaisesRegex(RuntimeError, 'through the add-on'):
            control.wait_for_confirmation('test')

    def test_open_failure_aborts_instead_of_returning_empty_audio(self):
        with self.assertRaisesRegex(RuntimeError, 'Cannot open audio input'):
            capture_bytes(Mock(side_effect=OSError('no input')), 1, 10, 2)

    def test_records_complete_frames_and_closes_device(self):
        device = Mock()
        device.read.side_effect = [(2, b'\0' * 8), (2, b'\0' * 8)]
        self.assertEqual(len(capture_bytes(lambda: device, 1, 4, 2)), 16)
        device.close.assert_called_once()

    def test_incomplete_frames_abort_and_close(self):
        device = Mock(); device.read.return_value = (2, b'\0')
        with self.assertRaisesRegex(RuntimeError, 'incomplete PCM'):
            capture_bytes(lambda: device, 1, 4, 2)
        device.close.assert_called_once()

    def test_silent_capture_timeout_aborts_and_closes(self):
        device = Mock(); device.read.return_value = (0, b'')
        with self.assertRaisesRegex(RuntimeError, 'no samples'):
            capture_bytes(lambda: device, 1, 4, 2, clock=iter([0, 6]).__next__)
        device.close.assert_called_once()
