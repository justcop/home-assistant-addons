import io
import json
import sys
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from diagnostic_monitor import DiagnosticMonitor
from diagnostic_reports import export_diagnostics
from experiment import ExperimentHarness, EventAudioRecorder
from regression import collect_labelled_event_clips


def frame(power=False, music=False, runout=False):
    return dict(turntable_on=power,music_active=music,runout_locked=runout,
                status='Playing' if music else 'Runout Groove' if runout else 'Motor Idle' if power else 'Powered Off')


class DiagnosticPolicyTests(unittest.TestCase):
    def monitor(self, root, mode):
        captured=[]
        def capture(kind,now,**details):
            captured.append(dict(kind=kind,now=now,**details))
            return True
        monitor=DiagnosticMonitor(root,{'detector_version':5},capture,version='test',source='USB')
        monitor.set_mode(mode,1000)
        return monitor,captured

    def test_known_off_captures_each_sensor_including_music_without_power(self):
        with tempfile.TemporaryDirectory() as root:
            monitor,captures=self.monitor(root,'known_off')
            monitor.observe(frame(),1001)
            monitor.observe(frame(music=True),1002)
            monitor.observe(frame(music=True),1003)
            monitor.observe(frame(music=True,runout=True),1004)
            monitor.observe(frame(power=True,music=True,runout=True),1005)
            self.assertEqual(len(captures),3)
            self.assertEqual([c['details']['sensors'] for c in captures],[['music_active'],['runout_locked'],['turntable_on']])
            self.assertTrue(all(c['label']=='actually_off' for c in captures))

    def test_early_drop_and_recovery_are_review_candidates(self):
        with tempfile.TemporaryDirectory() as root:
            monitor,captures=self.monitor(root,'listening_session')
            track=dict(title='Example',start_timestamp=1000,duration=180,duration_known=True)
            monitor.notify('confirmed_track',track,1001)
            monitor.observe(frame(True,True),1002)
            monitor.observe(frame(),1030)
            monitor.observe(frame(True,True),1035)
            self.assertEqual(len(captures),2)
            self.assertIsNone(captures[0]['label'])
            self.assertIn('power_lost_during_listening',captures[0]['details']['reasons'])
            self.assertEqual(captures[0]['details']['seconds_remaining'],150)
            self.assertEqual(captures[1]['kind'],'diagnostic_listening_recovery')

    def test_expected_track_end_then_flip_is_not_early_shutdown(self):
        with tempfile.TemporaryDirectory() as root:
            monitor,captures=self.monitor(root,'listening_session')
            monitor.notify('confirmed_track',dict(start_timestamp=1000,duration=60,duration_known=True),1001)
            monitor.observe(frame(True,True),1050)
            monitor.observe(frame(True),1056)
            monitor.observe(frame(True,runout=True),1060)
            monitor.observe(frame(),1065)
            self.assertFalse(captures)
            self.assertIsNone(monitor.session.get('label'))

    def test_new_music_clears_previous_track_end_grace(self):
        with tempfile.TemporaryDirectory() as root:
            monitor,captures=self.monitor(root,'listening_session')
            monitor.notify('confirmed_track',dict(start_timestamp=1000,duration=60,duration_known=True),1001)
            monitor.observe(frame(True,True),1050)
            monitor.observe(frame(True),1056)
            monitor.observe(frame(True,True),1060)
            monitor.notify('confirmed_track',dict(start_timestamp=1060,duration=180,duration_known=True),1061)
            monitor.observe(frame(),1062)
            self.assertTrue(captures)
            self.assertIn('power_lost_during_listening',captures[0]['details']['reasons'])

    def test_expired_track_timing_is_not_reused_for_new_unrecognised_music(self):
        with tempfile.TemporaryDirectory() as root:
            monitor,captures=self.monitor(root,'listening_session')
            monitor.notify('confirmed_track',dict(start_timestamp=1000,duration=60,duration_known=True),1001)
            monitor.observe(frame(True,True),1200)
            monitor.observe(frame(True),1205)
            self.assertIn('music_drop_without_track_timing',captures[0]['details']['reasons'])

    def test_unknown_timing_does_not_invent_music_labels(self):
        with tempfile.TemporaryDirectory() as root:
            monitor,captures=self.monitor(root,'listening_session')
            monitor.observe(frame(True,True),1001)
            monitor.observe(frame(True),1005)
            self.assertIn('music_drop_without_track_timing',captures[0]['details']['reasons'])
            self.assertIsNone(captures[0]['label'])

    def test_intentional_pause_annotation_and_completed_session(self):
        with tempfile.TemporaryDirectory() as root:
            monitor,captures=self.monitor(root,'listening_session')
            sid=monitor.session['id']
            monitor.observe(frame(True,True),1001)
            monitor.notify('intentional_action',now=1002)
            monitor.observe(frame(),1003)
            self.assertFalse(captures)
            monitor.notify('finish_session',now=1010)
            monitor.observe(frame(),1010)
            saved=json.loads((Path(root)/'experiments'/'diagnostic_sessions'/(sid+'.json')).read_text())
            self.assertTrue(saved['user_confirmed_session_complete'])
            self.assertEqual(monitor.mode,'normal')
            note=next(e for e in saved['events'] if e['event']=='intentional_action')
            self.assertEqual(note['applies_from_unix'],983)

    def test_long_side_break_is_classified_expected_without_recovery_alarm(self):
        with tempfile.TemporaryDirectory() as root:
            monitor,captures=self.monitor(root,'listening_session')
            monitor.observe(frame(True,True),1001)
            monitor.observe(frame(),1002)
            monitor.observe(frame(),1040)
            monitor.observe(frame(True,True),1050)
            expected=next(e for e in monitor.session['events'] if e['event']=='expected_long_break')
            self.assertEqual(expected['capture_times'],[1002])
            self.assertFalse(expected['review_required'])
            self.assertEqual(len(captures),1)

    def test_long_gap_with_missing_audio_frames_is_still_expected(self):
        with tempfile.TemporaryDirectory() as root:
            monitor,captures=self.monitor(root,'listening_session')
            monitor.observe(frame(True,True),1001)
            monitor.observe(frame(),1002)
            monitor.observe(frame(True,True),1100)
            self.assertEqual(monitor.session['expected_long_breaks'],1)
            self.assertEqual(len(captures),1)

    def test_live_mode_persists_and_new_config_overrides(self):
        with tempfile.TemporaryDirectory() as root:
            monitor,_=self.monitor(root,'known_off')
            restored=DiagnosticMonitor(root,{},lambda *a,**k:True)
            self.assertEqual(restored.mode,'known_off')
            changed=DiagnosticMonitor(root,{},lambda *a,**k:True,configured_mode='listening_session')
            self.assertEqual(changed.mode,'listening_session')

    def test_scrobble_is_a_timeline_hint_not_ground_truth(self):
        with tempfile.TemporaryDirectory() as root:
            monitor,captures=self.monitor(root,'listening_session')
            monitor.notify('scrobble_requested',dict(title='Example'),1002)
            monitor.observe(frame(),1003)
            self.assertEqual(monitor.session['events'][0]['event'],'scrobble_requested')
            self.assertFalse(captures)


