"""Background artwork rebuild; uses independent per-account store/artwork objects."""
import secrets
import threading
import time

from .errors import AppError


class ArtworkRefresh:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}

    def status(self, account_id, store):
        with self.lock:
            running = self.jobs.get(account_id)
            job = dict(running) if running else store.setting('artwork_refresh_status')
        if job and job.get('state') == 'running' and not running:
            job = dict(job, state='interrupted')
        return job

    def start(self, account_id, store, artwork, enabled=None):
        albums = [a['id'] for a in store.albums(owned=True)]
        if not albums:
            raise AppError('There are no albums on your shelf to refresh.', 409)
        with self.lock:
            if self.jobs.get(account_id, {}).get('state') == 'running':
                raise AppError('An artwork refresh is already running.', 409)
            job = {'id': secrets.token_hex(8), 'state': 'running', 'total': len(albums),
                   'processed': 0, 'fronts': 0, 'backs': 0, 'missing_fronts': 0,
                   'missing_backs': 0, 'failed': 0, 'started_at': int(time.time())}
            self.jobs[account_id] = job
            store.set_setting('artwork_refresh_status', job)
        worker = threading.Thread(target=self._run,
                                  args=(account_id,store,artwork,albums,enabled,job['id']),
                                  name='audioshelf-artwork-refresh',daemon=True)
        worker.start()
        return dict(job)

    def _run(self, account_id, store, artwork, albums, enabled, job_id):
        for album_id in albums:
            if enabled is not None and not enabled(account_id):
                self._finish(account_id,store,job_id,'interrupted')
                return
            results = {'fronts':0,'backs':0,'missing_fronts':0,'missing_backs':0,'failed':0}
            try:
                # Never change custom uploads or an explicit edition choice.
                artwork.evict(album_id)
                try:
                    data, mime, _ = artwork.sized(album_id,320)
                    results['missing_fronts' if mime=='image/svg+xml' else 'fronts'] += 1
                except Exception:
                    results['failed'] += 1
                try:
                    artwork.back_sized(album_id,320)
                    artwork.back_sized(album_id,640)
                    results['backs'] += 1
                except AppError as error:
                    if error.status == 404:
                        results['missing_backs'] += 1
                    else:
                        results['failed'] += 1
                except Exception:
                    results['failed'] += 1
            except Exception:
                results['failed'] += 1
            with self.lock:
                job = self.jobs.get(account_id)
                if not job or job['id'] != job_id:
                    return
                job['processed'] += 1
                for key,value in results.items():
                    job[key] += value
                store.set_setting('artwork_refresh_status',job)
        self._finish(account_id,store,job_id,'complete')

    def _finish(self, account_id, store, job_id, state):
        with self.lock:
            job = self.jobs.get(account_id)
            if job and job['id'] == job_id:
                job['state'] = state
                job['finished_at'] = int(time.time())
                store.set_setting('artwork_refresh_status', job)
