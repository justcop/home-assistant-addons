"""Server-owned, device-pinned Spotify activation and scoped helper status."""
import copy
import hashlib
import logging
import secrets
import threading
import time

from .errors import AppError

LOG = logging.getLogger('audioshelf')


class PlaybackHandoff:
    def __init__(self, spotify, timeout=60, interval=2):
        self.spotify = spotify
        self.timeout = timeout
        self.interval = interval
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.job = None
        self.worker = None

    def cancel_all(self):
        with self.changed:
            if self.job and self.job['state'] == 'waiting':
                self.job['state'] = 'cancelled'
                self.job['stop'].set()
                self.changed.notify_all()

    @staticmethod
    def snapshot(job):
        return {key: job[key] for key in ('id', 'state', 'result', 'error')}

    def start(self, album, disc, preferred, owner, authorized):
        token = secrets.token_urlsafe(32)
        with self.changed:
            self.cancel_all()
            job = {'id': secrets.token_urlsafe(24), 'state': 'waiting', 'result': None,
                   'error': None, 'owner': owner, 'stop': threading.Event(),
                   'helper_token_hash': hashlib.sha256(token.encode()).digest(),
                   'deadline': time.monotonic() + self.timeout,
                   'payload': (copy.deepcopy(album), disc, copy.deepcopy(preferred), authorized)}
            self.job = job
            self.changed.notify_all()
            if self.worker is None:
                self.worker = threading.Thread(target=self.drain, daemon=True)
                self.worker.start()
            # Only the initial job creation response contains this capability.
            return dict(self.snapshot(job), helper_token=token)

    def status(self, identifier, owner, cancel=False):
        with self.changed:
            if not self.job or self.job['id'] != identifier or self.job['owner'] != owner:
                raise AppError('That playback request is no longer available.', 404)
            if cancel:
                self.cancel_all()
            return self.snapshot(self.job)

    def helper_status(self, identifier, bearer, wait=20):
        """Read-only status via a random, single-job capability; never cookies."""
        if not isinstance(bearer, str) or len(bearer) > 128:
            raise AppError('Unknown playback job.', 404)
        supplied = hashlib.sha256(bearer.encode()).digest()
        with self.changed:
            job = self.job
            if not job or job['id'] != identifier or not secrets.compare_digest(supplied, job['helper_token_hash']):
                raise AppError('Unknown playback job.', 404)
            if time.monotonic() > job['deadline'] + 60:
                raise AppError('Playback job is no longer available.', 404)
            if not job['payload'][4]():
                return {'state': 'expired', 'error': 'Playback authorisation ended.'}
            if job['state'] == 'waiting':
                self.changed.wait_for(lambda: job['state'] != 'waiting' or self.job is not job,
                                      timeout=max(0, min(float(wait), 20)))
            if self.job is not job:
                raise AppError('Playback job was replaced.', 404)
            return {'state': job['state'], 'error': job['error']}

    def set_status(self, job, state, result=None, error=None):
        with self.changed:
            if self.job is job and job['state'] == 'waiting':
                job.update(state=state, result=result, error=error)
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

    def confirm_playing(self, job, result, guard):
        """Require real player state on the intended device and exact first track."""
        wanted_device = result['device_id']
        wanted_track = result['first_track']['id']
        while True:
            guard()
            try:
                state = self.spotify.api('GET', 'me/player')
            except AppError as error:
                if error.status not in (404, 409):
                    raise
                state = {}
            device = state.get('device') or {}
            item = state.get('item') or {}
            track_ids = (item.get('id'), (item.get('linked_from') or {}).get('id'))
            if (state.get('is_playing') is True and device.get('id') == wanted_device
                    and wanted_track in track_ids):
                self.set_status(job, 'started', result=result)
                return
            job['stop'].wait(min(self.interval, max(0, job['deadline'] - time.monotonic())))

    def run(self, job, album, disc, preferred, authorized):
        command_sent = False

        def guard():
            if job['stop'].is_set() or time.monotonic() >= job['deadline'] or not authorized():
                raise AppError('Playback request expired or was cancelled.', 410)

        def dispatch(send):
            nonlocal command_sent
            with self.lock:
                guard()
                try:
                    return send()
                finally:
                    # An ambiguous transport failure must never requeue the record.
                    command_sent = True

        while not job['stop'].is_set():
            try:
                guard()
                devices = self.spotify.devices()
                ready = [d for d in devices if d['id'] == preferred['id']]
                if not ready:
                    ready = [d for d in devices if d['name'] == preferred['name'] and d['type'] == preferred['type']]
                if len(ready) == 1 and not ready[0].get('is_restricted'):
                    guard()
                    result = self.spotify.play(album, disc, preferred_device=preferred, guard=guard, dispatch=dispatch)
                    self.confirm_playing(job, result, guard)
                    return
            except AppError as error:
                if command_sent:
                    self.set_status(job, 'unconfirmed', error='Spotify may have received Play, but the requested track was not confirmed on your phone. Check Spotify.')
                    return
                if error.status not in (404, 409):
                    self.set_status(job, 'expired' if error.status == 410 else 'failed', error=str(error))
                    return
            except Exception:
                LOG.exception('Background Spotify playback failed')
                self.set_status(job, 'unconfirmed' if command_sent else 'failed',
                                error='Spotify may have received Play, but confirmation failed. Check your phone.' if command_sent
                                else 'Spotify playback could not be started. Please retry.')
                return
            job['stop'].wait(min(self.interval, max(0, job['deadline'] - time.monotonic())))
