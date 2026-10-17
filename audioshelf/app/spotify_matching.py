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
from difflib import SequenceMatcher

from .matching import candidate, normalize
from .musicbrainz import original_release_candidates, release_summary_track_count

LOG = logging.getLogger('audioshelf.spotify_matching')
ATTEMPTS = 'spotify_matching_attempts_v4'
FAILURES = 'spotify_matching_failures_v1'
STATUS = 'spotify_matching_status_v1'
PAUSE_UNTIL = 'spotify_matching_pause_until_v1'


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
              'verified_tracks': 0, 'spotify_candidate': '', 'tracks': [],
              'musicbrainz_release_id': album.get('release_id'),
              'musicbrainz_release_label': album.get('release_label', ''),
              'canonical_reviewed': bool(album.get('canonical_reviewed')),
              'disc_count': len({t.get('disc_number') or 1 for t in album['tracks']})}
    if best is not None:
        result['spotify_candidate'] = best.get('name', '')
        result['verified_tracks'] = best.get('verified', 0)
        for track, mapping in zip(album['tracks'], best.get('mappings', [])):
            if mapping.get('verified'):
                continue
            assessment = mapping.get('assessment') or {}
            alternatives = mapping.get('alternatives') or []
            alternative = next((a for a in alternatives if a.get('verified')), None)
            if assessment.get('reason') == 'no_ordered_match':
                if alternative:
                    reason = 'matching_track_out_of_order'
                else:
                    alternative = next((a for a in alternatives if a.get('score', 0) > 0), None)
                    if alternative is None:
                        # A zero-score live/demo/cover can still be a useful
                        # diagnostic, but a random unrelated title is not.
                        possible = next(iter(alternatives), None)
                        if possible and SequenceMatcher(None, normalize(track['title'], True),
                                        normalize(possible.get('title', ''), True)).ratio() >= .65:
                            alternative = possible
                    reason = (alternative.get('reason', 'no_ordered_match') if alternative
                              else 'no_compatible_track_in_this_spotify_edition')
            else:
                reason = assessment.get('reason') or 'no_verified_match'
            result['tracks'].append({
                'title': track['title'], 'reason': reason,
                'spotify_title': mapping.get('spotify_title') or (alternative or {}).get('title', ''),
                'spotify_position': (alternative or {}).get('spotify_position'),
                'duration_delta_ms': (assessment.get('duration_delta_ms') if mapping.get('spotify_id')
                                      else (alternative or {}).get('duration_delta_ms'))})
    return result


def report_text(store, job):
    failures = current_failures(store)
    lines = ['AudioShelf bulk Spotify matching report',
             'Generated: ' + time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
             'Market: ' + str(job.get('market', 'unknown') if job else 'unknown'),
             'Background job: ' + str((job or {}).get('state', 'not started')),
             'Job interruption: ' + str((job or {}).get('reason', 'none')),
             'Albums still requiring review: ' + str(len(failures)), '']
    for issue in sorted(failures.values(), key=lambda x: (x.get('artist', '').casefold(),
                                                          x.get('title', '').casefold())):
        def plain(value):
            return ' '.join(str(value or '').split())
        lines.append(plain(issue.get('artist')) + ' — ' + plain(issue.get('title')))
        lines.append('  Album ID: ' + plain(issue.get('album_id')))
        if issue.get('musicbrainz_release_id'):
            lines.append('  Canonical MusicBrainz edition: ' + plain(issue.get('musicbrainz_release_label'))
                         + ' (' + plain(issue['musicbrainz_release_id']) + ')')
        if 'canonical_reviewed' in issue:
            lines.append('  Canonical tracks: ' + str(issue.get('total_tracks', 0))
                         + ' across ' + str(issue.get('disc_count', 0)) + ' disc(s)'
                         + '; ' + ('reviewed' if issue['canonical_reviewed'] else 'not yet reviewed'))
        if 'examined_releases' in issue:
            lines.append('  MusicBrainz search: ' + str(issue['examined_releases'])
                         + ' editions assessed; ' + str(issue.get('skipped_releases', 0))
                         + ' filtered or unusable; ' + str(issue.get('total_releases', 0))
                         + ' eligible editions found')
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
            if track.get('spotify_position'):
                details += '; Spotify position: ' + str(track['spotify_position'])
            lines.append('    • ' + plain(track.get('title')) + ': ' + details)
        lines.append('')
    if not failures:
        lines.append('No unresolved albums recorded.')
    return '\n'.join(lines)


