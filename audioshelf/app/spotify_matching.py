"""Account-scoped, resumable bulk Spotify matching of shelf albums.

Only complete, independently verified mappings are saved. Failed albums and
track-level mismatch reasons remain available after restarts for copy/paste.
"""
import hashlib
import json
import logging
import secrets
import threading
import time

from .errors import AppError
from .matching import candidate

LOG = logging.getLogger('audioshelf.spotify_matching')
ATTEMPTS = 'spotify_matching_attempts_v1'
FAILURES = 'spotify_matching_failures_v1'
STATUS = 'spotify_matching_status_v1'


def eligible(album):
    return bool(album['on_shelf']) and not (
        album.get('spotify_album_id') and album['tracks'] and
        all(track.get('spotify_id') and track.get('verified') for track in album['tracks']))


def signature(album, market):
    # Retry on a new canonical release, corrections to the tracklist or changes
    # in manual mappings. Do not repeatedly poll a failed unchanged album.
    data = [market, album.get('release_id'), [
        (t.get('title'), t.get('recording_id'), t.get('duration_ms'),
         t.get('method') if t.get('method') == 'manual' else '',
         t.get('spotify_id') if t.get('method') == 'manual' else '')
        for t in album['tracks']]]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()[:24]


def failure(album, reason, best=None):
    result = {'album_id': album['id'], 'artist': ', '.join(a['name'] for a in album['artists']),
              'title': album['title'], 'reason': reason, 'total_tracks': len(album['tracks']),
              'verified_tracks': 0, 'spotify_candidate': '', 'tracks': []}
    if best is not None:
        result['spotify_candidate'] = best.get('name', '')
        result['verified_tracks'] = best.get('verified', 0)
        for track, mapping in zip(album['tracks'], best.get('mappings', [])):
            if mapping.get('verified'):
                continue
            assessment = mapping.get('assessment') or {}
            alternative = (mapping.get('alternatives') or [{}])[0] if mapping.get('alternatives') else {}
            result['tracks'].append({
                'title': track['title'],
                'reason': assessment.get('reason') if assessment.get('reason') not in (None, 'no_ordered_match')
                          else alternative.get('reason', 'no_ordered_match'),
                'spotify_title': mapping.get('spotify_title') or alternative.get('title', ''),
                'duration_delta_ms': assessment.get('duration_delta_ms')})
    return result


def report_text(store, job):
    failures = store.setting(FAILURES, {}) or {}
    lines = ['AudioShelf bulk Spotify matching report',
             'Generated: ' + time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
             'Market: ' + str(job.get('market', 'unknown') if job else 'unknown'),
             'Background job: ' + str((job or {}).get('state', 'not started')),
             'Albums still requiring review: ' + str(len(failures)), '']
    for issue in sorted(failures.values(), key=lambda x: (x.get('artist', '').casefold(),
                                                          x.get('title', '').casefold())):
        def plain(value):
            return ' '.join(str(value or '').split())
        lines.append(plain(issue.get('artist')) + ' — ' + plain(issue.get('title')))
        lines.append('  Album ID: ' + plain(issue.get('album_id')))
        lines.append('  Reason: ' + plain(issue.get('reason')))
        if issue.get('spotify_candidate'):
            lines.append('  Best Spotify candidate: ' + plain(issue['spotify_candidate'])
                         + ' (' + str(issue.get('verified_tracks', 0))
                         + '/' + str(issue.get('total_tracks', 0)) + ' verified)')
        for track in issue.get('tracks', []):
            details = track.get('reason', 'unknown')
            if track.get('spotify_title'):
                details += '; Spotify: ' + plain(track['spotify_title'])
            if track.get('duration_delta_ms') is not None:
                details += '; duration difference: ' + str(track['duration_delta_ms']) + ' ms'
            lines.append('    • ' + plain(track.get('title')) + ': ' + details)
        lines.append('')
    if not failures:
        lines.append('No unresolved albums recorded.')
    return '\n'.join(lines)


