"""User-declared observation modes. Evidence collection never changes detection."""
import json
import os
import queue
import time
import uuid
from pathlib import Path

MODES = ('normal', 'known_off', 'listening_session')
MODE_NAMES = {'normal': 'Normal', 'known_off': 'Known off', 'listening_session': 'Listening session'}


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    os.replace(temporary, path)


class DiagnosticMonitor:
    def __init__(self, root, thresholds, capture, configured_mode='normal', version='', source=''):
        self.root = Path(root) / 'experiments' / 'diagnostic_sessions'
        self.state_path = self.root.parent / 'diagnostic_mode.json'
        self.thresholds = dict(thresholds)
        self.capture = capture
        self.configured_mode = configured_mode if configured_mode in MODES else 'normal'
        self.version, self.source = version, source
        self.notifications = queue.SimpleQueue()
        self.mode = 'normal'
        self.session = None
        self.previous = None
        self.track = None
        self.last_music = -1e12
        self.last_drop = None
        self.break_events = []
        self.in_long_break = False
        self.expected_boundary_until = -1e12
        self.intentional_until = -1e12
        selected = self.configured_mode
        try:
            state = json.loads(self.state_path.read_text())
            if state.get('configured_mode') == selected and state.get('mode') in MODES:
                selected = state['mode']
        except (OSError, ValueError):
            pass
        self.set_mode(selected, time.time())

    def set_mode(self, mode, now):
        if mode not in MODES:
            return False
        if mode == self.mode and self.session is not None:
            return True
        if self.session is not None:
            self.session['ended_unix'] = now
            self.session['end_reason'] = 'mode_changed'
            self._save()
        self.mode = mode
        self.previous = self.track = self.session = None
        self.last_music = self.intentional_until = self.expected_boundary_until = -1e12
        self.last_drop = None
        self.break_events = []
        self.in_long_break = False
        if mode != 'normal':
            self.session = {'id': uuid.uuid4().hex, 'mode': mode, 'started_unix': now,
                            'addon_version': self.version, 'audio_source': self.source,
                            'thresholds': self.thresholds, 'events': [], 'event_count': 0,
                            'captured_count': 0, 'label_policy': 'All persistent diagnostic modes are capture hints only. Human review after capture is required before a sample becomes ground truth.'}
            self._save()
            self._prune_sessions()
        save_json(self.state_path, {'mode': mode, 'configured_mode': self.configured_mode})
        return True

    def _save(self):
        if self.session:
            save_json(self.root / (self.session['id'] + '.json'), self.session)

    def _prune_sessions(self):
        for path in sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime)[:-30]:
            path.unlink()

    def notify(self, kind, track=None, now=None):
        self.notifications.put((kind, dict(track or {}), time.time() if now is None else now))

    def _note(self, kind, now, **details):
        if not self.session:
            return
        self.session['event_count'] += 1
        self.session['events'].append(dict(event=kind, unix_time=now, **details))
        self.session['events'] = self.session['events'][-500:]
        self._save()

    def drain(self, now):
        while not self.notifications.empty():
            kind, track, stamp = self.notifications.get()
            if self.session and stamp < self.session['started_unix']:
                continue
            if kind == 'confirmed_track':
                if track.get('recognition_status', 'confirmed') != 'confirmed':
                    continue
                self.track = track
                self._note(kind, stamp, track=track)
            elif kind == 'scrobble_requested':
                self._note(kind, stamp, track=track)
            elif kind == 'intentional_action':
                self.intentional_until = now + 45
                self.track = None
                self._note(kind, stamp, applies_from_unix=now-20, applies_until_unix=now+45)
            elif kind == 'finish_session':
                if self.mode == 'listening_session' and self.session:
                    self.session['user_confirmed_session_complete'] = True
                    self._note(kind, stamp)
                    self.set_mode('normal', now)

    def _event(self, kind, now, frame, **details):
        payload = dict(diagnostic_session=self.session['id'], mode=self.mode,
                       production=dict(frame), confirmed_track=self.track,
                       intentional_action_active=now <= self.intentional_until,
                       thresholds=self.thresholds, addon_version=self.version,
                       audio_source=self.source, **details)
        if self.mode == 'known_off':
            payload['suggested_label'] = 'actually_off'
            payload['hint_only'] = True
        payload['review_required'] = True
        saved = self.capture('diagnostic_' + kind, now, label=None, details=payload, min_gap_sec=3)
        if saved:
            self.session['captured_count'] += 1
        self._note(kind, now, capture_requested=saved, **details)

    def observe(self, frame, now):
        self.drain(now)
        if not self.session:
            self.previous = dict(frame)
            return self.summary()
        previous = self.previous or {}
        flags = ('turntable_on', 'music_active', 'runout_locked')
        if self.mode == 'known_off':
            rising = [field for field in flags if frame.get(field) and not previous.get(field)]
            if rising:
                self._event('known_off_activation', now, frame, sensors=rising,
                            expected_state='Powered Off', review_required=True)
        else:
            if self.last_drop is not None and now-self.last_drop >= 30:
                self.session['expected_long_breaks'] = self.session.get('expected_long_breaks', 0) + 1
                self._note('expected_long_break', now, break_started_unix=self.last_drop,
                           capture_times=list(self.break_events), review_required=False)
                self.last_drop = None
                self.break_events = []
                self.in_long_break = True
            if frame.get('music_active'):
                self.in_long_break = False
            remaining = None
            if self.track and self.track.get('duration_known') and self.track.get('duration', 0) > 0:
                remaining = self.track['start_timestamp'] + self.track['duration'] - now
            if remaining is not None and remaining < -20:
                remaining = None  # Old track duration no longer describes this passage.
            if frame.get('music_active') and not previous.get('music_active'):
                self.expected_boundary_until = -1e12
            music_drop = previous.get('music_active') and not frame.get('music_active')
            power_drop = previous.get('turntable_on') and not frame.get('turntable_on')
            early_runout = not previous.get('runout_locked') and frame.get('runout_locked')
            # Expected track endings support a side-change grace period. Timings
            # never supply frame labels or suppress the production detector.
            if music_drop and remaining is not None and -20 <= remaining <= 8:
                self.expected_boundary_until = now + 45
                self._note('expected_track_boundary', now, seconds_remaining=remaining)
            grace = now <= max(self.intentional_until, self.expected_boundary_until)
            reasons = []
            if not grace and not self.in_long_break:
                if power_drop and now - self.last_music < 90:
                    reasons.append('power_lost_during_listening')
                if music_drop and (remaining is None or remaining > 8):
                    reasons.append('music_dropped_before_expected_end' if remaining is not None else 'music_drop_without_track_timing')
                if early_runout and now - self.last_music < 90 and (remaining is None or remaining > 8):
                    reasons.append('runout_before_expected_end' if remaining is not None else 'runout_without_track_timing')
            if reasons:
                self._event('listening_anomaly', now, frame, reasons=reasons,
                            seconds_remaining=remaining, review_required=True)
                if self.last_drop is None:
                    self.last_drop = now
                self.break_events.append(now)
            if frame.get('music_active'):
                if self.last_drop is not None and now-self.last_drop <= 30:
                    self._event('listening_recovery', now, frame, review_required=True,
                                seconds_after_drop=now-self.last_drop)
                    self.last_drop = None
                    self.break_events = []
                self.last_music = now
        self.previous = dict(frame)
        return self.summary()

    def summary(self):
        return {'mode': self.mode, 'session_id': self.session['id'] if self.session else None,
                'captured_count': self.session['captured_count'] if self.session else 0,
                'expected_long_breaks': self.session.get('expected_long_breaks',0) if self.session else 0,
                'report_folder': str(self.root)}
