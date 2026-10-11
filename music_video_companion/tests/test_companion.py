"""Offline regression tests for approvals, automatic matching and mute protection."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from model import Library, song_key, strict_candidate
from runtime import Companion
from providers import spotify_track


VID = "dQw4w9WgXcQ"
CANDIDATE = {"video_id": VID, "title": "The Beatles - Something (Official Music Video)",
             "channel_id": "UCofficial1234", "channel_title": "The Beatles"}


class FakeHA:
    def __init__(self):
        self.calls = []
        self.spotify = {"state": "playing", "attributes": {"media_artist": "The Beatles",
                        "media_title": "Something", "media_position": 0}}
        self.cast = {"state": "idle", "attributes": {"is_volume_muted": False}}

    def state(self, entity):
        if entity == "media_player.spotify":
            return self.spotify
        if entity == "media_player.chromecast":
            return self.cast
        raise KeyError(entity)

    def service(self, action, data):
        self.calls.append((action, data))
        if action == "volume_mute":
            self.cast["attributes"]["is_volume_muted"] = data["is_volume_muted"]
        if action == "play_media":
            self.cast["state"] = "playing"
            self.cast["attributes"]["app_name"] = "YouTube"
            self.cast["attributes"]["media_content_id"] = VID
        if action == "media_stop":
            self.cast["state"] = "idle"
        return {}


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ha = FakeHA()
        self.app = Companion(path=Path(self.temp.name) / "library.json", ha=self.ha, options={})
        self.app.library.configure({"spotify_entity": "media_player.spotify",
                                    "cast_entity": "media_player.chromecast"})

    def test_remaster_identity(self):
        self.assertEqual(song_key("The Beatles", "Something (2009 Remastered)"),
                         song_key("the beatles", "Something"))

    def test_official_text_not_enough_without_trusted_channel(self):
        self.assertFalse(strict_candidate(CANDIDATE, "The Beatles", "Something", []))
        self.assertTrue(strict_candidate(CANDIDATE, "The Beatles", "Something", ["UCofficial1234"]))
        fake = dict(CANDIDATE, title="Something Live At Abbey Road (Official Video)")
        self.assertFalse(strict_candidate(fake, "The Beatles", "Something", ["UCofficial1234"]))

    def test_unapproved_tracks_never_cast(self):
        self.app.library.configure({"auto": True})
        self.app.poll()
        self.assertIsNone(self.app.active)
        self.assertEqual(self.ha.calls, [])

    def test_approved_song_casts_and_restores_volume(self):
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.library.configure({"auto": True})
        self.app.poll()
        self.assertEqual(self.app.active["video_id"], VID)
        self.assertEqual([x[0] for x in self.ha.calls][:2], ["volume_mute", "play_media"])
        self.app.stop()
        self.assertIn("media_stop", [x[0] for x in self.ha.calls])
        self.assertFalse(self.ha.cast["attributes"]["is_volume_muted"])

    def test_paused_spotify_stops_owned_video(self):
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.library.configure({"auto": True})
        self.app.poll()
        self.ha.spotify["state"] = "paused"
        self.app.poll()
        self.assertIsNone(self.app.active)

    def test_unrelated_tv_playback_not_stopped(self):
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.poll()
        self.app.play_current()
        self.ha.cast["attributes"]["media_content_id"] = "another-video"
        self.ha.cast["attributes"]["app_name"] = "Netflix"
        before = len(self.ha.calls)
        self.app.stop()
        self.assertEqual(len(self.ha.calls), before)

    def test_library_persists_approvals_and_channel_trust(self):
        self.app.library.approve("The Beatles", "Something", CANDIDATE, trust=True)
        loaded = Library(self.app.library.path)
        self.assertEqual(loaded.match("the beatles", "Something")["video_id"], VID)
        self.assertEqual(loaded.trusted("The Beatles"), ["UCofficial1234"])

    def test_external_events_are_inert_until_selected(self):
        self.app.external_update({"artist": "A", "title": "B", "state": "playing"})
        self.app.poll()
        self.assertEqual(self.app.current["artist"], "The Beatles")
        self.app.library.configure({"source": "external"})
        self.app.poll()
        self.assertEqual(self.app.current["artist"], "A")

    def test_spotify_entity_extract(self):
        self.assertEqual(spotify_track(self.ha.spotify)["title"], "Something")


if __name__ == "__main__":
    unittest.main()
