import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from experiment import EventTimeline, ExperimentHarness, SideSessionTracker
from profile_manager import ProfileManager


class TrustedLabelTests(unittest.TestCase):
    def test_album_session_is_not_frame_level_ground_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = ExperimentHarness(
                tmp, {}, rate=44100, channels=2, chunk=2048,
                enabled=True, auto_capture=False,
                session_label="album_playback",
            )
            self.assertIsNone(harness.trusted_label(1000.0))
            self.assertTrue(harness.manual_label("playing", now=1000.0))
            self.assertEqual(harness.trusted_label(1001.0), "playing")

    def test_persistent_known_off_label_is_only_a_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = ExperimentHarness(
                tmp, {}, rate=44100, channels=2, chunk=2048,
                enabled=True, auto_capture=False,
                session_label="known_off",
                diagnostic_mode="known_off",
            )
            self.assertIsNone(harness.trusted_label(1000.0))


class SideSessionTests(unittest.TestCase):
    def test_side_session_closes_after_runout_needle_lift(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = SideSessionTracker(tmp, EventTimeline(tmp))
            tracker.observe({
                "turntable_on": True,
                "music_active": True,
                "runout_locked": False,
                "status": "Playing",
            }, 1000.0)
            tracker.track_identified({"title": "Example", "artist": "Artist"}, now=1010.0)
            tracker.observe({
                "turntable_on": True,
                "music_active": False,
                "runout_locked": True,
                "runout_rpm": "33⅓",
                "runout_estimated_rpm": 33.34,
                "status": "Runout Groove",
            }, 1100.0)
            tracker.observe({
                "turntable_on": True,
                "music_active": False,
                "runout_locked": False,
                "status": "Motor Idle",
            }, 1105.0)

            self.assertIsNone(tracker.active)
            latest = os.path.join(tmp, "experiments", "latest_side_session.json")
            with open(latest, "r") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["track_count"], 1)
            self.assertTrue(payload["runout_detected"])


class ProfileManagerTests(unittest.TestCase):
    def test_rollback_restores_previous_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            active = os.path.join(tmp, "auto_calibration.json")
            manager = ProfileManager(tmp, active)
            first = manager.save_candidate({"music_threshold": 0.01}, accepted=True)
            manager.save_candidate({"music_threshold": 0.02}, accepted=True)
            rolled = manager.rollback_previous()
            self.assertEqual(rolled["profile_id"], first["profile_id"])
            with open(active, "r") as handle:
                self.assertEqual(json.load(handle)["music_threshold"], 0.01)


if __name__ == "__main__":
    unittest.main()
