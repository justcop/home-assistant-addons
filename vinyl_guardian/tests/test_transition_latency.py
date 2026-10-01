import json
import sys
import tempfile
import unittest
import zipfile
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from diagnostic_reports import export_diagnostics
from experiment import EventAudioRecorder
from transition_latency import TransitionLatencyCollector


class Timeline:
    def __init__(self):
        self.events = []

    def record(self, event, now=None, **details):
        self.events.append(dict(event=event, now=now, **details))


def state(status, power=False, music=False, runout=False, **extra):
    frame = {
        "status": status,
        "turntable_on": power,
        "music_active": music,
        "runout_locked": runout,
        "has_played_music": bool(music),
        "seconds_since_music": 1e9,
        "motor_evidence": 0.0,
        "motor_confidence": 0.0,
        "music_evidence": 0.0,
        "music_confidence": 0.0,
        "runout_candidate_accepted": False,
        "runout_support": 0,
        "runout_confidence": 0.0,
        "runout_rpm": None,
    }
    frame.update(extra)
    return frame


class TransitionLatencyTests(unittest.TestCase):
    def collector(self, root):
        captured = []
        timeline = Timeline()

        def capture(kind, now, **details):
            captured.append(dict(kind=kind, now=now, **details))
            return True

        collector = TransitionLatencyCollector(
            root, capture, timeline, rate=44100, channels=1, chunk=2048
        )
        return collector, captured, timeline

    def test_motor_idle_transient_becomes_positive_needle_drop_sample(self):
        with tempfile.TemporaryDirectory() as root:
            collector, captured, _ = self.collector(root)
            quiet = {
                "stylus_contact_candidate": False,
                "stylus_contact_score": 0.1,
            }
            contact = {
                "stylus_contact_candidate": True,
                "stylus_contact_score": 12.0,
                "transient_peak": 0.4,
                "transient_crest": 20.0,
                "derivative_peak": 0.3,
                "derivative_crest": 14.0,
            }
            idle = state(
                "Motor Idle",
                power=True,
                motor_evidence=0.8,
                motor_confidence=0.9,
            )
            collector.observe(idle, 1000.0, quiet)
            collector.observe(idle, 1000.2, contact)
            collector.observe(
                state(
                    "Playing",
                    power=True,
                    music=True,
                    motor_evidence=0.8,
                    motor_confidence=0.9,
                    music_evidence=0.9,
                    music_confidence=0.8,
                ),
                1001.0,
                quiet,
            )

            rows = [
                json.loads(line)
                for line in Path(root, "experiments", "needle_drop_candidates.jsonl")
                .read_text()
                .splitlines()
            ]
            self.assertEqual(rows[0]["event"], "needle_drop_candidate")
            self.assertEqual(rows[-1]["outcome"], "music_confirmed")
            self.assertEqual(rows[-1]["timing_bucket"], "within_2s")
            self.assertAlmostEqual(rows[-1]["seconds_to_resolution"], 0.8)
            self.assertTrue(any(c["kind"] == "needle_drop_candidate" for c in captured))

    def test_transient_is_ignored_outside_motor_idle_precondition(self):
        with tempfile.TemporaryDirectory() as root:
            collector, captured, _ = self.collector(root)
            contact = {
                "stylus_contact_candidate": True,
                "stylus_contact_score": 10.0,
            }
            collector.observe(state("Powered Off"), 1000.0, contact)
            collector.observe(state("Powered Off"), 1000.2, contact)
            collector.observe(state("Playing", power=True, music=True), 1000.4, contact)
            self.assertFalse(
                Path(root, "experiments", "needle_drop_candidates.jsonl").exists()
            )
            self.assertFalse(any(c["kind"] == "needle_drop_candidate" for c in captured))

    def test_slow_playing_confirmation_is_captured(self):
        with tempfile.TemporaryDirectory() as root:
            collector, captured, _ = self.collector(root)
            quiet = {"stylus_contact_candidate": False}
            collector.observe(
                state("Motor Idle", power=True, music_evidence=0.0),
                1000.0,
                quiet,
            )
            collector.observe(
                state(
                    "Motor Idle",
                    power=True,
                    music_evidence=0.35,
                    music_confidence=0.20,
                ),
                1000.4,
                quiet,
            )
            records = collector.observe(
                state(
                    "Playing",
                    power=True,
                    music=True,
                    music_evidence=0.9,
                    music_confidence=0.8,
                ),
                1001.8,
                quiet,
            )
            playing = next(x for x in records if x["transition_type"] == "playing_on")
            self.assertTrue(playing["slow"])
            self.assertGreater(playing["latency_sec"], 1.0)
            self.assertTrue(
                any(c["kind"] == "transition_latency_playing_on" for c in captured)
            )

    def test_transition_clips_without_diagnostic_session_are_exported(self):
        with tempfile.TemporaryDirectory() as root:
            audio = EventAudioRecorder(
                root, rate=4, channels=1, chunk=4, pre_roll_sec=1, post_roll_sec=1
            )
            audio.feed(b"\0\0" * 4, {"index": 0})
            audio.trigger(
                "transition_latency_playing_on",
                1000,
                details={"transition_type": "playing_on"},
                min_gap_sec=0,
            )
            audio.feed(b"\1\0" * 4, {"index": 1})
            archive = zipfile.ZipFile(io.BytesIO(export_diagnostics(root)))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["included_clips"], 1)
            self.assertEqual(
                manifest["events"][0]["event"], "transition_latency_playing_on"
            )
            self.assertIsNone(manifest["events"][0]["session_id"])


if __name__ == "__main__":
    unittest.main()
