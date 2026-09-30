import ast
import json
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recognition_session import RecognitionSession, recognize_fragment


class StagedRecognitionTests(unittest.TestCase):
    def test_preview_once_then_longer_result_wins_in_either_order(self):
        for preview_first in (True, False):
            session = RecognitionSession()
            token = session.begin()
            self.assertFalse(session.preview_due(token, 4, 10))
            self.assertTrue(session.preview_due(token, 5, 10))
            self.assertFalse(session.preview_due(token, 6, 10))
            if preview_first:
                self.assertTrue(session.accept(token, 'RECORDING', True))
            self.assertTrue(session.accept(token, 'PROCESSING', False))
            self.assertFalse(session.accept(token, 'PROCESSING', True))

    def test_short_full_captures_and_old_sessions(self):
        session = RecognitionSession()
        token = session.begin()
        self.assertFalse(session.preview_due(token, 5, 5))
        session.invalidate()
        self.assertFalse(session.accept(token, 'RECORDING', False))
        new = session.begin()
        self.assertFalse(session.accept(token, 'PROCESSING', True))
        self.assertFalse(session.accept(new, 'IDLE', False))

    def test_unique_concurrent_wavs_cleanup_and_stereo_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            barrier = threading.Barrier(2)
            paths, outputs = [], []
            def recognize(path):
                paths.append(path)
                barrier.wait(timeout=3)
                with wave.open(path) as wav:
                    self.assertEqual(wav.getnchannels(), 2)
                    outputs.append(wav.readframes(wav.getnframes()))
                return {'title': 'match'}
            raw = np.array([[0,0],[-32768,1234],[3456,7890]], dtype=np.int16).tobytes()
            jobs = [threading.Thread(target=recognize_fragment, args=(raw,directory,1,2,1000,0,recognize)) for _ in range(2)]
            for job in jobs: job.start()
            for job in jobs: job.join()
            self.assertEqual(len(set(paths)), 2)
            self.assertEqual(outputs, [raw[4:],raw[4:]])
            self.assertFalse(list(Path(directory).glob('*.wav')))

    def test_api_failure_deletes_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            def fail(path): raise RuntimeError('API down')
            with self.assertRaises(RuntimeError):
                recognize_fragment(b'\0\0'*8,directory,4,1,1000,1,fail)
            self.assertFalse(list(Path(directory).glob('*.wav')))

    def runtime(self, result):
        # Exercise the actual worker without importing hardware/startup services.
        tree = ast.parse((Path(__file__).resolve().parents[1]/'vinyl_guardian.py').read_text())
        function = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='process_audio_background')
        session=RecognitionSession(); token=session.begin()
        publications=[]
        class MQTT:
            def publish(self,topic,value,**kw): publications.append((topic,value))
        env=dict(RecognitionSession=RecognitionSession, recognition_session=session,
                 state_lock=threading.Lock(), app_state='RECORDING', current_attempt=1,
                 current_track=None, consecutive_failures=0, wake_up_time=0, scrobble_fired=False,
                 last_scrobbled_track=None, paused_track_memory=None, experiment_harness=None,
                 RATE=4, CHANNELS=1, RECORDING_DIR='', AUDIO_ONSET_THRESHOLD=1000,
                 MIN_AUDIO_SECONDS=5, MAX_ATTEMPTS=3, CONSECUTIVE_FAILURE_TIMEOUT=1800,
                 FALLBACK_SLEEP_SECS=60, TEST_CAPTURE_MODE=False, time=time, json=json,
                 log=lambda message:None, mqtt_client=MQTT(), recognize_shazam=lambda path:None,
                 recognize_fragment=lambda *args:(result,0), get_track_duration=lambda *args:120)
        exec(compile(ast.Module(body=[function],type_ignores=[]),'worker','exec'),env)
        return env,token,publications

    def test_preview_does_not_start_scrobble_and_full_overrides_it(self):
        short=dict(title='Short',artist='Artist',album='Album',duration=120)
        env,token,pubs=self.runtime(short)
        env['process_audio_background'](b'\0\0'*20,100,token,True)
        self.assertIsNone(env['current_track'])
        self.assertEqual(env['app_state'],'RECORDING')
        long=dict(short,title='Long')
        env['recognize_fragment']=lambda *args:(long,0)
        env['process_audio_background'](b'\0\0'*40,100,token)
        self.assertEqual(env['current_track']['title'],'Long')
        self.assertEqual(env['current_track']['recognition_status'],'confirmed')
        count=len(pubs)
        env['process_audio_background'](b'\0\0'*20,100,token,True)
        self.assertEqual(len(pubs),count)

    def test_no_long_match_retries_without_scrobbling_preview(self):
        env,token,pubs=self.runtime(dict(title='Short',artist='Artist',album='Album'))
        env['process_audio_background'](b'\0\0'*20,100,token,True)
        env['recognize_fragment']=lambda *args:(None,0)
        env['process_audio_background'](b'\0\0'*40,100,token)
        self.assertEqual(env['current_attempt'],2)
        self.assertIsNone(env['current_track'])
        self.assertEqual(env['app_state'],'RECORDING')

    def test_duration_lookup_can_reset_state_without_deadlock(self):
        env,token,pubs=self.runtime(dict(title='Long',artist='Artist',album='Album'))
        def duration(*args):
            with env['state_lock']:
                env['recognition_session'].invalidate()
                env['app_state']='IDLE'
            return 120
        env['get_track_duration']=duration
        env['process_audio_background'](b'\0\0'*40,100,token)
        self.assertIsNone(env['current_track'])
        self.assertFalse(pubs)
