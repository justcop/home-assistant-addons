import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
import calibration_control as control
from calibration_capture import capture_bytes
from calibration_session import CalibrationSession


class NavigationTests(unittest.TestCase):
    def setUp(self):
        control.begin(True)
        control.configure(lambda message: None)

    def tearDown(self):
        control.configure(None)
        control.begin(False)

    def test_repeat_interrupts_capture_and_closes_device(self):
        control.set_stage(2, 'Motor startup')
        control.set_status('Recording', phase='recording')
        step = control.snapshot()['step_id']
        device = Mock()
        def read():
            self.assertTrue(control.request_navigation('repeat', step, 2))
            return 1, b'\0\0'
        device.read.side_effect = read
        with self.assertRaises(control.CalibrationNavigation) as request:
            capture_bytes(lambda: device, 1, 10, 1, checkpoint=control.checkpoint)
        self.assertEqual(request.exception.stage, 2)
        device.close.assert_called_once()
        device.read.assert_called_once()

    def test_navigation_wakes_waiting_confirmation_without_recording(self):
        ready = threading.Event(); received = []
        def wait():
            try:
                control.wait_for_confirmation('Prepare', lambda _: ready.set())
            except control.CalibrationNavigation as request:
                received.append(request.action)
        control.set_stage(1, 'Baseline')
        worker = threading.Thread(target=wait, daemon=True); worker.start()
        self.assertTrue(ready.wait(1))
        step = control.snapshot()['step_id']
        self.assertTrue(control.request_navigation('restart', step))
        self.assertFalse(control.request_navigation('restart', step))
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(received, ['restart'])
        self.assertFalse(control.confirm(step))

    def test_cannot_skip_unrecorded_stages_or_interrupt_save(self):
        control.set_stage(2, 'Motor startup')
        control.set_status('Recording', phase='recording')
        self.assertFalse(control.request_navigation('repeat', control.snapshot()['step_id'], 4))
        control.freeze_for_save()
        self.assertFalse(control.request_navigation('restart', control.snapshot()['step_id']))

    def test_repeat_preserves_earlier_recordings_and_redoes_selected_and_later(self):
        with tempfile.TemporaryDirectory() as root:
            paths = {str(index): str(Path(root) / f'{index}.wav') for index in range(1, 7)}
            session = CalibrationSession(); session.prepare(root, paths, False, 8)
            counts = [0] * 7
            def callback(index):
                def run():
                    counts[index] += 1
                    if index:
                        Path(paths[str(index)]).write_text(str(counts[index]))
                    return 50 if index == 0 else None
                return run
            callbacks = [callback(i) for i in range(7)]
            control.configure(lambda message: control.confirm())
            self.assertEqual(session.record(callbacks, lambda _: None), 50)
            original = Path(paths['2']).read_text()
            session.navigate(control.CalibrationNavigation('repeat', 3), lambda _: None)
            self.assertEqual(Path(paths['2']).read_text(), original)
            self.assertFalse(Path(paths['3']).exists())
            self.assertFalse(Path(paths['6']).exists())
            self.assertEqual(session.record(callbacks, lambda _: None), 50)
            self.assertEqual(counts, [1, 1, 1, 2, 2, 2, 2])
            session.navigate(control.CalibrationNavigation('restart'), lambda _: None)
            self.assertIsNone(session.gain)
            self.assertTrue(all(not Path(p).exists() for p in paths.values()))
            self.assertEqual(session.record(callbacks, lambda _: None), 50)
            self.assertEqual(counts, [2, 2, 2, 3, 3, 3, 3])

    def test_repeat_from_saved_audio_keeps_saved_gain_until_gain_is_repeated(self):
        with tempfile.TemporaryDirectory() as root:
            paths = {str(i): str(Path(root) / f'{i}.wav') for i in range(1, 7)}
            for path in paths.values(): Path(path).write_text('saved')
            session = CalibrationSession(); session.prepare(root, paths, True, 42)
            session.navigate(control.CalibrationNavigation('repeat', 4), lambda _: None)
            self.assertEqual(session.gain, 42)
            self.assertFalse(session.use_existing)
            self.assertEqual(session.next_stage, 4)
            self.assertTrue(Path(paths['3']).exists())
