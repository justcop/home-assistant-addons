"""Regressions for the repeated Cover Corp. Walrus match in session captures."""
import ast
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from track_reasoning import (AlbumIdentityGuard, TrackMonitor, scrobble_identity_confident,
                             identity_key, UNKNOWN_DURATION_SCROBBLE_SECONDS)


def song(title, artist="The Beatles", album="Magical Mystery Tour", start=1000):
    return dict(title=title, artist=artist, album=album, session_start_time=start,
                start_timestamp=start, recognition_confidence="high",
                recognition_verified=True, recognition_conflicts=0)


class ArtistContextTests(unittest.TestCase):
    def setUp(self):
        self.guard = AlbumIdentityGuard()
        self.guard.observe(song("Blue Jay Way", start=1000))
        self.guard.observe(song("Your Mother Should Know", start=1240))
        self.wrong = song("I Am the Walrus", "The Cover Corp.", "Unknown", 1390)

    def test_repeated_high_confidence_wrong_artist_is_held(self):
        conflict = self.guard.conflict(self.wrong, 1390)
        self.assertEqual(conflict["expected_artist"], "The Beatles")
        self.wrong["identity_context_conflict"] = conflict
        self.wrong["boundary_confirmed"] = True
        self.assertFalse(scrobble_identity_confident(self.wrong))
        self.guard.observe(self.wrong)
        self.assertEqual(self.guard.tracks[-1]["track"]["artist"], "The Beatles")

    def test_expected_title_artist_conflict_even_with_cover_catalogue(self):
        previous = song("Your Mother Should Know", start=1240)
        previous["expected_next"] = song("I Am the Walrus")
        self.guard.observe(previous)
        candidate = dict(self.wrong, title="I Am the Walrus (Remastered 2009)",
                         album="Beatles Covers", album_adamid="covers")
        self.assertIn("expected album track", self.guard.conflict(candidate, 1390)["reason"])

    def test_catalogue_backed_artist_change_allowed(self):
        candidate = song("A Different Song", "Another Artist", "Another Album", 1390)
        self.assertIsNone(self.guard.conflict(candidate, 1390))

    def test_one_track_and_repeated_updates_do_not_establish_album(self):
        guard = AlbumIdentityGuard()
        for _ in range(3):
            guard.observe(song("Blue Jay Way"))
        self.assertIsNone(guard.conflict(self.wrong, 1390))

    def test_expired_context_does_not_block_next_session(self):
        self.assertIsNone(self.guard.conflict(self.wrong, 2000))

    def test_correct_artist_is_never_invented_or_blocked(self):
        self.assertIsNone(self.guard.conflict(dict(self.wrong, artist="The Beatles"), 1390))
        self.assertEqual(self.wrong["artist"], "The Cover Corp.")

    def test_held_high_confidence_track_still_gets_fresh_verification(self):
        current = dict(self.wrong, identity_context_conflict=self.guard.conflict(self.wrong, 1390),
                       duration=200, duration_known=True)
        monitor = TrackMonitor()
        monitor.begin_track(current)
        requests = monitor.due_requests(1420, current)
        self.assertEqual([row["kind"] for row in requests], ["verification", "verification"])

    def test_real_send_gate_blocks_delayed_boundary_promotions(self):
        source = Path(__file__).resolve().parents[1] / "vinyl_guardian.py"
        tree = ast.parse(source.read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "_send_scrobble")
        env = {"scrobble_to_lastfm": lambda *args: self.fail("Must not send wrong artist")}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), env)
        current = dict(self.wrong, identity_context_conflict=self.guard.conflict(self.wrong, 1390),
                       boundary_confirmed=True)
        self.assertFalse(env["_send_scrobble"](current, mark_current=True))

    def test_real_track_creation_attaches_conflict_without_rewriting_artist(self):
        source = Path(__file__).resolve().parents[1] / "vinyl_guardian.py"
        tree = ast.parse(source.read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "_make_track")
        env = dict(_track_duration=lambda match: 0, identity_key=identity_key,
                   album_identity_guard=self.guard, log=lambda message: None,
                   _track_id=lambda track: track["title"],
                   UNKNOWN_DURATION_SCROBBLE_SECONDS=UNKNOWN_DURATION_SCROBBLE_SECONDS)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), env)
        current = env["_make_track"](self.wrong, 1390, 1390, "high", support=2)
        self.assertEqual(current["artist"], "The Cover Corp.")
        self.assertIn("Unsupported artist change", current["scrobble_pending_reason"])
        self.assertFalse(scrobble_identity_confident(current))

    def test_compilation_does_not_establish_single_artist_context(self):
        guard = AlbumIdentityGuard()
        guard.observe(song("A", "Artist One", "Various Artists"))
        guard.observe(song("B", "Artist Two", "Various Artists", 1200))
        self.assertIsNone(guard.conflict(self.wrong, 1390))


if __name__ == "__main__":
    unittest.main()
