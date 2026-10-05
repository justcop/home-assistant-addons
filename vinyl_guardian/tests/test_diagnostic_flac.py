import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagnostic_audio import write_flac, open_pcm
from experiment import EventAudioRecorder
from review_queue import list_samples, audio_bytes, audio_content_type
from diagnostic_reports import export_diagnostics
from regression import collect_labelled_event_clips, _read_wav
from replay_lab import _dataset_audio_paths
import zipfile


class FlacTests(unittest.TestCase):
    def test_exact_pcm_roundtrip_and_mixed_legacy_consumers(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root, 'experiments', 'event_audio')
            directory.mkdir(parents=True)
            # Different stereo channels, negative values and full-scale extrema.
            raw = np.random.default_rng(19).integers(-32768,32768,(4096,2),dtype=np.int16).tobytes()
            flac = directory / 'new.flac'
            write_flac(flac, raw, 44100, 2)
            with open_pcm(flac) as reader:
                self.assertEqual((reader.getframerate(),reader.getnchannels(),reader.getsampwidth()),(44100,2,2))
                self.assertEqual(reader.readframes(reader.getnframes()),raw)
            legacy = directory / 'old.wav'
            with wave.open(str(legacy),'wb') as reader:
                reader.setparams((2,2,44100,0,'NONE','not compressed'))
                reader.writeframes(raw)
            for path, key in [(flac,'audio'),(legacy,'wav')]:
                path.with_suffix('.json').write_text(json.dumps({key:path.name,'trace':path.stem+'.frames.jsonl',
                    'event':'manual_label','trigger_time':10,'review':{'status':'reviewed','reviewed_label':'playing'}}))
                path.with_suffix('.frames.jsonl').write_text('{}\n')
                self.assertEqual(_read_wav(str(path))[0],raw)
                self.assertEqual(audio_bytes(root,path.stem),path.read_bytes())
            self.assertEqual(audio_content_type(root,'new'),'audio/flac')
            self.assertEqual(audio_content_type(root,'old'),'audio/wav')
            self.assertEqual(len(list_samples(root)['samples']),2)
            self.assertEqual(len(collect_labelled_event_clips(root)),2)
            self.assertEqual(len(_dataset_audio_paths(str(directory))[0]),2)
            archive=zipfile.ZipFile(io.BytesIO(export_diagnostics(root)))
            self.assertEqual(json.loads(archive.read('manifest.json'))['included_clips'],2)
            self.assertIn('event_audio/new.flac',archive.namelist())
            self.assertIn('event_audio/old.wav',archive.namelist())

    def test_failed_encoder_leaves_no_capture_or_temporary_audio(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root,'test.flac')
            with patch('diagnostic_audio.subprocess.run',side_effect=RuntimeError('encoder failed')):
                with self.assertRaises(RuntimeError):write_flac(path,b'\0\0'*8,44100,1)
            self.assertFalse(path.exists())
            self.assertFalse(Path(str(path)+'.tmp').exists())

    def test_retention_counts_both_formats_and_preserves_reviewed_flac(self):
        with tempfile.TemporaryDirectory() as root:
            recorder = EventAudioRecorder(root,44100,1,2048,max_files=10)
            directory = Path(recorder.root)
            directory.mkdir(parents=True)
            for index in range(12):
                path=directory / (str(index)+('.flac' if index%2 else '.wav'))
                path.write_bytes(b'audio')
                path.with_suffix('.json').write_text(json.dumps({'review':{'status':'pending'}}))
                path.with_suffix('.frames.jsonl').write_text('{}')
                import os
                os.utime(path,(index+100,index+100))
            reviewed=directory/'reviewed.flac'
            reviewed.write_bytes(b'audio')
            reviewed.with_suffix('.json').write_text(json.dumps({'review':{'status':'reviewed'}}))
            recorder._prune()
            self.assertTrue(reviewed.exists())
            self.assertFalse((directory/'0.wav').exists())
            self.assertFalse((directory/'1.flac').exists())
            self.assertFalse((directory/'1.frames.jsonl').exists())
            self.assertEqual(len(list(directory.glob('*.flac')))+len(list(directory.glob('*.wav'))),11)