def current_failures(store):
    saved = store.setting(FAILURES, {}) or {}
    active = {}
    for album_id, issue in saved.items():
        try:
            if eligible(store.album(album_id)):
                active[album_id] = issue
        except KeyError:
            pass
    if len(active) != len(saved):
        store.set_setting(FAILURES, active)
    return active


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
            album = store.album(row['id'])
            if not eligible(album):
                continue
            if force or attempted.get(album['id']) != signature(album, spotify.market):
                result.append(album['id'])
        return result

    def status(self, account_id, store):
        with self.lock:
            active = self.jobs.get(account_id)
            job = dict(active) if active else store.setting(STATUS)
        if job and job.get('state') == 'running' and not active:
            job = dict(job, state='interrupted')
        return {'job': job, 'failures': len(current_failures(store))}

    def ensure_started(self, account_id, store, spotify, playable, enabled=None):
        if not spotify.connected or time.time() < (store.setting(PAUSE_UNTIL, 0) or 0):
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
            if not automatic:
                store.set_setting(PAUSE_UNTIL, 0)
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
        consecutive_outages = 0
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
                else:
                    # Manual choices and user-reviewed MusicBrainz pressings do
                    # not bypass the complete Spotify match requirement.
                    # Search official alternatives before invalidating either.
                    verified, issue = self._find_verified_edition(album, playable)
                    with store.catalogue_lock:
                        fresh = store.album(album_id)
                        if (not eligible(fresh) or
                                signature(fresh, spotify.market) != signature(album, spotify.market)):
                            outcome, issue = 'skipped', None
                        elif verified:
                            release_id, spotify_id = verified
                            # Preserve the user's confirmation only when the
                            # exact same MusicBrainz pressing is still valid.
                            # Switching to a different release needs a new review.
                            still_reviewed = bool(
                                fresh.get('canonical_reviewed') and
                                fresh.get('release_id') == release_id)
                            playable.select(album_id, release_id, spotify_id,
                                            reviewed=still_reviewed)
                            outcome, issue = 'matched', None
                        else:
                            # No tracklist, including a manually selected or
                            # previously reviewed one, is valid without an
                            # entirely verified Spotify edition.
                            cleared = store.clear_unverified_edition(
                                album_id, expected_release=album.get('release_id'),
                                include_manual=True)
                            if not cleared:
                                outcome, issue = 'skipped', None
                            else:
                                if issue:
                                    issue['previous_manual_selection_cleared'] = bool(
                                        album.get('canonical_reviewed') or
                                        any(t.get('method') == 'manual'
                                            for t in album['tracks']))
                                outcome = 'unmatched'
                attempted = store.setting(ATTEMPTS, {}) or {}
                attempted[album_id] = signature(store.album(album_id), spotify.market)
                store.set_setting(ATTEMPTS, attempted)
                consecutive_outages = 0
            except AppError as error:
                if error.status in (401, 403, 429):
                    store.set_setting(PAUSE_UNTIL, time.time() + (3600 if error.status in (401, 403) else 1800))
                    self._finish(account_id, store, job_id, 'interrupted', str(error))
                    return
                if error.status >= 500:
                    consecutive_outages += 1
                    if consecutive_outages >= 3:
                        store.set_setting(PAUSE_UNTIL, time.time() + 900)
                        self._finish(account_id, store, job_id, 'interrupted', 'Spotify temporarily unavailable after three consecutive errors.')
                        return
                else:
                    consecutive_outages = 0
                try:
                    album = store.album(album_id)
                    issue = failure(album, str(error))
                except KeyError:
                    issue = None
                outcome = 'errors'
                attempted = store.setting(ATTEMPTS, {}) or {}
                attempted[album_id] = signature(album, spotify.market)
                store.set_setting(ATTEMPTS, attempted)
            except Exception:
                LOG.exception('Unexpected error matching Spotify album %s', album_id)
                album = store.album(album_id)
                issue = failure(album, 'Unexpected matcher error. Check the AudioShelf add-on log.')
                outcome = 'errors'
                attempted = store.setting(ATTEMPTS, {}) or {}
                attempted[album_id] = signature(album, spotify.market)
                store.set_setting(ATTEMPTS, attempted)
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
        # New Last.fm imports can arrive while this pass is running. Pick up
        # newly collected albums without needing another browser visit.
        self.start(account_id, store, spotify, playable,
                   enabled=enabled, automatic=True)

    @staticmethod
    def _find_verified_edition(album, playable):
        """Find one complete original tracklist without writing speculative tracks.

        Search alternate official, permitted MusicBrainz releases on the
        background thread. Group and rank using edition metadata before
        network-intensive inspection; a nearby Spotify candidate alone
        cannot determine the canonical tracks. A limit avoids indefinite
        scans when a release group contains thousands of reissues.
        """
        mb = playable.musicbrainz
        candidates = original_release_candidates(
            mb.releases(album['id']), album.get('release_date', ''),
            playable.store.release_filters(album['id']))
        examined, skipped, best = 0, 0, None
        # Prefer standard / original pressings; postpone expanded media and
        # excessive track counts but do not assume a country is intrinsically
        # more canonical than another.
        for release in candidates:
            if examined >= 80:
                break
            count = release_summary_track_count(release)
            if count is not None and count > 100:
                skipped += 1
                continue
            try:
                probe = playable.inspect(album['id'], release['id'])
            except AppError as error:
                if error.status >= 500 or error.status == 429:
                    raise
                skipped += 1
                continue
            if len(probe['tracks']) > 100:
                skipped += 1
                continue
            examined += 1
            matches, assessments = playable.verified_choices(probe, album)
            if matches:
                return (release['id'], matches[0]['id']), None
            if assessments:
                top = max(assessments, key=lambda c: (c['verified'], c['matched'], c['score']))
                if best is None or (top['verified']/max(1,len(probe['tracks'])), top['verified']) > (
                        best[1]['verified']/max(1,len(best[0]['tracks'])), best[1]['verified']):
                    best = (probe, top)
        total = len(candidates)
        reason = ('No fully Spotify-verified original MusicBrainz tracklist after checking '
                  + str(examined) + ' allowed official editions'
                  + (' (scan capped before all ' + str(total) + ' editions).' if examined >= 80 and total > examined + skipped else '.')
                  + ' The album stays on your shelf unresolved; its unverified tracklist is not retained.')
        failed_album, failed_choice = best if best else (album, None)
        issue = failure(failed_album, reason, failed_choice)
        issue['examined_releases'] = examined
        issue['skipped_releases'] = skipped
        issue['total_releases'] = total
        return None, issue

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
        if best and len(album['tracks']) > best.get('track_count', 0) and (
                best['verified'] >= 6 and best['verified'] >= best.get('track_count', 0) * .7):
            reason = ('The saved MusicBrainz canonical edition has more tracks than the '
                      'closest Spotify album; check whether a bonus/deluxe release was '
                      'mistakenly selected. The stored edition was not changed.')
        else:
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
