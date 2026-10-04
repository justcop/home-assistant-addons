import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime_presentation import current_track_presentation


class RuntimePresentationTests(unittest.TestCase):
    def test_runout_is_not_playing_even_when_track_is_still_retained(self):
        track = {"title": "A", "artist": "Artist", "album": "Album"}
        state, attributes = current_track_presentation(
            "Runout Groove",
            "SLEEPING",
            track,
        )
        self.assertEqual(state, "Not Playing")
        self.assertEqual(attributes, {})

    def test_live_track_is_restored_outside_runout(self):
        track = {"title": "A", "artist": "Artist", "album": "Album"}
        state, attributes = current_track_presentation("Playing", "SLEEPING", track)
        self.assertEqual(state, "A - Artist")
        self.assertEqual(attributes["title"], "A")

    def test_post_runout_suppression_survives_status_change_until_verified(self):
        track = {"title": "Old", "artist": "Artist"}
        state, attributes = current_track_presentation(
            "Playing",
            "SLEEPING",
            track,
            suppress_track=True,
        )
        self.assertEqual(state, "Not Playing")
        self.assertEqual(attributes, {})

    def test_searching_and_idle_states_are_distinct(self):
        self.assertEqual(
            current_track_presentation("Playing", "RECORDING", None)[0],
            "Searching...",
        )
        self.assertEqual(
            current_track_presentation("Motor Idle", "IDLE", None)[0],
            "Not Playing",
        )


if __name__ == "__main__":
    unittest.main()