class DiagnosticCaptureTests(unittest.TestCase):
    def test_context_alignment_partial_flush_and_regression_import(self):
        with tempfile.TemporaryDirectory() as root:
            audio=EventAudioRecorder(root,rate=4,channels=1,chunk=4,pre_roll_sec=2,post_roll_sec=2)
            for i in range(3): audio.feed(bytes([i,0])*4,{'index':i})
            audio.trigger('diagnostic_known_off_activation',10,label='actually_off',details={'diagnostic_session':'session'})
            audio.feed(b'\x04\0'*4,{'index':3})
            completed=audio.flush()
            self.assertEqual(len(completed),1)
            path=Path(completed[0][1])
            meta=json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(meta['pre_roll_sec'],2)
            self.assertEqual(meta['post_roll_sec'],1)
            rows=[json.loads(row) for row in path.with_suffix('.frames.jsonl').read_text().splitlines()]
            self.assertEqual([row['index'] for row in rows],[1,2,3])
            with wave.open(str(path)) as wav:self.assertEqual(wav.getnframes(),12)
            self.assertEqual(collect_labelled_event_clips(root)[0][1],'off')
            Path(root,'options.json').write_text('private-api-key')
            archive=zipfile.ZipFile(io.BytesIO(export_diagnostics(root)))
            self.assertNotIn('options.json',archive.namelist())
            self.assertEqual(json.loads(archive.read('manifest.json'))['included_clips'],1)
            small=zipfile.ZipFile(io.BytesIO(export_diagnostics(root,max_bytes=1)))
            self.assertEqual(json.loads(small.read('manifest.json'))['included_clips'],0)

    def test_long_break_clips_are_excluded_from_suspicious_download(self):
        with tempfile.TemporaryDirectory() as root:
            audio=EventAudioRecorder(root,rate=4,channels=1,chunk=4)
            audio.feed(b'\0\0'*4)
            audio.trigger('diagnostic_listening_anomaly',1002,details={'diagnostic_session':'example'})
            audio.flush()
            sessions=Path(root)/'experiments'/'diagnostic_sessions'
            sessions.mkdir()
            (sessions/'example.json').write_text(json.dumps({'id':'example','events':[
                {'event':'expected_long_break','capture_times':[1002]}]}))
            archive=zipfile.ZipFile(io.BytesIO(export_diagnostics(root)))
            manifest=json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['included_clips'],0)
            self.assertEqual(manifest['excluded_long_break_clips'],1)
            self.assertFalse(any(p.endswith('.wav') for p in archive.namelist()))

    def test_mode_change_flushes_off_before_later_playback(self):
        with tempfile.TemporaryDirectory() as root:
            harness=ExperimentHarness(root,{},rate=44100,channels=1,chunk=2048,auto_capture=False)
            harness.set_diagnostic_mode('known_off',1000)
            data=b'\0\0'*2048
            harness.observe(data,1001,frame(power=True),'IDLE')
            self.assertEqual(len(harness.audio.pending),1)
            harness.set_diagnostic_mode('listening_session',1002)
            self.assertFalse(harness.audio.pending)
            self.assertFalse(harness.audio.ring)
            sidecar=next((Path(root)/'experiments'/'event_audio').glob('*.json'))
            meta=json.loads(sidecar.read_text())
            self.assertEqual(meta['post_roll_sec'],0)
            self.assertEqual(meta['label'],'actually_off')
            self.assertIsNone(harness.trusted_label(1003))

    def test_observation_preserves_production_frame(self):
        with tempfile.TemporaryDirectory() as root:
            harness=ExperimentHarness(root,{},rate=44100,channels=1,chunk=2048,auto_capture=False)
            harness.set_diagnostic_mode('listening_session',1000)
            original=frame(True,True)
            expected=dict(original)
            result=harness.observe(b'\0\0'*2048,1001,original,'SLEEPING')
            self.assertEqual(original,expected)
            self.assertEqual(result['diagnostics']['mode'],'listening_session')
