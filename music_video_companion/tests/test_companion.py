"""Offline regression tests for approvals, automatic matching and mute protection."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from model import Library, song_key, strict_candidate, vevo_candidate
from runtime import Companion
from providers import spotify_track, ApiHTTPError


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

    def refresh_entity(self, entity):
        self.calls.append(("update_entity", {"entity_id": entity}))
        return {}

    def service(self, action, data):
        self.calls.append((action, data))
        if action == "volume_mute":
            target = self.state(data["entity_id"])
            target["attributes"]["is_volume_muted"] = data["is_volume_muted"]
        if action == "volume_set":
            target = self.state(data["entity_id"])
            target["attributes"]["volume_level"] = data["volume_level"]
        if action == "play_media":
            self.cast["state"] = "playing"
            self.cast["attributes"]["app_name"] = "YouTube"
            self.cast["attributes"]["media_content_id"] = VID
        if action == "media_stop":
            self.cast["state"] = "idle"
        if action == "media_pause":
            self.cast["state"] = "paused"
        if action == "media_play":
            self.cast["state"] = "playing"
        if action == "media_seek":
            self.cast["attributes"]["media_position"] = data["seek_position"]
        return {}


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ha = FakeHA()
        self.app = Companion(path=Path(self.temp.name) / "library.json", ha=self.ha, options={})
        self.app.library.configure({"spotify_entity": "media_player.spotify",
                                    "cast_entity": "media_player.chromecast",
                                    "audio_control": "automatic"})

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

    def test_paused_spotify_pauses_and_resumes_video(self):
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.library.configure({"auto": True})
        self.app.poll()
        self.ha.spotify["state"] = "paused"
        self.app.poll()
        self.assertIsNotNone(self.app.active)
        self.assertEqual(self.ha.cast["state"], "paused")
        self.ha.spotify["state"] = "playing"
        self.ha.spotify["attributes"]["media_position"] = 42
        self.app.poll()
        self.assertEqual(self.ha.cast["state"], "playing")
        self.assertEqual(self.ha.cast["attributes"]["media_position"], 42.25)
        self.assertEqual(self.app.active["seek_count"], 2)

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

    def test_initial_seek_after_cast_loads(self):
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.poll()
        self.ha.spotify["attributes"]["media_position"] = 65
        self.app.poll()
        self.app.play_current()
        self.app.sync_playback()
        self.assertEqual(self.ha.cast["attributes"]["media_position"], 65.25)
        self.assertEqual(self.app.active["sync_state"], "Aligned at launch")

    def test_no_seek_corrections_during_uninterrupted_song(self):
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.poll()
        self.app.play_current()
        self.app.sync_playback()
        count = sum(action == "media_seek" for action, _ in self.ha.calls)
        for seconds in (20, 48, 95):
            self.ha.spotify["attributes"]["media_position"] = seconds
            self.app.poll()
        self.assertEqual(sum(action == "media_seek" for action, _ in self.ha.calls), count)

    def test_extra_video_intro_offset(self):
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.library.set_offset("The Beatles", "Something", 12.5)
        self.app.poll()
        self.ha.spotify["attributes"]["media_position"] = 20
        self.app.play_current()
        self.app.sync_playback()
        self.assertEqual(self.ha.cast["attributes"]["media_position"], 32.75)
        self.assertEqual(Library(self.app.library.path).match("The Beatles", "Something")["offset_seconds"], 12.5)

    def test_different_receiver_content_not_sought(self):
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.poll()
        self.app.play_current()
        self.ha.cast["attributes"]["media_content_id"] = "anotherid01"
        self.ha.cast["attributes"]["app_name"] = "Netflix"
        before = len(self.ha.calls)
        self.app.sync_playback()
        self.assertEqual(len(self.ha.calls), before)

    def test_unloaded_receiver_not_sought(self):
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.poll()
        self.app.play_current()
        self.ha.cast["attributes"]["media_content_id"] = ""
        self.ha.cast["state"] = "idle"
        before = len(self.ha.calls)
        self.app.sync_playback()
        self.assertEqual(len(self.ha.calls), before)
        self.assertTrue(self.app.active["initial_pending"])

    def test_offset_input_validation(self):
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        for invalid in ("nan", 100000, "oops"):
            with self.assertRaises(ValueError):
                self.app.library.set_offset("The Beatles", "Something", invalid)

    def test_seek_unsupported_visible_to_user(self):
        class NoSeekHA(FakeHA):
            def service(self, action, data):
                if action == "media_seek":
                    raise RuntimeError("SEEK not supported")
                return super().service(action, data)
        ha = NoSeekHA()
        app = Companion(path=Path(self.temp.name) / "no_seek.json", ha=ha, options={})
        app.library.configure({"spotify_entity": "media_player.spotify", "cast_entity": "media_player.chromecast"})
        app.library.approve("The Beatles", "Something", CANDIDATE)
        app.poll()
        app.play_current()
        app.sync_playback()
        self.assertIn("seek unavailable", app.active["sync_state"])

    def test_unsupported_mute_falls_back_to_volume_zero(self):
        class VolumeOnlyHA(FakeHA):
            def __init__(self):
                super().__init__()
                self.cast["attributes"] = {"volume_level": 0.35, "supported_features": 516}
        ha = VolumeOnlyHA()
        app = Companion(path=Path(self.temp.name) / "volume.json", ha=ha, options={})
        app.library.configure({"spotify_entity": "media_player.spotify", "cast_entity": "media_player.chromecast"})
        app.library.configure({"audio_control": "automatic"})
        app.library.approve("The Beatles", "Something", CANDIDATE)
        app.poll()
        app.play_current()
        self.assertEqual(ha.cast["attributes"]["volume_level"], 0.0)
        self.assertEqual([a for a, _ in ha.calls if a == "volume_mute"], [])
        self.assertEqual(app.active["audio_restore"]["method"], "volume")
        app.stop()
        self.assertEqual(ha.cast["attributes"]["volume_level"], 0.35)

    def test_mute_action_rejected_falls_back_to_volume_set(self):
        class RejectMuteHA(FakeHA):
            def __init__(self):
                super().__init__()
                self.cast["attributes"]["volume_level"] = 0.3
            def service(self, action, data):
                if action == "volume_mute":
                    raise ApiHTTPError(400, "Entity does not support mute")
                return super().service(action, data)
        ha = RejectMuteHA()
        app = Companion(path=Path(self.temp.name) / "reject.json", ha=ha, options={})
        app.library.configure({"spotify_entity": "media_player.spotify", "cast_entity": "media_player.chromecast"})
        app.library.configure({"audio_control": "automatic"})
        app.library.approve("The Beatles", "Something", CANDIDATE)
        app.poll()
        app.play_current()
        self.assertEqual(app.active["audio_restore"]["method"], "volume")
        self.assertEqual(ha.cast["attributes"]["volume_level"], 0)
        app.stop()
        self.assertEqual(ha.cast["attributes"]["volume_level"], 0.3)

    def test_no_supported_audio_control_fails_closed_without_cast(self):
        self.ha.cast["attributes"] = {"supported_features": 512}
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.poll()
        with self.assertRaisesRegex(ValueError, "cannot be safely silenced"):
            self.app.play_current()
        self.assertFalse(any(action == "play_media" for action, _ in self.ha.calls))

    def test_optional_separate_soundbar_mute(self):
        class SeparateAudioHA(FakeHA):
            def __init__(self):
                super().__init__()
                self.cast["attributes"] = {"supported_features": 512}
                self.soundbar = {"state": "idle", "attributes": {
                    "is_volume_muted": False, "supported_features": 8
                }}
            def state(self, entity):
                if entity == "media_player.soundbar":
                    return self.soundbar
                return super().state(entity)
        ha = SeparateAudioHA()
        app = Companion(path=Path(self.temp.name) / "separate.json", ha=ha, options={})
        app.library.configure({"spotify_entity": "media_player.spotify",
                               "cast_entity": "media_player.chromecast",
                               "mute_entity": "media_player.soundbar"})
        app.library.configure({"audio_control": "automatic"})
        app.library.approve("The Beatles", "Something", CANDIDATE)
        app.poll()
        app.play_current()
        self.assertTrue(ha.soundbar["attributes"]["is_volume_muted"])
        self.assertEqual(app.active["audio_restore"]["entity"], "media_player.soundbar")
        app.stop()
        self.assertFalse(ha.soundbar["attributes"]["is_volume_muted"])

    def test_dont_mute_spotify_playing_device(self):
        self.app.library.configure({"mute_entity": "media_player.spotify"})
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.poll()
        with self.assertRaisesRegex(ValueError, "Spotify audio device"):
            self.app.play_current()
        self.assertEqual(self.ha.calls, [])

    def test_manual_volume_change_is_not_undone(self):
        self.ha.cast["attributes"] = {"volume_level": 0.5, "supported_features": 516}
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.poll()
        self.app.play_current()
        self.ha.cast["attributes"]["volume_level"] = 0.6
        self.app.stop()
        self.assertEqual(self.ha.cast["attributes"]["volume_level"], 0.6)

    def test_mute_failure_prevents_cast_when_no_volume_fallback(self):
        class NoMuteHA(FakeHA):
            def service(self, action, data):
                if action == "volume_mute":
                    raise ApiHTTPError(400, "Unsupported")
                return super().service(action, data)
        ha = NoMuteHA()
        app = Companion(path=Path(self.temp.name) / "none.json", ha=ha, options={})
        app.library.configure({"spotify_entity": "media_player.spotify", "cast_entity": "media_player.chromecast"})
        app.library.configure({"audio_control": "automatic"})
        app.library.approve("The Beatles", "Something", CANDIDATE)
        app.poll()
        with self.assertRaisesRegex(ValueError, "cannot be safely silenced"):
            app.play_current()
        self.assertFalse(any(action == "play_media" for action, _ in ha.calls))

    def test_manual_volume_casts_without_any_mute_or_volume_command(self):
        self.ha.cast["attributes"] = {"supported_features": 512}
        self.app.library.configure({"audio_control": "manual"})
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.poll()
        self.app.play_current()
        self.assertIsNotNone(self.app.active)
        self.assertTrue(self.app.active["manual_audio"])
        self.assertIsNone(self.app.active["audio_restore"])
        self.assertIn("play_media", [action for action, _ in self.ha.calls])
        self.assertFalse(any(action.startswith("volume") for action, _ in self.ha.calls))
        self.app.sync_playback()
        self.app.stop()
        self.assertFalse(any(action.startswith("volume") for action, _ in self.ha.calls))

    def test_manual_mode_skips_audio_entity_even_if_it_is_unavailable(self):
        self.app.library.configure({"audio_control": "manual", "mute_entity": "media_player.missing"})
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.poll()
        self.app.play_current()
        self.assertEqual(self.app.active["entity"], "media_player.chromecast")

    def test_fresh_install_defaults_to_manual_volume(self):
        from model import Library
        library = Library(Path(self.temp.name) / "new-library.json")
        self.assertEqual(library.settings()["audio_control"], "manual")

    def test_settings_upgrade_adds_manual_audio_default(self):
        import json
        target = Path(self.temp.name) / "old-library.json"
        target.write_text(json.dumps({"settings": {"auto": True, "cast_entity": "media_player.chromecast"},
                                      "matches": {}, "trusted_channels": {}}))
        from model import Library
        upgraded = Library(target)
        self.assertEqual(upgraded.settings()["audio_control"], "manual")
        self.assertTrue(upgraded.settings()["auto"])

    def test_invalid_audio_mode_rejected(self):
        with self.assertRaisesRegex(ValueError, "audio control"):
            self.app.library.configure({"audio_control": "always_mute"})

    def test_vevo_exact_artist_channel_and_title(self):
        vevo = {"video_id": VID, "channel_id": "UC" + "x" * 22,
                "channel_title": "TheBeatlesVEVO",
                "title": "The Beatles - Something (Official Music Video)"}
        self.assertTrue(vevo_candidate(vevo, "The Beatles", "Something"))
        self.assertFalse(vevo_candidate(dict(vevo, channel_title="BestMusicVEVO"), "The Beatles", "Something"))
        self.assertFalse(vevo_candidate(dict(vevo, title="The Beatles Something LIVE at Wembley Official Video"), "The Beatles", "Something"))
        self.assertFalse(vevo_candidate(dict(vevo, title="The Beatles - Something cover official video"), "The Beatles", "Something"))
        self.assertFalse(vevo_candidate(dict(vevo, channel_title="The Beatles VEVO Fans"), "The Beatles", "Something"))

    def test_auto_approve_one_exact_artist_vevo_result(self):
        vevo = {"video_id": VID, "channel_id": "UC" + "x" * 22,
                "channel_title": "TheBeatlesVEVO",
                "title": "The Beatles - Something (Official Music Video)"}
        self.app.options["youtube_api_key"] = "test"
        self.app.library.configure({"auto": True})
        with patch("actions_cast.youtube_search", return_value=[vevo]) as youtube:
            self.app.poll()
        self.assertEqual(youtube.call_count, 1)
        approved = self.app.library.match("The Beatles", "Something")
        self.assertEqual(approved["approval_basis"], "vevo_channel_name")
        self.assertEqual(approved["video_id"], VID)

    def test_ambiguous_vevo_results_not_auto_approved(self):
        one = {"video_id": VID, "channel_id": "UC" + "x" * 22,
               "channel_title": "TheBeatlesVEVO", "title": "The Beatles Something Official Video"}
        two = dict(one, video_id="a" * 11)
        self.app.options["youtube_api_key"] = "test"
        self.app.library.configure({"auto": True})
        with patch("actions_cast.youtube_search", return_value=[one, two]):
            self.app.poll()
        self.assertIsNone(self.app.library.match("The Beatles", "Something"))
        self.assertIsNone(self.app.active)

    def test_spotify_fast_refresh_bounded(self):
        self.app.spotify_refresh_next = 0
        self.app.poll()
        self.assertEqual(sum(action == "update_entity" for action, _ in self.ha.calls), 1)
        self.app.poll()
        self.assertEqual(sum(action == "update_entity" for action, _ in self.ha.calls), 1)
        self.assertEqual(self.app.status()["spotify_refresh"]["attempts"], 1)

    def test_refresh_errors_back_off_without_stopping_poll(self):
        class BrokenRefreshHA(FakeHA):
            def refresh_entity(self, entity):
                raise RuntimeError("429 Too Many Requests")
        ha = BrokenRefreshHA()
        app = Companion(path=Path(self.temp.name) / "broken_refresh.json",
                        ha=ha, options={"spotify_refresh_seconds": 10})
        app.library.configure({"spotify_entity": "media_player.spotify", "cast_entity": "media_player.chromecast"})
        app.spotify_refresh_next = 0
        app.poll()
        status = app.status()["spotify_refresh"]
        self.assertEqual(status["failures"], 1)
        self.assertIn("429", status["last_error"])
        self.assertGreater(status["seconds_until_next"], 10)
        self.assertEqual(app.current["title"], "Something")

    def test_refresh_disabled_means_no_update_entity(self):
        app = Companion(path=Path(self.temp.name) / "disabled.json", ha=self.ha,
                        options={"spotify_refresh_seconds": 0})
        app.library.configure({"spotify_entity": "media_player.spotify", "cast_entity": "media_player.chromecast"})
        app.spotify_refresh_next = 0
        app.poll()
        self.assertFalse(any(action == "update_entity" for action, _ in self.ha.calls))

    def test_youtube_preroll_waits_for_selected_video_before_initial_seek(self):
        self.app.library.approve("The Beatles", "Something", CANDIDATE)
        self.app.poll()
        self.app.play_current()
        self.ha.cast["attributes"]["media_content_id"] = "a" * 11
        self.ha.cast["attributes"]["app_name"] = "YouTube"
        self.app.active["launched_at"] -= 15
        self.app.sync_playback()
        self.assertIsNotNone(self.app.active)
        self.assertIn("advert", self.app.active["sync_state"])
        self.assertEqual(self.app.active["seek_count"], 0)
        self.ha.cast["attributes"]["media_content_id"] = VID
        self.ha.spotify["attributes"]["media_position"] = 34
        self.app.sync_playback()
        self.assertEqual(self.app.active["seek_count"], 1)
        self.assertEqual(self.ha.cast["attributes"]["media_position"], 34.25)


if __name__ == "__main__":
    unittest.main()
