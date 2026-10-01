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
    def test_all_ladder_stages_become_due_once(self):
        session = RecognitionSession()
        token = session.begin()
        self.assertEqual(session.due_stages(token, 2.9), [])
        self.assertEqual(session.due_stages(token, 3.0), [3])
        self.assertEqual(session.due_stages(token, 5.1), [5])
        self.assertEqual(session.due_stages(token, 21), [10, 20])
        self.assertEqual(session.due_stages(token, 30), [30])
        self.assertEqual(session.due_stages(token, 60), [])

    def test_later_success_supersedes_earlier_even_if_results_return_out_of_order(self):
        session = RecognitionSession()
        token = session.begin()
        session.due_stages(token, 30)
        early = {"title": "Early"}
        later = {"title": "Later"}

        out = session.record_result(token, "PROCESSING", 20, later, 0.2)
        self.assertTrue(out["display"])
        self.assertFalse(out["finalize"])
        out = session.record_result(token, "PROCESSING", 3, early, 0.1)
        self.assertFalse(out["display"])
        self.assertEqual(out["best_match"]["title"], "Later")

        session.record_result(token, "PROCESSING", 5, None)
        session.record_result(token, "PROCESSING", 10, None)
        out = session.record_result(token, "PROCESSING", 30, None)
        self.assertTrue(out["finalize"])
        self.assertEqual(out["best_match"]["title"], "Later")
        self.assertEqual(out["best_stage"], 20)

    def test_finalization_waits_for_every_outstanding_stage(self):
        session = RecognitionSession()
        token = session.begin()
        session.due_stages(token, 30)
        for stage in (3, 5, 10, 30):
            out = session.record_result(token, "PROCESSING", stage, None)
            self.assertFalse(out["finalize"])
        out = session.record_result(token, "PROCESSING", 20, {"title": "Best"})
        self.assertTrue(out["finalize"])
        self.assertEqual(out["best_stage"], 20)

    def test_old_session_results_are_rejected(self):
        session = RecognitionSession()
        token = session.begin()
        session.due_stages(token, 3)
        session.invalidate()
        out = session.record_result(token, "RECORDING", 3, {"title": "stale"})
        self.assertFalse(out["accepted"])

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
                return {"title": "match"}

            raw = np.array([[0, 0], [-32768, 1234], [3456, 7890]], dtype=np.int16).tobytes()
            jobs = [
                threading.Thread(
                    target=recognize_fragment,
                    args=(raw, directory, 1, 2, 1000, 0, recognize),
                )
                for _ in range(2)
            ]
            for job in jobs:
                job.start()
            for job in jobs:
                job.join()
            self.assertEqual(len(set(paths)), 2)
            self.assertEqual(outputs, [raw[4:], raw[4:]])
            self.assertFalse(list(Path(directory).glob("*.wav")))

    def test_api_failure_deletes_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            def fail(path):
                raise RuntimeError("API down")

            with self.assertRaises(RuntimeError):
                recognize_fragment(b"\0\0" * 8, directory, 4, 1, 1000, 1, fail)
            self.assertFalse(list(Path(directory).glob("*.wav")))

    def runtime(self, results):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "vinyl_guardian.py").read_text())
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "process_audio_background"
        )
        session = RecognitionSession()
        token = session.begin()
        session.due_stages(token, 30)
        publications = []
        result_iter = iter(results)

        class MQTT:
            def publish(self, topic, value, **kwargs):
                publications.append((topic, value))

        env = dict(
            recognition_session=session,
            state_lock=threading.Lock(),
            app_state="PROCESSING",
            current_attempt=1,
            current_track=None,
            consecutive_failures=0,
            wake_up_time=0,
            scrobble_fired=False,
            last_scrobbled_track=None,
            paused_track_memory=None,
            experiment_harness=None,
            RATE=4,
            CHANNELS=1,
            RECORDING_DIR="",
            AUDIO_ONSET_THRESHOLD=1000,
            MIN_AUDIO_SECONDS=5,
            MAX_ATTEMPTS=3,
            CONSECUTIVE_FAILURE_TIMEOUT=1800,
            FALLBACK_SLEEP_SECS=60,
            TEST_CAPTURE_MODE=False,
            time=time,
            json=json,
            os=__import__("os"),
            log=lambda message: None,
            mqtt_client=MQTT(),
            recognize_shazam=lambda path: None,
            recognize_fragment=lambda *args: (next(result_iter), 0),
            get_track_duration=lambda *args: 120,
        )
        exec(compile(ast.Module(body=[function], type_ignores=[]), "worker", "exec"), env)
        return env, token, publications

    def test_early_match_displays_but_only_completed_ladder_confirms(self):
        early = dict(title="Early", artist="Artist", album="Album", duration=120)
        later = dict(title="Later", artist="Artist", album="Album", duration=120)
        env, token, pubs = self.runtime([early, None, later, None, later])

        for stage in (3, 5, 10, 20):
            env["process_audio_background"](b"\0\0" * (stage * 4), 100, token, stage)
            self.assertIsNone(env["current_track"])
            self.assertEqual(env["app_state"], "PROCESSING")

        env["process_audio_background"](b"\0\0" * (30 * 4), 100, token, 30)
        self.assertEqual(env["current_track"]["title"], "Later")
        self.assertEqual(env["current_track"]["recognition_status"], "confirmed")
        self.assertEqual(env["current_track"]["recognition_stage_seconds"], 30)
        provisional = [
            json.loads(value)
            for topic, value in pubs
            if topic == "vinyl_guardian/attributes"
            and json.loads(value).get("recognition_status") == "provisional"
        ]
        self.assertEqual(provisional[0]["recognition_stage_seconds"], 3)
        self.assertTrue(any(item["recognition_stage_seconds"] >= 10 for item in provisional))

    def test_if_30_second_stage_fails_best_earlier_match_is_confirmed(self):
        ten = dict(title="Ten", artist="Artist", album="Album", duration=120)
        env, token, pubs = self.runtime([None, None, ten, None, None])
        for stage in (3, 5, 10, 20, 30):
            env["process_audio_background"](b"\0\0" * (stage * 4), 100, token, stage)
        self.assertEqual(env["current_track"]["title"], "Ten")
        self.assertEqual(env["current_track"]["recognition_stage_seconds"], 10)

    def test_complete_failure_retries_from_idle_for_fresh_session(self):
        env, token, pubs = self.runtime([None, None, None, None, None])
        for stage in (3, 5, 10, 20, 30):
            env["process_audio_background"](b"\0\0" * (stage * 4), 100, token, stage)
        self.assertEqual(env["current_attempt"], 2)
        self.assertIsNone(env["current_track"])
        self.assertEqual(env["app_state"], "IDLE")


if __name__ == "__main__":
    unittest.main()