class SpotifyMatching:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}
        self.last_check = {}

    @staticmethod
    def pending(store, spotify, force=False):
        attempted = store.setting(ATTEMPTS, {}) or {}
        result = []
        for row in store.albums(owned=True):
            if not eligible(row):
                continue
            album = store.album(row['id'])
            if force or attempted.get(album['id']) != signature(album, spotify.market):
                result.append(album['id'])
        return result

    def status(self, account_id, store):
        with self.lock:
            active = self.jobs.get(account_id)
            job = dict(active) if active else store.setting(STATUS)
        if job and job.get('state') == 'running' and not active:
            job = dict(job, state='interrupted')
        return {'job': job, 'failures': len(store.setting(FAILURES, {}) or {})}

    def ensure_started(self, account_id, store, spotify, playable, enabled=None):
        if not spotify.connected:
            return None
        with self.lock:
            if self.jobs.get(account_id, {}).get('state') == 'running':
                return None
            # A status call occurs on most page navigations: throttle scans.
            now = time.monotonic()
            if now - self.last_check.get(account_id, -1e10) < 10:
                return None
            self.last_check[account_id] = now
        return self.start(account_id, store, spotify, playable, enabled=enabled, automatic=True)

    def start(self, account_id, store, spotify, playable, enabled=None,
              automatic=False, force=False):
        if not spotify.connected:
            if automatic:
                return None
            raise AppError('Connect Spotify before matching your shelf.', 409)
        with self.lock:
            if self.jobs.get(account_id, {}).get('state') == 'running':
                if automatic:
                    return None
                raise AppError('Spotify background matching is already running.', 409)
            albums = self.pending(store, spotify, force=force)
            if not albums:
                if automatic:
                    return None
                raise AppError('No unmatched albums need scanning.', 409)
            job = {'id': secrets.token_hex(8), 'state': 'running', 'total': len(albums),
                   'processed': 0, 'matched': 0, 'unmatched': 0, 'errors': 0,
                   'skipped': 0, 'market': spotify.market, 'started_at': int(time.time())}
            self.jobs[account_id] = job
            store.set_setting(STATUS, job)
            thread = threading.Thread(target=self._run,
                  args=(account_id, store, spotify, playable, albums, enabled, job['id']),
                  name='audioshelf-spotify-matching', daemon=True)
            thread.start()
            return dict(job)

    def _run(self, account_id, store, spotify, playable, albums, enabled, job_id):
        for album_id in albums:
            if enabled is not None and not enabled(account_id):
                self._finish(account_id, store, job_id, 'interrupted', 'Account disabled.')
                return
            if not spotify.connected:
                self._finish(account_id, store, job_id, 'interrupted', 'Spotify disconnected.')
                return
            try:
                album = store.album(album_id)
                if not album['on_shelf'] or not eligible(album):
                    outcome, issue = 'skipped', None
                elif not album['tracks']:
                    outcome, issue = 'unmatched', failure(album, 'No canonical MusicBrainz tracklist is saved.')
                elif any(t.get('method') == 'manual' and t.get('spotify_id') for t in album['tracks']):
                    outcome, issue = 'unmatched', failure(album, 'Manual Spotify track choices present; automatic replacement disabled.')
                else:
                    choice, issue = self._match(album, spotify, playable)
                    if choice is None:
                        outcome = 'unmatched'
                    else:
                        # Only lock for the write, not for slow Spotify searches.
                        with store.catalogue_lock:
                            fresh = store.album(album_id)
                            if (eligible(fresh) and fresh.get('release_id') == album.get('release_id')
                                    and signature(fresh, spotify.market) == signature(album, spotify.market)
                                    and not any(t.get('method') == 'manual' and t.get('spotify_id')
                                                for t in fresh['tracks'])):
                                store.mapping(album_id, choice)
                                outcome, issue = 'matched', None
                            else:
                                outcome, issue = 'skipped', None
                attempted = store.setting(ATTEMPTS, {}) or {}
                attempted[album_id] = signature(store.album(album_id), spotify.market)
                store.set_setting(ATTEMPTS, attempted)
            except AppError as error:
                if error.status in (401, 403, 429):
                    self._finish(account_id, store, job_id, 'interrupted', str(error))
                    return
                try:
                    album = store.album(album_id)
                    issue = failure(album, str(error))
                except KeyError:
                    issue = None
                outcome = 'errors'
            except Exception:
                LOG.exception('Unexpected error matching Spotify album %s', album_id)
                album = store.album(album_id)
                issue = failure(album, 'Unexpected matcher error. Check the AudioShelf add-on log.')
                outcome = 'errors'
            with self.lock:
                job = self.jobs.get(account_id)
                if not job or job['id'] != job_id:
                    return
                failures = store.setting(FAILURES, {}) or {}
                if issue:
                    failures[album_id] = issue
                else:
                    failures.pop(album_id, None)
                store.set_setting(FAILURES, failures)
                job['processed'] += 1
                job[outcome] += 1
                job['last_album'] = album_id
                store.set_setting(STATUS, job)
            # Give regular user requests priority and avoid spiky API activity.
            time.sleep(0.2)
        self._finish(account_id, store, job_id, 'complete')

    @staticmethod
    def _match(album, spotify, playable):
        candidates = spotify.candidates(album)
        by_id = {c['id']: c for c in candidates}
        matches = [c for c in by_id.values() if playable.fully_verified(c, len(album['tracks']))]
        if not matches:
            for extra in spotify.candidates(album, broad=True):
                by_id.setdefault(extra['id'], extra)
            matches = [c for c in by_id.values() if playable.fully_verified(c, len(album['tracks']))]
        if not matches and by_id:
            best = max(by_id.values(), key=lambda c: (c['verified'], c['matched'], c['score']))
            missing = len(album['tracks']) - best['verified']
            if best['verified'] > 0 and 0 < missing <= 2:
                enriched = playable.enrich_probe(album, best)
                if enriched is not album:
                    assessments = [candidate(enriched, spotify.album(k)) for k in by_id]
                    matches = [c for c in assessments if playable.fully_verified(c, len(album['tracks']))]
                    if not matches:
                        by_id = {c['id']: c for c in assessments}
        if matches:
            return max(matches, key=lambda c: c['score']), None
        best = max(by_id.values(), key=lambda c: (c['verified'], c['matched'], c['score'])) if by_id else None
        reason = ('No Spotify album candidates found in this market.' if not best
                  else 'No candidate verified every canonical track in the correct order.')
        return None, failure(album, reason, best)

    def _finish(self, account_id, store, job_id, state, reason=None):
        with self.lock:
            job = self.jobs.get(account_id)
            if job and job['id'] == job_id:
                job['state'] = state
                job['finished_at'] = int(time.time())
                if reason:
                    job['reason'] = reason
                store.set_setting(STATUS, job)
