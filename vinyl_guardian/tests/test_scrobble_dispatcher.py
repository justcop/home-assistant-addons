import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrobble_dispatcher import ScrobbleDispatcher, scrobble_event_id


def track(title="Song", start=1000):
    return {
        "title": title,
        "artist": "Artist",
        "album": "Album",
        "start_timestamp": start,
    }


class ScrobbleDispatcherTests(unittest.TestCase):
    def test_failure_is_persisted_and_retried_until_success(self):
        with tempfile.TemporaryDirectory() as root:
            calls = []
            outcomes = iter([False, True])
            clock = [100.0]

            def send(*args):
                calls.append(args)
                return next(outcomes)

            path = Path(root) / "queue.json"
            queue = ScrobbleDispatcher(
                path,
                send,
                clock=lambda: clock[0],
                autostart=False,
            )
            event_id = queue.submit(track())
            self.assertTrue(queue.process_due())
            self.assertEqual(queue.pending_count, 1)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["pending"][0]["attempts"], 1)
            self.assertEqual(saved["pending"][0]["next_attempt"], 105.0)

            clock[0] = 104.9
            self.assertFalse(queue.process_due())
            clock[0] = 105.0
            self.assertTrue(queue.process_due())
            self.assertEqual(queue.pending_count, 0)
            self.assertIn(event_id, json.loads(path.read_text())["sent"])
            self.assertTrue(queue.is_sent(event_id))
            self.assertEqual(len(calls), 2)

    def test_pending_queue_survives_restart(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "queue.json"
            queue = ScrobbleDispatcher(path, lambda *_: False, autostart=False)
            event_id = queue.submit(track())
            queue.close()

            restored = ScrobbleDispatcher(path, lambda *_: True, autostart=False)
            self.assertTrue(restored.is_pending(event_id))

    def test_corrupt_primary_recovers_current_sent_state_without_replay(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "queue.json"
            queue = ScrobbleDispatcher(path, lambda *_: True, autostart=False)
            event_id = queue.submit(track())
            queue.process_due()
            self.assertEqual(queue.pending_count, 0)
            path.write_text("corrupted")

            restored = ScrobbleDispatcher(path, lambda *_: True, autostart=False)
            self.assertFalse(restored.is_pending(event_id))
            self.assertEqual(restored.pending_count, 0)
            self.assertEqual(restored.submit(track()), event_id)
            self.assertEqual(restored.pending_count, 0)

    def test_restart_timestamp_jitter_maps_to_same_physical_play(self):
        self.assertEqual(
            scrobble_event_id(track(start=1000)),
            scrobble_event_id(track(start=1003)),
        )
        self.assertNotEqual(
            scrobble_event_id(track(start=1000)),
            scrobble_event_id(track(start=1010)),
        )

    def test_same_song_replay_with_different_timestamp_is_not_deduplicated(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "queue.json"
            queue = ScrobbleDispatcher(path, lambda *_: True, autostart=False)
            first = queue.submit(track(start=1000))
            second = queue.submit(track(start=1010))
            self.assertNotEqual(first, second)
            self.assertEqual(queue.pending_count, 2)

    def test_same_physical_play_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "queue.json"
            queue = ScrobbleDispatcher(path, lambda *_: True, autostart=False)
            first = queue.submit(track(start=1000))
            second = queue.submit(track(start=1000))
            self.assertEqual(first, second)
            self.assertEqual(queue.pending_count, 1)

    def test_success_callback_receives_original_track(self):
        with tempfile.TemporaryDirectory() as root:
            delivered = []
            queue = ScrobbleDispatcher(
                Path(root) / "queue.json",
                lambda *_: True,
                on_success=lambda row: delivered.append(row),
                autostart=False,
            )
            queue.submit(track())
            queue.process_due()
            self.assertEqual(delivered[0]["track"]["title"], "Song")

    def test_disabled_queue_does_not_accumulate(self):
        with tempfile.TemporaryDirectory() as root:
            queue = ScrobbleDispatcher(
                Path(root) / "queue.json",
                lambda *_: True,
                enabled=False,
                autostart=False,
            )
            self.assertIsNone(queue.submit(track()))
            self.assertEqual(queue.pending_count, 0)

    def test_event_id_requires_title_artist_and_timestamp(self):
        self.assertIsNone(scrobble_event_id({}))
        self.assertIsNotNone(scrobble_event_id(track()))


if __name__ == "__main__":
    unittest.main()
