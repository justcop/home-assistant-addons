"""Bounded server-owned Spotify activation, independent of browser visibility."""
import copy
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
        self.job = None
        self.worker = None

    def cancel_all(self):
        with self.lock:
            if self.job and self.job['state'] == 'waiting':
                self.job['state'] = 'cancelled'
                self.job['stop'].set()

    def snapshot(self, job):
        return {key: job[key] for key in ('id', 'state', 'result', 'error')}

    def start(self, album, disc, preferred, owner, authorized):
        with self.lock:
            self.cancel_all()
            job = {'id': secrets.token_urlsafe(24), 'state': 'waiting', 'result': None,
                   'error': None, 'owner': owner, 'stop': threading.Event(),
                   'deadline': time.monotonic() + self.timeout,
                   'payload': (copy.deepcopy(album), disc, copy.deepcopy(preferred), authorized)}
            self.job = job
            if self.worker is None:
                self.worker = threading.Thread(target=self.drain, daemon=True)
                self.worker.start()
            return self.snapshot(job)

    def status(self, identifier, owner, cancel=False):
        with self.lock:
            if not self.job or self.job['id'] != identifier or self.job['owner'] != owner:
                raise AppError('That playback request is no longer available.', 404)
            if cancel:
                self.cancel_all()
            return self.snapshot(self.job)

    def drain(self):
        # At most one worker and one pending request, even if Play is pressed repeatedly.
        while True:
            with self.lock:
                job = self.job
                if not job or job['state'] != 'waiting':
                    self.worker = None
                    return
            self.run(job, *job['payload'])

    def run(self, job, album, disc, preferred, authorized):
        def guard():
            if job['stop'].is_set() or time.monotonic() >= job['deadline'] or not authorized():
                raise AppError('Playback request expired or was cancelled.', 410)
        def dispatch(send):
            # Once sent to Spotify a command cannot be recalled; cancel all waiting work.
            with self.lock:
                guard()
                return send()
        while not job['stop'].is_set():
            try:
                guard()
                # Pin the chosen device; an active speaker must never become a fallback.
                devices = self.spotify.devices()
                ready = [d for d in devices if d['id'] == preferred['id']]
                if not ready:
                    ready = [d for d in devices if d['name'] == preferred['name'] and d['type'] == preferred['type']]
                if len(ready) == 1 and not ready[0].get('is_restricted'):
                    guard()
                    result = self.spotify.play(album, disc, preferred_device=preferred, guard=guard, dispatch=dispatch)
                    with self.lock:
                        job.update(state='started', result=result)
                    return
            except AppError as error:
                if error.status not in (404, 409):
                    with self.lock:
                        if job['state'] == 'waiting':
                            job.update(state='expired' if error.status == 410 else 'failed', error=str(error))
                    return
            except Exception:
                LOG.exception('Background Spotify playback failed')
                with self.lock:
                    job.update(state='failed', error='Spotify playback could not be started. Please retry.')
                return
            job['stop'].wait(min(self.interval, max(0, job['deadline'] - time.monotonic())))
