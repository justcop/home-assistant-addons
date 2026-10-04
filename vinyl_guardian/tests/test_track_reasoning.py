import ast
import unittest
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from track_reasoning import (
    PendingScrobbleQueue,
    TrackMonitor,
    expected_end,
    identity_key,
    scrobble_identity_confident,
    scrobble_is_eligible,
)


def match(title, adamid):
    return {
        "title": title,
        "artist": "Artist",
        "album": "Album",
        "adamid": adamid,
    }


def track(title="A", adamid="1", confidence="low", start=1000.0, duration=180.0):
    item = {
        **match(title, adamid),
        "session_start_time": start,
        "start_timestamp": start,
        "duration": duration,
        "duration_known": True,
        "expected_end_time": start + duration,
        "scrobble_trigger_time": start + min(duration / 2.0, 240.0),
        "recognition_confidence": confidence,
        "recognition_verified": confidence == "high",
        "scrobble_fired": False,
    }
    item["identity_key"] = identity_key(item)
    return item


class AudioWindowTests(unittest.TestCase):
    def test_actual_recovery_handler_preserves_clock_album_and_sent_scrobble(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "vinyl_guardian.py").read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "process_tracking_audio_background")
        current = track("Because", "love", confidence="high")
        current["scrobble_fired"] = True
        current["album"] = "Love"
        monitor = TrackMonitor()
        monitor.begin_track(current)
        monitor.start_boundary(1040, "music_recovery")
        requests = monitor.due_requests(1045, current)
        logs = []
        env = dict(current_track=current, track_monitor=monitor, state_lock=threading.Lock(),
                   app_state="SLEEPING", RATE=1, CHANNELS=1, RECORDING_DIR="unused",
                   AUDIO_ONSET_THRESHOLD=0, MIN_AUDIO_SECONDS=2,
                   recognize_shazam=None, recognize_fragment=lambda *args: (match("Because (Remastered 2009)", "abbey"), 0),
                   time=SimpleNamespace(time=lambda: 1045), expected_end=expected_end,
                   _track_id=lambda t: t["title"], _publish_track=lambda t: None,
                   log=logs.append, wake_up_time=1180, scrobble_fired=True)
        exec(compile(ast.Module(body=[function], type_ignores=[]), "handler", "exec"), env)
        for request in requests:
            env[function.name](b"\0" * 10, request["start"], request["id"])
        self.assertIs(env["current_track"], current)
        self.assertEqual(current["start_timestamp"], 1000)
        self.assertEqual(current["expected_end_time"], 1180)
        self.assertEqual(current["album"], "Love")
        self.assertTrue(current["scrobble_fired"])
        self.assertTrue(env["scrobble_fired"])
        self.assertIn("quiet passage", logs[-1])

    def test_tracking_window_is_byte_clamped_to_requested_ten_seconds(self):
        source = Path(__file__).resolve().parents[1] / "vinyl_guardian.py"
        tree = ast.parse(source.read_text())
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_extract_audio_window"
        )
        env = {"RATE": 4, "CHANNELS": 1, "CHUNK": 2}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "window", "exec"), env)
        # Deliberately include one chunk beyond the nominal end; the helper
        # may select it for boundary alignment but must never upload it.
        ring = [(index * 0.5, b"\\x01\\x00" * 2) for index in range(22)]
        raw = env["_extract_audio_window"](ring, 0.0, 10.0)
        self.assertEqual(len(raw), 10 * 4 * 1 * 2)


