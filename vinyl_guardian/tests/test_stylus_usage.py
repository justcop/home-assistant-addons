import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from stylus_usage import StylusUsage


class StylusUsageTests(unittest.TestCase):
    def test_counts_playing_and_runout_once_and_restores_after_restart(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'usage.json'
            usage=StylusUsage(path)
            usage.observe({'status':'Playing','runout_locked':True},44100,44100)
            usage.observe({'status':'Runout Groove'},88200,44100)
            for status in ('Between Tracks','Motor Idle','Powered Off'):
                usage.observe({'status':status},44100,44100)
            restored=StylusUsage(path)
            self.assertEqual(restored.music_seconds,1)
            self.assertEqual(restored.runout_seconds,2)
            self.assertEqual(restored.total_seconds,3)
            self.assertEqual(restored.hours,3/3600)

    def test_downtime_and_wall_clock_changes_never_add_use(self):
        with tempfile.TemporaryDirectory() as root:
            clock=[100]
            usage=StylusUsage(Path(root)/'usage.json',clock=lambda:clock[0])
            clock[0]=100000
            usage.observe({'status':'Playing'},2048,44100)
            self.assertAlmostEqual(usage.total_seconds,2048/44100)
            usage=StylusUsage(Path(root)/'usage.json',clock=lambda:clock[0])
            self.assertAlmostEqual(usage.total_seconds,2048/44100)

    def test_known_off_excludes_false_music_and_runout(self):
        with tempfile.TemporaryDirectory() as root:
            usage=StylusUsage(Path(root)/'usage.json')
            usage.observe({'status':'Playing'},44100,44100,known_off=True)
            usage.observe({'status':'Runout Groove'},44100,44100,known_off=True)
            self.assertEqual(usage.total_seconds,0)

    def test_minute_checkpoint_and_shutdown_flush(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'usage.json'
            clock=[0]
            usage=StylusUsage(path,clock=lambda:clock[0])
            usage.observe({'status':'Playing'},44100,44100)
            self.assertEqual(json.loads(path.read_text())['music_seconds'],0)
            self.assertEqual(usage.saved_hours,0)
            clock[0]=60
            usage.observe({'status':'Playing'},44100,44100)
            self.assertEqual(json.loads(path.read_text())['music_seconds'],2)
            self.assertEqual(usage.saved_hours,2/3600)
            usage.observe({'status':'Runout Groove'},44100,44100)
            usage.flush()
            self.assertEqual(StylusUsage(path).total_seconds,3)

    def test_corrupt_primary_recovers_without_reset_and_corrupt_both_stops(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'usage.json'
            usage=StylusUsage(path)
            usage.observe({'status':'Playing'},44100*120,44100)
            usage.flush()
            path.write_text('corrupted')
            restored=StylusUsage(path)
            self.assertTrue(restored.recovered)
            self.assertEqual(restored.total_seconds,120)
            path.write_text('corrupted')
            restored.backup.write_text('also corrupted')
            with self.assertRaisesRegex(ValueError,'refusing to reset'):
                StylusUsage(path)

    def test_interrupted_save_uses_newest_valid_copy(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'usage.json'
            usage=StylusUsage(path)
            usage.backup.write_text(json.dumps({'format_version':1,'music_seconds':123,'runout_seconds':4}))
            restored=StylusUsage(path)
            self.assertEqual(restored.total_seconds,127)

    def test_invalid_saved_numbers_are_not_accepted(self):
        for value in (-1,float('nan'),float('inf'),'1',True):
            with tempfile.TemporaryDirectory() as root:
                path=Path(root)/'usage.json'
                path.write_text(json.dumps({'format_version':1,'music_seconds':value,'runout_seconds':0}))
                with self.assertRaises(ValueError): StylusUsage(path)
