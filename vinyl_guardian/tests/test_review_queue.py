import json
import tempfile
import unittest
import wave
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from review_queue import list_samples, review_sample, audio_bytes
from regression import collect_labelled_event_clips


class ReviewQueueTests(unittest.TestCase):
    def make_sample(self, root, name="sample", event="shadow_disagreement", label=None, review=None):
        audio = Path(root) / "experiments" / "event_audio"
        audio.mkdir(parents=True, exist_ok=True)
        wav = audio / (name + ".wav")
        with wave.open(str(wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(b"\0\0" * 80)
        payload = {
            "event": event,
            "label": label,
            "trigger_time": 1000.0,
            "details": {"suggested_label": label} if label else {},
            "wav": wav.name,
            "trace": name + ".frames.jsonl",
        }
        if review is not None:
            payload["review"] = review
        (audio / (name + ".json")).write_text(json.dumps(payload))
        return audio / (name + ".json")

    def test_unreviewed_known_off_is_not_ground_truth(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_sample(root, event="diagnostic_known_off_activation", label="actually_off")
            queue = list_samples(root)
            self.assertEqual(queue["pending"], 1)
            self.assertEqual(collect_labelled_event_clips(root), [])

    def test_human_review_promotes_sample_to_regression_fixture(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_sample(root)
            review_sample(root, "sample", "playing")
            queue = list_samples(root)
            self.assertEqual(queue["pending"], 0)
            self.assertEqual(queue["reviewed"], 1)
            clips = collect_labelled_event_clips(root)
            self.assertEqual(len(clips), 1)
            self.assertEqual(clips[0][1:], ("music", "playing"))
            self.assertTrue(audio_bytes(root, "sample"))

    def test_legacy_manual_mark_remains_explicit_human_truth(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_sample(root, event="manual_label", label="actually_off")
            queue = list_samples(root)
            self.assertEqual(queue["reviewed"], 1)
            clips = collect_labelled_event_clips(root)
            # Regression deliberately requires the persisted review field; the
            # UI normalises old marks for display but does not silently rewrite.
            self.assertEqual(clips, [])

    def test_legacy_shadow_signature_stays_reviewable(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.make_sample(root)
            payload=json.loads(path.read_text())
            payload["details"]={"production":[True,False,True]}
            path.write_text(json.dumps(payload))
            sample=list_samples(root)["samples"][0]
            self.assertEqual(sample["production"],{"turntable_on":True,"music_active":False,"runout_locked":True})
            self.assertIsNone(sample["production_status"])
            self.assertTrue(audio_bytes(root,"sample"))

    def test_malformed_optional_metadata_does_not_break_queue(self):
        with tempfile.TemporaryDirectory() as root:
            for name,details in [("list-details",[1]),("invalid-production",{"production":["bad"]}),
                                 ("valid",{"production":{"status":"Playing","music_active":True}})]:
                path=self.make_sample(root,name=name)
                payload=json.loads(path.read_text());payload["details"]=details
                path.write_text(json.dumps(payload))
            Path(root,"experiments","event_audio","not-an-object.json").write_text('[]')
            queue=list_samples(root)
            self.assertEqual(queue["pending"],3)
            valid=next(item for item in queue["samples"] if item["id"]=="valid")
            self.assertEqual(valid["production_status"],"Playing")


if __name__ == "__main__":
    unittest.main()
