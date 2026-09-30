import json
from pathlib import Path
import re
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import calibration_control as control
from calibration_web import make_server


class CalibrationWebTests(unittest.TestCase):
    def setUp(self):
        control.begin(True)
        control.configure(lambda message: None)
        self.server = make_server('127.0.0.1', 0, allowed_peer='127.0.0.1')
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        with urlopen(self.base + '/') as response:
            self.page = response.read().decode()
        self.token = re.search(r"screen_token\|\|'([^']+)", self.page).group(1)

    def tearDown(self):
        control.confirm()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(1)
        control.configure(None)

    def request(self, path, payload=None, token=None):
        if payload is None:
            request = Request(self.base + path)
        else:
            request = Request(self.base + path, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json', 'X-Calibration-Token': token or self.token})
        with urlopen(request, timeout=2) as response:
            return json.load(response)

    def waiting(self):
        ready = threading.Event()
        worker = threading.Thread(target=control.wait_for_confirmation, args=('Prepare the turntable', lambda message: ready.set()), daemon=True)
        worker.start()
        self.assertTrue(ready.wait(1))
        return worker, control.snapshot()['step_id']

    def test_instruction_logs_and_valid_confirmation_share_the_same_state(self):
        worker, step = self.waiting()
        control.append_log('Recording <script> is shown as plain text')
        state = self.request('/api/state')
        self.assertTrue(state['waiting'])
        self.assertIn('Prepare the turntable', state['instruction'])
        self.assertEqual(state['logs'][-1]['message'], 'Recording <script> is shown as plain text')
        accepted = self.request('/api/continue', {'step_id': step})
        self.assertFalse(accepted['waiting'])
        worker.join(1)
        self.assertFalse(worker.is_alive())

    def test_double_click_and_stale_tab_cannot_confirm_the_next_step(self):
        worker, previous = self.waiting()
        self.request('/api/continue', {'step_id': previous})
        worker.join(1)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/continue', {'step_id': previous})
        self.assertEqual(error.exception.code, 409)
        next_worker, next_step = self.waiting()
        with self.assertRaises(HTTPError) as error:
            self.request('/api/continue', {'step_id': previous})
        self.assertEqual(error.exception.code, 409)
        self.assertTrue(control.snapshot()['waiting'])
        self.request('/api/continue', {'step_id': next_step})
        next_worker.join(1)

    def test_missing_screen_token_cannot_continue(self):
        worker, step = self.waiting()
        with self.assertRaises(HTTPError) as error:
            self.request('/api/continue', {'step_id': step}, token='wrong')
        self.assertEqual(error.exception.code, 403)
        self.assertTrue(worker.is_alive())
        control.confirm(step); worker.join(1)

    def test_reopening_screen_does_not_restart_or_consume_waiting_step(self):
        worker, step = self.waiting()
        with urlopen(self.base + '/') as response:
            response.read()
        self.assertEqual(self.request('/api/state')['step_id'], step)
        self.assertTrue(worker.is_alive())
        control.confirm(step); worker.join(1)

    def test_completed_and_failed_states_disable_continue(self):
        for phase in ('complete', 'failed'):
            control.set_status('Finished or stopped', phase=phase)
            state = self.request('/api/state')
            self.assertEqual(state['phase'], phase)
            self.assertFalse(state['waiting'])

    def test_production_server_denies_non_ingress_peers(self):
        server = make_server('127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(f'http://127.0.0.1:{server.server_port}/', timeout=2)
            self.assertEqual(error.exception.code, 403)
        finally:
            server.shutdown(); server.server_close(); thread.join(1)
