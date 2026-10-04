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
