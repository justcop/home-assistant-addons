"""Channel matching and one-shot timing trim regression tests."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from model import Library, official_channel_candidate
from runtime import Companion
from sync import align_once, target_position, SEEK_TOLERANCE

VID = "4" * 11
CHANNEL = "UC" + "x" * 22


def candidate(channel="Oasis Official", title="Oasis - Wonderwall (Official Video)"):
    return {"video_id": VID, "channel_id": CHANNEL, "channel_title": channel, "title": title}


class OfficialTests(unittest.TestCase):
    def test_official_any_channel_name_if_song_and_artist_exact(self):
        self.assertTrue(official_channel_candidate(candidate(), "Oasis", "Wonderwall"))
        self.assertTrue(official_channel_candidate(candidate(channel="Label Music Official"),
                                                   "Oasis", "Wonderwall"))
        self.assertTrue(official_channel_candidate(candidate(channel="Official Oasis"),
                                                   "Oasis", "Wonderwall"))

    def test_official_is_a_whole_word(self):
        self.assertFalse(official_channel_candidate(candidate(channel="Unofficial Covers"),
                                                    "Oasis", "Wonderwall"))
        self.assertFalse(official_channel_candidate(candidate(channel="OasisOfficial"),
                                                    "Oasis", "Wonderwall"))
        self.assertFalse(official_channel_candidate(candidate(channel="MusicChannel"),
                                                    "Oasis", "Wonderwall"))

    def test_official_only_when_exact_artist_title_video(self):
        self.assertFalse(official_channel_candidate(candidate(title="Wonderwall (Official Video)"),
                                                    "Oasis", "Wonderwall"))
        self.assertFalse(official_channel_candidate(candidate(title="Oasis - Wonderwall Live (Official Video)"),
                                                    "Oasis", "Wonderwall"))
        self.assertFalse(official_channel_candidate(candidate(title="Oasis - Wonderwall Cover (Official Video)"),
                                                    "Oasis", "Wonderwall"))
        self.assertFalse(official_channel_candidate(candidate(title="Blur - Wonderwall (Official Video)"),
                                                    "Oasis", "Wonderwall"))

    def test_auto_approval_is_per_video_not_blanket_channel_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            library = Library(Path(directory) / "library.json")
            entry = library.approve("Oasis", "Wonderwall", candidate(), automatic=True)
            self.assertEqual(entry["approval_basis"], "official_channel_name")
            self.assertEqual(library.trusted("Oasis"), [])

    def test_timing_lead_defaults_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            library = Library(path)
            self.assertEqual(library.settings()["sync_lead_seconds"], .25)
            library.configure({"sync_lead_seconds": -0.4})
            self.assertEqual(Library(path).settings()["sync_lead_seconds"], -0.4)

    def test_bad_fine_tune_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            library = Library(Path(directory) / "library.json")
            for value in (-4, 4, float("nan"), "bad", float("inf")):
                with self.assertRaises(ValueError):
                    library.configure({"sync_lead_seconds": value})


class Recorder:
    def __init__(self):
        self.calls = []
    def service(self, action, data):
        self.calls.append((action, data))


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.ha = Recorder()
        self.active = {"entity": "media_player.cast", "video_id": VID,
                       "launched_at": 0.0, "offset": 0, "sync_lead_seconds": .25,
                       "seek_count": 0}
        self.receiver = {"state": "playing", "attributes": {
            "media_content_id": VID, "app_name": "YouTube", "media_position": 10}}
        self.track = {"state": "playing", "artist": "Oasis", "title": "Wonderwall", "position": 10}

    def test_video_advances_by_small_default_lead(self):
        self.assertEqual(align_once(self.ha, self.active, self.track, self.receiver, "initial", clock=1),
                         "aligned")
        self.assertEqual(self.ha.calls[0][1]["seek_position"], 10.25)

    def test_strict_tolerance_seeks_for_quarter_second_difference(self):
        self.assertLess(SEEK_TOLERANCE, 0.25)
        self.receiver["attributes"]["media_position"] = 10.5
        align_once(self.ha, self.active, self.track, self.receiver, "initial", clock=1)
        self.assertEqual(self.ha.calls[0][1]["seek_position"], 10.25)

    def test_tolerance_does_not_seek_when_within_hundredth(self):
        self.receiver["attributes"]["media_position"] = 10.29
        align_once(self.ha, self.active, self.track, self.receiver, "initial", clock=1)
        self.assertEqual(self.ha.calls, [])

    def test_paused_video_does_not_apply_network_lead(self):
        self.track["state"] = "paused"
        align_once(self.ha, self.active, self.track, self.receiver, "resume", clock=1)
        self.assertEqual(self.ha.calls, [])

    def test_explicit_force_seek_works_despite_near_position(self):
        self.receiver["attributes"]["media_position"] = 10.25
        self.active["force_seek"] = True
        align_once(self.ha, self.active, self.track, self.receiver, "manual", clock=1)
        self.assertEqual(self.ha.calls[0][1]["seek_position"], 10.25)

    def test_manual_realignment_refuses_unrelated_content(self):
        class HA:
            def state(self, entity):
                return {"state": "playing", "attributes": {"media_artist": "Oasis",
                        "media_title": "Wonderwall", "media_position": 10}} if entity == "media_player.spotify" else {
                    "state": "playing", "attributes": {
                        "app_name": "Netflix", "media_content_id": "other"}}
        with tempfile.TemporaryDirectory() as directory:
            app = Companion(path=Path(directory) / "library.json", ha=HA(), options={})
            app.library.configure({"spotify_entity": "media_player.spotify",
                                   "cast_entity": "media_player.cast"})
            app.current = {"state": "playing", "artist": "Oasis",
                           "title": "Wonderwall", "position": 10}
            app.active = dict(self.active, song_key="oasis|wonderwall")
            with self.assertRaisesRegex(ValueError, "not confirmed"):
                app.align_manually()


if __name__ == "__main__":
    unittest.main()
