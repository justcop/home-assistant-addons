"""Server-owned, device-pinned Spotify activation and scoped helper status."""
import copy
import hashlib
import logging
import secrets
import threading
import time

from .errors import AppError
from .playback_audit import inspect_dispatched_tracks

LOG = logging.getLogger('audioshelf')


class PlaybackHandoff:
    def __init__(self, spotify, timeout=60, interval=2):
        self.spotify = spotify
        self.timeout = timeout
        self.interval = interval
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.helper_changed = threading.Condition()
        self.helper_job = None
        self.job = None
        self.worker = None

    def cancel_all(self, reason='cancelled'):
        # A predefined, non-user-controlled reason explains why an otherwise
        # healthy Spotify wake was terminated before Play was sent.
        valid = {'cancelled', 'user_cancelled', 'device_change',
                 'new_playback', 'direct_playback', 'logout', 'spotify_disconnect'}
        if reason not in valid:
            reason = 'cancelled'
        with self.changed:
            if self.job and self.job['state'] == 'waiting':
                self.job['state'] = 'cancelled'
                self.job['phase'] = 'cancelled'
                self.job['cancel_reason'] = reason
                self.job['stop'].set()
                self.publish_helper(self.job)
                self.changed.notify_all()

    @staticmethod
    def snapshot(job):
        # The authenticated PWA can show wake/dispatch progress without polling
        # Spotify's player API or mistaking a submitted command for confirmed play.
        return {key: job[key] for key in ('id', 'state', 'result', 'error', 'phase', 'play_accepted', 'cancel_reason', 'track_audit',
                                       'sdk_queue_count', 'sdk_queue_expected', 'sdk_queue_verified')}

    def start(self, album, disc, preferred, owner, authorized, sdk_single_track=False, sdk_album=False):
        token = secrets.token_urlsafe(32)
        started = time.monotonic()
        with self.changed:
            self.cancel_all('new_playback')
            job = {'id': secrets.token_urlsafe(24), 'state': 'waiting', 'result': None,
                   'error': None, 'cancel_reason': None, 'owner': owner, 'stop': threading.Event(),
                   'phase': 'checking_devices', 'device_checks': 0, 'confirmation_checks': 0,
                   'play_accepted': False, 'device_seen_ms': None, 'play_accepted_ms': None,
                   'last_device_check_ms': None, 'last_device_probe_ms': None,
                   'preparation_retry': None, 'verification': None, 'track_audit': None,
                   'sdk_single_track': sdk_single_track, 'sdk_test_uri': None,
                   'sdk_album': sdk_album, 'sdk_album_uris': None,
                   'sdk_queue_count': 0, 'sdk_queue_expected': 0,
                   'sdk_queue_finished': False, 'sdk_queue_verified': 'pending',
                   'created_monotonic': started,
                   'helper_token_hash': hashlib.sha256(token.encode()).digest(),
                   'deadline': started + self.timeout,
                   'payload': (copy.deepcopy(album), disc, copy.deepcopy(preferred), authorized)}
            self.job = job
            self.publish_helper(job)
            self.changed.notify_all()
            if self.worker is None:
                self.worker = threading.Thread(target=self.drain, daemon=True)
                self.worker.start()
            # Only the initial job creation response contains this capability.
            return dict(self.snapshot(job), helper_token=token)

    def status(self, identifier, owner, cancel=False, reason='cancelled'):
        with self.changed:
            if not self.job or self.job['id'] != identifier or self.job['owner'] != owner:
                raise AppError('That playback request is no longer available.', 404)
            if cancel:
                self.cancel_all(reason)
            return self.snapshot(self.job)

    def publish_helper(self, job):
        # Called with the control lock held. Helper reads use a separate condition,
        # so a slow Spotify Play request cannot block the status/health response.
        with self.helper_changed:
            job['helper_view'] = {key: job[key] for key in
                                  ('state', 'error', 'phase', 'device_checks', 'confirmation_checks',
                                   'play_accepted', 'device_seen_ms', 'play_accepted_ms',
                                   'last_device_check_ms', 'last_device_probe_ms', 'verification',
                                   'preparation_retry', 'cancel_reason', 'track_audit',
                                   'sdk_single_track', 'sdk_test_uri', 'sdk_album',
                                   'sdk_album_uris', 'sdk_queue_count', 'sdk_queue_expected',
                                   'sdk_queue_finished', 'sdk_queue_verified')}
            self.helper_job = job
            self.helper_changed.notify_all()

    def progress(self, job, phase, counter=None, **metrics):
        with self.lock:
            if self.job is job and job['state'] == 'waiting':
                job['phase'] = phase
                if counter:
                    job[counter] += 1
                job.update(metrics)
                self.publish_helper(job)

    def launch_catalogue_audit(self, job, album, disc, device_id):
        """Read-only diagnostics after Play acceptance, never delay confirmation."""
        if not isinstance(album, dict) or not isinstance(album.get('tracks'), list):
            return
        if not isinstance(getattr(self.spotify, 'market', None), str) or not callable(getattr(self.spotify, 'play_tracks', None)):
            return

        def publish(audit):
            with self.lock:
                if self.job is job and job['state'] in ('waiting', 'started', 'unconfirmed'):
                    job['track_audit'] = audit
                    self.publish_helper(job)

        def work():
            try:
                inspect_dispatched_tracks(self.spotify, album, disc, device_id, publish)
            except Exception:
                LOG.exception('Read-only Spotify catalogue audit failed')
                publish({'state': 'failed', 'market': 'unknown', 'track_count': 0,
                         'samples': [], 'phone_visible_after_play': 'unknown',
                         'phone_active_after_play': 'unknown'})
        threading.Thread(target=work, name='audioshelf-track-audit', daemon=True).start()

    def helper_status(self, identifier, bearer, wait=20):
        """Read-only status via a random, single-job capability; never cookies."""
        if not isinstance(bearer, str) or len(bearer) > 128:
            raise AppError('Unknown playback job.', 404)
        supplied = hashlib.sha256(bearer.encode()).digest()
        with self.helper_changed:
            job = self.helper_job
            if not job or job['id'] != identifier or not secrets.compare_digest(supplied, job['helper_token_hash']):
                raise AppError('Unknown playback job.', 404)
            if time.monotonic() > job['deadline'] + 60:
                raise AppError('Playback job is no longer available.', 404)
            if not job['payload'][3]():
                return {'state': 'expired', 'phase': 'expired', 'error': 'Playback authorisation ended.'}
            if job['helper_view']['state'] == 'waiting' and wait:
                self.helper_changed.wait_for(lambda: job['helper_view']['state'] != 'waiting' or self.helper_job is not job,
                                             timeout=max(0, min(float(wait), 20)))
            if self.helper_job is not job:
                raise AppError('Playback job was replaced.', 404)
            if not job['payload'][3]():
                return {'state': 'expired', 'phase': 'expired', 'error': 'Playback authorisation ended.'}
            return dict(job['helper_view'])

    def sdk_queue_report(self, identifier, bearer, count, state):
        """Read queue acknowledgements through one short-lived helper capability."""
        if not isinstance(bearer, str) or len(bearer) > 128:
            raise AppError('Unknown playback job.', 404)
        supplied = hashlib.sha256(bearer.encode()).digest()
        with self.changed:
            job = self.job
            if (not job or job['id'] != identifier or
                    not secrets.compare_digest(supplied, job['helper_token_hash']) or
                    job['state'] != 'waiting' or not job['sdk_album'] or
                    job['sdk_album_uris'] is None or time.monotonic() > job['deadline'] or
                    not job['payload'][3]()):
                raise AppError('Playback job is not accepting SDK queue reports.', 404)
            expected = job['sdk_queue_expected']
            if type(count) is not int or count < 1 or count > expected or state not in ('progress','complete','failed'):
                raise AppError('Invalid SDK queue report.', 400)
            if count < job['sdk_queue_count']:
                raise AppError('SDK queue progress went backwards.', 409)
            if state == 'complete' and count != expected:
                raise AppError('A partial Spotify queue cannot be marked complete.', 409)
            job['sdk_queue_count'] = count
            if state == 'failed':
                self.set_status(job, 'unconfirmed',
                    error='Spotify SDK could not queue the whole album. Playback may be partial; see Android diagnostics.')
                job['stop'].set()
            else:
                if state == 'complete':
                    job['sdk_queue_finished'] = True
                self.progress(job, 'sdk_queue_confirming' if state == 'complete' else 'sdk_queueing')
            return {'accepted': True, 'queued': job['sdk_queue_count'], 'expected': expected}

    def verify_sdk_queue(self, job, tracks):
        """Check actual Spotify next-up order, not merely SDK callbacks."""
        wanted = [t['spotify_id'] for t in tracks[1:]]
        if not wanted:
            return 'matched'
        for attempt in range(4):
            try:
                data = self.spotify.api('GET', 'me/player/queue')
            except AppError as error:
                if error.status in (401, 403, 404, 429):
                    return 'unavailable'
                raise
            items = data.get('queue') if isinstance(data, dict) else None
            if not isinstance(items, list):
                return 'unavailable'
            matched = len(items) >= len(wanted) and all(
                isinstance(items[i], dict) and
                wanted[i] in (items[i].get('id'), (items[i].get('linked_from') or {}).get('id'))
                for i in range(len(wanted)))
            if matched:
                return 'matched'
            if attempt < 3:
                job['stop'].wait(.6)
        return 'mismatch'

    def set_status(self, job, state, result=None, error=None):
        with self.changed:
            if self.job is job and job['state'] == 'waiting':
                job.update(state=state, result=result, error=error, phase='confirmed' if state == 'started' else state)
                self.publish_helper(job)
                self.changed.notify_all()

    def drain(self):
        # Only one worker; replaced job capabilities are invalidated.
        while True:
            with self.lock:
                job = self.job
                if not job or job['state'] != 'waiting':
                    self.worker = None
                    return
            self.run(job, *job['payload'])

    @staticmethod
    def verification_snapshot(state, wanted_device, wanted_track, wanted_title=''):
        """Safe, bounded player evidence for this job's short-lived helper token.

        Spotify track IDs are hashed for correlation. Never publish whole API
        responses, device IDs, account details, OAuth tokens or player URLs.
        Song titles are useful for identifying a stale previous recording.
        """
        def track_tag(value):
            return hashlib.sha256(value.encode('utf-8')).hexdigest()[:10] if isinstance(value, str) and value else None

        def label(value):
            return ''.join(char if char.isprintable() else ' ' for char in value).strip()[:72] if isinstance(value, str) else ''

        state = state if isinstance(state, dict) else {}
        device = state.get('device') if isinstance(state.get('device'), dict) else {}
        item = state.get('item') if isinstance(state.get('item'), dict) else {}
        linked = item.get('linked_from') if isinstance(item.get('linked_from'), dict) else {}
        item_id = item.get('id')
        linked_id = linked.get('id')
        age = None
        timestamp = state.get('timestamp')
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            age = min(24*3600*1000, max(0, int(time.time()*1000-timestamp)))
        return {
            'requested': label(wanted_title),
            'observed': label(item.get('name')),
            'requested_tag': track_tag(wanted_track),
            'observed_tag': track_tag(item_id),
            'linked_tag': track_tag(linked_id),
            'item_type': label(item.get('type'))[:20],
            'is_playing': state.get('is_playing') if isinstance(state.get('is_playing'), bool) else None,
            'device_matches': device.get('id') == wanted_device if device else False,
            'track_matches': wanted_track in (item_id, linked_id) if item else False,
            'player_state_present': bool(state),
            'item_present': bool(item),
            'player_age_ms': age,
        }

    def confirm_playing(self, job, result, guard):
        """Require real player state on the intended device and exact first track."""
        wanted_device = result['device_id']
        wanted_track = result['first_track']['id']
        last_mode_attempt = 0
        while True:
            guard()
            self.progress(job, 'confirming_playback', 'confirmation_checks')
            try:
                state = self.spotify.api('GET', 'me/player')
            except AppError as error:
                if error.status not in (404, 409):
                    raise
                state = {}
            device = state.get('device') or {}
            item = state.get('item') or {}
            track_ids = (item.get('id'), (item.get('linked_from') or {}).get('id'))
            observed = self.verification_snapshot(state, wanted_device, wanted_track,
                                                  result['first_track'].get('title', ''))
            if (state.get('is_playing') is True and device.get('id') == wanted_device
                    and wanted_track in track_ids):
                if result.get('modes_need_confirmation'):
                    # A transfer's GET /me/player response may refer to the
                    # *previous* player. Correct shuffle/repeat only once the
                    # requested phone and first track are demonstrably active.
                    # Mode commands are idempotent and may be retried; the Play
                    # request itself is NEVER automatically repeated.
                    wrong_shuffle = state.get('shuffle_state') is not False
                    wrong_repeat = state.get('repeat_state') != 'off'
                    if wrong_shuffle or wrong_repeat:
                        now = time.monotonic()
                        if now - last_mode_attempt >= 1:
                            last_mode_attempt = now
                            self.progress(job, 'preparing_playback_modes')
                            params = {'device_id': wanted_device}
                            for endpoint, needed, value in (
                                ('me/player/shuffle', wrong_shuffle, 'false'),
                                ('me/player/repeat', wrong_repeat, 'off'),
                            ):
                                if not needed:
                                    continue
                                guard()
                                try:
                                    self.spotify.api('PUT', endpoint, dict(params, state=value))
                                except AppError as error:
                                    # A just-awoken Spotify device can still
                                    # reject mode updates transiently. Poll and
                                    # retry the idempotent mode command only.
                                    if error.status not in (404, 409):
                                        raise
                        self.progress(job, 'confirming_modes', verification=observed)
                        now = time.monotonic()
                        interval = self.confirmation_poll_interval(
                            now - (job.get('play_accepted_monotonic') or job['created_monotonic']))
                        job['stop'].wait(min(interval, max(0, job['deadline'] - now)))
                        continue
                if job['sdk_album']:
                    if not job['sdk_queue_finished']:
                        self.progress(job, 'sdk_queueing', verification=observed)
                        now = time.monotonic()
                        job['stop'].wait(min(.5, max(0, job['deadline'] - now)))
                        continue
                    verification = self.verify_sdk_queue(job, result['canonical_tracks'])
                    self.progress(job, 'sdk_queue_confirming', sdk_queue_verified=verification)
                    if verification == 'mismatch':
                        self.set_status(job, 'unconfirmed',
                            error='Spotify queue order did not match the selected album. Check Android diagnostics.')
                        return
                    result['queue_verified'] = verification == 'matched'
                    result.pop('canonical_tracks', None)
                    result['sdk_album'] = True
                # This verified observation is authoritative for the PWA's
                # initial PLAYING state. Don't require a second Spotify read.
                self.progress(job, 'confirmed', verification=observed)
                self.set_status(job, 'started', result=result)
                return
            # A device response without an item is NOT evidence of another
            # track; Spotify may be waking up and have no player track yet.
            phase = ('no_player_state' if not state or not item
                     else 'wrong_device' if device.get('id') != wanted_device
                     else 'wrong_track' if wanted_track not in track_ids else 'paused')
            self.progress(job, phase, verification=observed)
            now = time.monotonic()
            accepted_at = job.get('play_accepted_monotonic') or job['created_monotonic']
            interval = self.confirmation_poll_interval(now - accepted_at)
            job['stop'].wait(min(interval, max(0, job['deadline'] - now)))

    def confirmation_poll_interval(self, elapsed):
        """Verify Spotify promptly after Play, then back off on slow devices."""
        target = 0.5 if elapsed < 10 else 1.0 if elapsed < 20 else self.interval
        return min(self.interval, target)

    def device_poll_interval(self, elapsed):
        """Favor low-latency discovery early, without busy-looping slow phones."""
        target = 0.5 if elapsed < 5 else 1.0 if elapsed < 15 else self.interval
        return min(self.interval, target)

    def run(self, job, album, disc, preferred, authorized):
        command_sent = False

        def guard():
            if job['stop'].is_set() or time.monotonic() >= job['deadline'] or not authorized():
                raise AppError('Playback request expired or was cancelled.', 410)

        def dispatch(send):
            nonlocal command_sent
            with self.lock:
                guard()
                self.progress(job, 'sending_play')
                try:
                    response = send()
                finally:
                    # An ambiguous transport failure must never requeue the record.
                    command_sent = True
                # Only a successful Spotify API response means Play was accepted.
                # Keep confirming the exact device/track after the helper returns.
                accepted_at = time.monotonic()
                job['play_accepted_monotonic'] = accepted_at
                self.progress(job, 'play_accepted', play_accepted=True,
                              play_accepted_ms=int((accepted_at - job['created_monotonic']) * 1000))
                return response

        while not job['stop'].is_set():
            try:
                guard()
                self.progress(job, 'checking_devices', 'device_checks')
                probe_started = time.monotonic()
                devices = self.spotify.devices()
                checked_at = time.monotonic()
                probe_metrics = {
                    'last_device_check_ms': int((checked_at - job['created_monotonic']) * 1000),
                    'last_device_probe_ms': int((checked_at - probe_started) * 1000),
                }
                ready = [d for d in devices if d['id'] == preferred['id']]
                if not ready:
                    ready = [d for d in devices if d['name'] == preferred['name'] and d['type'] == preferred['type']]
                if len(ready) == 1 and not ready[0].get('is_restricted'):
                    guard()
                    self.progress(job, 'preparing_playback',
                                  device_seen_ms=probe_metrics['last_device_check_ms'], **probe_metrics)
                    if job['sdk_album']:
                        # Exact canonical disc/side filter; never dispatch Web API Play.
                        tracks, uris = self.spotify.play_tracks(album, disc)
                        guard()
                        result = {'started': True, 'started_at': time.time(), 'track_count': len(uris),
                                  'device': ready[0].get('name', 'Spotify'), 'device_id': ready[0]['id'],
                                  'first_track': {'id': tracks[0]['spotify_id'], 'title': tracks[0]['title'],
                                                  'duration_ms': tracks[0].get('duration_ms')},
                                  'canonical_tracks': tracks,
                                  'sdk_album': True, 'modes_need_confirmation': True}
                        self.progress(job, 'sdk_queue_ready', sdk_album_uris=uris,
                                      sdk_queue_expected=len(uris))
                    elif job['sdk_single_track']:
                        # Intentionally *no* Web API Play or queue. Let the
                        # connected Android App Remote SDK play only the exact
                        # first canonical track and verify independently here.
                        tracks, uris = self.spotify.play_tracks(album, disc)
                        guard()
                        result = {'started': True, 'started_at': time.time(), 'track_count': 1,
                                  'device': ready[0].get('name', 'Spotify'), 'device_id': ready[0]['id'],
                                  'first_track': {'id': tracks[0]['spotify_id'], 'title': tracks[0]['title'],
                                                  'duration_ms': tracks[0].get('duration_ms')},
                                  'sdk_single_track': True, 'modes_need_confirmation': False}
                        self.progress(job, 'sdk_waiting_for_local_play', sdk_test_uri=uris[0])
                    else:
                        result = self.spotify.play(
                            album, disc, preferred_device=preferred, resolved_device=ready[0],
                            guard=guard, dispatch=dispatch,
                            progress=lambda phase: self.progress(job, phase))
                        self.launch_catalogue_audit(job, album, disc, result['device_id'])
                    self.confirm_playing(job, result, guard)
                    return
                self.progress(job, 'waiting_for_device', **probe_metrics)
            except AppError as error:
                if command_sent:
                    self.set_status(job, 'unconfirmed', error='Spotify may have received Play, but the requested track was not confirmed on your phone. Check Spotify.')
                    return
                if error.status not in (404, 409):
                    self.set_status(job, 'expired' if error.status == 410 else 'failed', error=str(error))
                    return
                # The old handoff quietly retried from device discovery,
                # obscuring failures inside Spotify's player preparation.
                # Only emit a safe, server-authored stage/code classification.
                stage = job.get('phase', '')
                reason = ('device_lost' if stage == 'preparing_device_check'
                          else 'transfer_unconfirmed' if stage in (
                              'preparing_transfer_ack', 'preparing_transfer_fallback')
                          else 'player_not_ready' if stage == 'preparing_player_state'
                          else 'preparation_unavailable')
                self.progress(job, 'waiting_for_device', preparation_retry=reason)
            except Exception:
                LOG.exception('Background Spotify playback failed')
                self.set_status(job, 'unconfirmed' if command_sent else 'failed',
                                error='Spotify may have received Play, but confirmation failed. Check your phone.' if command_sent
                                else 'Spotify playback could not be started. Please retry.')
                return
            # 0-5 seconds: 500 ms; 5-15 seconds: 1 second; then normal interval.
            # Respect configured shorter intervals, cancellation and job expiry.
            now = time.monotonic()
            interval = self.device_poll_interval(now - job['created_monotonic'])
            job['stop'].wait(min(interval, max(0, job['deadline'] - now)))
