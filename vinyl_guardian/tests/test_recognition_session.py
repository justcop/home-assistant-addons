import tempfile
import threading
import unittest
import wave
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recognition_session import RecognitionSession, recognize_fragment


def match(title, adamid):
    return {
        "title": title,
        "artist": "Artist",
        "album": "Album",
        "adamid": adamid,
    }


class InitialRecognitionTests(unittest.TestCase):
    def test_different_catalogue_releases_agree_without_conflicts(self):
        session = RecognitionSession()
        token = session.begin()
        session.due_stages(token, 5)
        session.record_result(token, "RECORDING", 3, match("Because", "1"))
        result = session.record_result(token, "RECORDING", 5, match("Because (Remastered 2009)", "2"))
        self.assertTrue(result["finalize"])
        self.assertEqual(result["conflicts"], 0)
        self.assertEqual(result["support"], 2)

    def test_initial_stages_are_only_3_5_10(self):
        session = RecognitionSession()
        self.assertEqual(session.stages, (3, 5, 10))
        token = session.begin()
        self.assertEqual(session.due_stages(token, 2.9), [])
        self.assertEqual(session.due_stages(token, 3), [3])
        self.assertEqual(session.due_stages(token, 5), [5])
        self.assertEqual(session.due_stages(token, 10), [10])
        self.assertEqual(session.due_stages(token, 30), [])

    def test_3_and_5_agreement_confirms_early(self):
        session = RecognitionSession()
        token = session.begin()
        session.due_stages(token, 5)
        a = match("A", "1")
        first = session.record_result(token, "RECORDING", 3, a)
        self.assertFalse(first["finalize"])
        second = session.record_result(token, "RECORDING", 5, a)
        self.assertTrue(second["finalize"])
        self.assertEqual(second["confidence"], "high")
        self.assertEqual(second["support"], 2)
        self.assertEqual(second["conflicts"], 0)
        self.assertEqual(second["best_match"]["title"], "A")

    def test_conflicting_fast_answers_can_resolve_at_10_but_remain_medium(self):
        session = RecognitionSession()
        token = session.begin()
        session.due_stages(token, 10)
        a, b = match("A", "1"), match("B", "2")
        session.record_result(token, "PROCESSING", 3, a)
        session.record_result(token, "PROCESSING", 5, b)
        result = session.record_result(token, "PROCESSING", 10, a)
        self.assertTrue(result["finalize"])
        self.assertEqual(result["best_match"]["title"], "A")
        self.assertEqual(result["confidence"], "medium")
        self.assertEqual(result["support"], 2)
        self.assertEqual(result["conflicts"], 1)

    def test_single_success_is_usable_but_low_confidence(self):
        session = RecognitionSession()
        token = session.begin()
        session.due_stages(token, 10)
        session.record_result(token, "PROCESSING", 3, None)
        session.record_result(token, "PROCESSING", 5, None)
        result = session.record_result(token, "PROCESSING", 10, match("A", "1"))
        self.assertTrue(result["finalize"])
        self.assertEqual(result["confidence"], "low")
        self.assertEqual(result["support"], 1)

    def test_out_of_order_results_wait_for_requested_initial_windows(self):
        session = RecognitionSession()
        token = session.begin()
        session.due_stages(token, 10)
        a = match("A", "1")
        ten = session.record_result(token, "PROCESSING", 10, a)
        self.assertFalse(ten["finalize"])
        session.record_result(token, "PROCESSING", 3, a)
        five = session.record_result(token, "PROCESSING", 5, a)
        self.assertTrue(five["finalize"])
        self.assertEqual(five["confidence"], "high")

    def test_old_session_result_is_rejected(self):
        session = RecognitionSession()
        token = session.begin()
        session.due_stages(token, 3)
        session.invalidate()
        self.assertFalse(
            session.record_result(token, "RECORDING", 3, match("stale", "9"))["accepted"]
        )

    def test_recognition_fragment_rejects_more_than_ten_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = np.zeros(11 * 4, dtype=np.int16).tobytes()
            with self.assertRaises(ValueError):
                recognize_fragment(raw, directory, 4, 1, 1000, 3, lambda path: None)

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


if __name__ == "__main__":
    unittest.main()
