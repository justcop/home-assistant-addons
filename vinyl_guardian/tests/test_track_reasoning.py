import ast
import unittest
from pathlib import Path
import sys

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


def track(title="A", adamid="1", confidence="low", start=1000.0, duration=180.0, duration_known=True):
    item = {
        **match(title, adamid),
        "session_start_time": start,
        "start_timestamp": start,
        "duration": duration,
        "duration_known": duration_known,
        "scrobble_trigger_time": start + min(duration / 2.0, 240.0),
        "recognition_confidence": confidence,
        "recognition_verified": confidence == "high",
        "scrobble_fired": False,
    }
    if duration_known:
        item["expected_end_time"] = start + duration
    item["identity_key"] = identity_key(item)
    return item


class AudioWindowTests(unittest.TestCase):
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

    def test_unknown_duration_uses_repeated_gapless_probes(self):
        current = track(confidence="high", duration_known=False)
        monitor = TrackMonitor()
        monitor.begin_track(current)

        first = monitor.due_requests(1020.1, current)
        self.assertEqual(len(first), 1)
        self.assertEqual((first[0]["start"], first[0]["end"]), (1010.0, 1020.0))
        result = monitor.record_result(first[0]["id"], match("B", "2"))
        self.assertEqual(result["action"], "unknown_candidate")

        second = monitor.due_requests(1030.1, current)
        result = monitor.record_result(second[0]["id"], match("B", "2"))
        self.assertEqual(result["action"], "successor")
        self.assertEqual(result["reason"], "unknown_duration_consensus")
        self.assertEqual(result["anchor"], 1010.0)

    def test_unknown_duration_probe_waits_for_previous_result(self):
        current = track(confidence="high", duration_known=False)
        monitor = TrackMonitor()
        monitor.begin_track(current)
        first = monitor.due_requests(1020.1, current)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["stage"], 10)
        self.assertEqual(monitor.due_requests(1035.0, current), [])
        monitor.record_result(first[0]["id"], match("A", "1"))
        second = monitor.due_requests(1035.0, current)
        self.assertEqual(len(second), 1)
        self.assertEqual((second[0]["start"], second[0]["end"]), (1020.0, 1030.0))

    def test_unknown_duration_single_or_mixed_alternate_does_not_change_track(self):
        current = track(confidence="high", duration_known=False)
        monitor = TrackMonitor()
        monitor.begin_track(current)
        first = monitor.due_requests(1020.1, current)[0]
        self.assertEqual(
            monitor.record_result(first["id"], match("Mashup X", "8"))["action"],
            "unknown_candidate",
        )
        second = monitor.due_requests(1030.1, current)[0]
        self.assertEqual(
            monitor.record_result(second["id"], match("Mashup Y", "9"))["action"],
            "unknown_candidate",
        )
        third = monitor.due_requests(1040.1, current)[0]
        self.assertIsNone(
            monitor.record_result(third["id"], match("A", "1"))["action"]
        )

    def test_completed_unknown_duration_track_can_scrobble_before_four_minutes(self):
        unknown = track(
            confidence="high",
            start=1000,
            duration=1200,
            duration_known=False,
        )
        unknown["scrobble_trigger_time"] = 1240
        self.assertFalse(scrobble_is_eligible(unknown, 1120))
        self.assertTrue(scrobble_is_eligible(unknown, 1120, completed=True))
        self.assertFalse(scrobble_is_eligible(unknown, 1029, completed=True))

    def test_lastfm_minimum_duration_is_enforced(self):
        short = track(confidence="high", duration=30)
        self.assertFalse(scrobble_is_eligible(short, 2000))
        long_enough = track(confidence="high", duration=31)
        self.assertTrue(scrobble_is_eligible(long_enough, 1016))
        unknown = track(confidence="high", duration=1200, duration_known=False)
        unknown["scrobble_trigger_time"] = 1240
        self.assertTrue(scrobble_is_eligible(unknown, 1240))

    def test_scrobble_waits_for_identity_confidence(self):
        current = track(confidence="low", duration=100)
        self.assertTrue(scrobble_is_eligible(current, 1051))
        self.assertFalse(scrobble_identity_confident(current))
        current["recognition_verified"] = True
        self.assertTrue(scrobble_identity_confident(current))

    def test_pending_ambiguous_track_can_be_resolved_by_next_boundary(self):
        queue = PendingScrobbleQueue()
        old = track(confidence="medium", duration=40)
        self.assertTrue(queue.hold(old, ended_at=1040, physical_now=1040, reason="silence"))
        resolved = queue.resolve_with_successor(
            track(title="B", adamid="2", confidence="high", start=1040, duration=120),
            boundary_time=1040,
            strong_boundary=True,
        )
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["title"], "A")
        self.assertTrue(resolved[0]["boundary_confirmed"])

    def test_same_identity_after_pause_is_resume_not_second_scrobble(self):
        queue = PendingScrobbleQueue()
        old = track(confidence="medium", duration=40)
        queue.hold(old, ended_at=1040, physical_now=1040, reason="silence")
        resolved = queue.resolve_with_successor(
            track(title="A", adamid="1", confidence="high", start=1050, duration=40),
            boundary_time=1050,
            strong_boundary=True,
        )
        self.assertEqual(resolved, [])
        self.assertEqual(queue.items, [])


if __name__ == "__main__":
    unittest.main()