class TrackReasoningTests(unittest.TestCase):
    def test_release_ids_and_remaster_suffixes_do_not_change_song_identity(self):
        original = identity_key(match("Because", "1"))
        for title in ("Because", "Because (Remastered 2009)", "Because - 2009 Remaster",
                      "Because [Remastered]", "Because (2009 Remastered)"):
            self.assertEqual(identity_key(match(title, "another-release")), original)
        for title in ("Because (Live)", "Because (Remix)", "Because / Get Back"):
            self.assertNotEqual(identity_key(match(title, "1")), original)
        self.assertNotEqual(identity_key(dict(match("Because", "1"), artist="Someone Else")), original)
        self.assertEqual(identity_key({"adamid": "1"}), "adamid:1")

    def test_repeated_recovery_checks_are_throttled_without_blocking_expected_end(self):
        current = track(confidence="high")
        monitor = TrackMonitor()
        monitor.begin_track(current)
        monitor.start_boundary(1040, "music_recovery")
        for request in monitor.due_requests(1045, current):
            monitor.record_result(request["id"], match("A", "different-release"))
        self.assertFalse(monitor.start_boundary(1050, "music_recovery"))
        self.assertTrue(monitor.start_boundary(1050, "expected_end", strength="strong"))

    def test_overlap_agreement_after_rest_is_not_enough_to_reset_song(self):
        current = track(confidence="high")
        monitor = TrackMonitor()
        monitor.begin_track(current)
        monitor.start_boundary(1040, "music_recovery")
        for request in monitor.due_requests(1045, current):
            self.assertIsNone(monitor.record_result(request["id"], match("X", "2"))["action"])
        fresh = monitor.due_requests(1050, current)[0]
        self.assertEqual(monitor.record_result(fresh["id"], match("A", "1"))["action"], "continuation")

    def test_weak_track_gets_fresh_10_20_and_20_30_windows(self):
        current = track(confidence="low", duration=180)
        monitor = TrackMonitor()
        monitor.begin_track(current)
        due = monitor.due_requests(1020.1, current)
        self.assertEqual([(x["name"], x["start"], x["end"]) for x in due],
                         [("verify_20", 1010.0, 1020.0)])
        due = monitor.due_requests(1030.1, current)
        self.assertEqual([(x["name"], x["start"], x["end"]) for x in due],
                         [("verify_30", 1020.0, 1030.0)])
        self.assertTrue(all(x["end"] - x["start"] <= 10.0 for x in due))

    def test_short_song_skips_verification_window_that_crosses_expected_end(self):
        current = track(confidence="low", duration=18)
        monitor = TrackMonitor()
        monitor.begin_track(current)
        self.assertEqual(monitor.due_requests(1020.1, current), [])
        self.assertEqual(monitor.due_requests(1030.1, current), [])

    def test_one_fresh_agreeing_window_verifies_weak_identity(self):
        current = track(confidence="low")
        monitor = TrackMonitor()
        monitor.begin_track(current)
        request = monitor.due_requests(1020.1, current)[0]
        result = monitor.record_result(request["id"], match("A", "1"))
        self.assertEqual(result["action"], "verified")
        self.assertEqual(result["confidence"], "high")

    def test_two_fresh_agreeing_alternatives_correct_weak_identity(self):
        current = track(confidence="low")
        monitor = TrackMonitor()
        monitor.begin_track(current)
        first = monitor.due_requests(1020.1, current)[0]
        result = monitor.record_result(first["id"], match("B", "2"))
        self.assertEqual(result["action"], "hold_ambiguous")
        second = monitor.due_requests(1030.1, current)[0]
        result = monitor.record_result(second["id"], match("B", "2"))
        self.assertEqual(result["action"], "correct_identity")
        self.assertEqual(result["match"]["title"], "B")

    def test_love_style_different_constituent_matches_do_not_rewrite_track(self):
        current = track(confidence="low")
        monitor = TrackMonitor()
        monitor.begin_track(current)
        first = monitor.due_requests(1020.1, current)[0]
        self.assertEqual(
            monitor.record_result(first["id"], match("Source X", "2"))["action"],
            "hold_ambiguous",
        )
        second = monitor.due_requests(1030.1, current)[0]
        result = monitor.record_result(second["id"], match("Source Y", "3"))
        self.assertEqual(result["action"], "hold_ambiguous")

    def test_internal_pause_same_identity_cancels_boundary(self):
        current = track(confidence="high")
        monitor = TrackMonitor()
        monitor.begin_track(current)
        monitor.start_boundary(1040.0, "music_recovery", strength="medium")
        requests = monitor.due_requests(1045.1, current)
        by_stage = {r["stage"]: r for r in requests}
        self.assertEqual(
            monitor.record_result(by_stage[3]["id"], match("A", "1"))["action"],
            None,
        )
        result = monitor.record_result(by_stage[5]["id"], match("A", "1"))
        self.assertEqual(result["action"], "continuation")
        self.assertFalse(monitor.boundary_active())

    def test_recovery_boundary_retains_last_music_time(self):
        current = track(confidence="high")
        monitor = TrackMonitor()
        monitor.begin_track(current)
        monitor.start_boundary(
            1060.0,
            "music_recovery",
            strength="medium",
            previous_end=1052.0,
        )
        requests = monitor.due_requests(1065.1, current)
        by_stage = {r["stage"]: r for r in requests}
        monitor.record_result(by_stage[3]["id"], match("B", "2"))
        result = monitor.record_result(by_stage[5]["id"], match("B", "2"))
        self.assertIsNone(result["action"])
        fresh = monitor.due_requests(1070.1, current)[0]
        self.assertEqual((fresh["start"], fresh["end"]), (1065.0, 1070.0))
        result = monitor.record_result(fresh["id"], match("B", "2"))
        self.assertEqual(result["action"], "successor")
        self.assertEqual(result["previous_end"], 1052.0)

    def test_gapless_expected_end_two_new_matches_confirm_successor(self):
        current = track(confidence="high", duration=18)
        monitor = TrackMonitor()
        monitor.begin_track(current)
        monitor.start_boundary(expected_end(current), "expected_end", strength="strong")
        requests = monitor.due_requests(1023.1, current)
        by_stage = {r["stage"]: r for r in requests}
        first = monitor.record_result(by_stage[3]["id"], match("B", "2"))
        self.assertIsNone(first["action"])
        second = monitor.record_result(by_stage[5]["id"], match("B", "2"))
        self.assertEqual(second["action"], "successor")
        self.assertEqual(second["confidence"], "high")
        self.assertEqual(second["match"]["title"], "B")

    def test_expected_next_album_track_can_break_a_boundary_tie(self):
        current = track(confidence="high", duration=30)
        current["expected_next"] = match("B", "2")
        monitor = TrackMonitor()
        monitor.begin_track(current)
        monitor.start_boundary(expected_end(current), "expected_end", strength="strong")
        requests = monitor.due_requests(1035.1, current)
        by_stage = {r["stage"]: r for r in requests}
        monitor.record_result(by_stage[3]["id"], match("Mashup source", "9"))
        result = monitor.record_result(by_stage[5]["id"], match("B", "2"))
        self.assertEqual(result["action"], "successor")
        self.assertTrue(result["expected_next_match"])
        self.assertEqual(result["match"]["title"], "B")

    def test_expected_next_hint_does_not_override_later_current_track_evidence(self):
        current = track(confidence="high", duration=30)
        current["expected_next"] = match("B", "2")
        monitor = TrackMonitor()
        monitor.begin_track(current)
        monitor.start_boundary(expected_end(current), "expected_end", strength="strong")
        requests = monitor.due_requests(1035.1, current)
        by_stage = {r["stage"]: r for r in requests}
        monitor.record_result(by_stage[3]["id"], match("B", "2"))
        result = monitor.record_result(by_stage[5]["id"], match("A", "1"))
        self.assertIsNone(result["action"])
        ten_request = monitor.due_requests(1040.1, current)[0]
        self.assertEqual(ten_request["stage"], 10)
        result = monitor.record_result(ten_request["id"], match("A", "1"))
        self.assertEqual(result["action"], "continuation")

    def test_mixed_boundary_fingerprints_stay_unresolved(self):
        current = track(confidence="high", duration=30)
        monitor = TrackMonitor()
        monitor.begin_track(current)
        monitor.start_boundary(expected_end(current), "expected_end", strength="strong")
        requests = monitor.due_requests(1040.1, current)
        by_stage = {r["stage"]: r for r in requests}
        monitor.record_result(by_stage[3]["id"], match("X", "2"))
        monitor.record_result(by_stage[5]["id"], match("Y", "3"))
        result = monitor.record_result(by_stage[10]["id"], match("Z", "4"))
        self.assertEqual(result["action"], "boundary_unresolved")

    def test_single_clear_10s_successor_can_use_strong_expected_end_prior(self):
        current = track(confidence="high", duration=30)
        monitor = TrackMonitor()
        monitor.begin_track(current)
        monitor.start_boundary(expected_end(current), "expected_end", strength="strong")
        requests = monitor.due_requests(1040.1, current)
        by_stage = {r["stage"]: r for r in requests}
        monitor.record_result(by_stage[3]["id"], None)
        monitor.record_result(by_stage[5]["id"], None)
        result = monitor.record_result(by_stage[10]["id"], match("B", "2"))
        self.assertEqual(result["action"], "successor")
        self.assertEqual(result["confidence"], "medium")

    def test_scrobble_waits_for_identity_confidence(self):
        current = track(confidence="low", duration=100)
        self.assertTrue(scrobble_is_eligible(current, 1051))
        self.assertFalse(scrobble_identity_confident(current))
        current["recognition_verified"] = True
        self.assertTrue(scrobble_identity_confident(current))

    def test_pending_ambiguous_track_can_be_resolved_by_next_boundary(self):
        queue = PendingScrobbleQueue()
        old = track(confidence="medium", duration=20)
        self.assertTrue(queue.hold(old, ended_at=1020, physical_now=1020, reason="silence"))
        resolved = queue.resolve_with_successor(
            track(title="B", adamid="2", confidence="high", start=1020, duration=120),
            boundary_time=1020,
            strong_boundary=True,
        )
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["title"], "A")
        self.assertTrue(resolved[0]["boundary_confirmed"])

    def test_same_identity_after_pause_is_resume_not_second_scrobble(self):
        queue = PendingScrobbleQueue()
        old = track(confidence="medium", duration=20)
        queue.hold(old, ended_at=1020, physical_now=1020, reason="silence")
        resolved = queue.resolve_with_successor(
            track(title="A", adamid="1", confidence="high", start=1030, duration=20),
            boundary_time=1030,
            strong_boundary=True,
        )
        self.assertEqual(resolved, [])
        self.assertEqual(queue.items, [])


if __name__ == "__main__":
    unittest.main()
