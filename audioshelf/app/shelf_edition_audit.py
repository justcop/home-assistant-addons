"""Read-only audit of every shelf album's MusicBrainz canonical tracklist.

Spotify verification proves playback IDs work; it does not prove that the
chosen MusicBrainz edition represents the original standard album. This
audit flags corroborated excess tracks without modifying any selections.
"""
import re
import secrets
import threading
import time

from .errors import AppError
from .musicbrainz import EXPANSION, standard_release_reference, release_summary_track_count

STATUS = 'original_edition_audit_status_v1'
RESULTS = 'original_edition_audit_results_v1'


def inspect_album(album, releases):
    """Classify a saved edition against independent original-era evidence.

    No selected tracklist, Spotify mapping, review state, artwork, or shelf
    membership is altered. An ambiguous original receives an INCONCLUSIVE
    classification rather than a guessed shorter tracklist.
    """
    result = {'album_id': album['id'],
              'artist': ', '.join(a['name'] for a in album.get('artists', [])),
              'title': album['title'], 'saved_tracks': len(album['tracks']),
              'release_id': album.get('release_id') or '',
              'release_label': album.get('release_label') or '',
              'canonical_reviewed': bool(album.get('canonical_reviewed')),
              'fully_spotify_verified': bool(album.get('playable')),
              'classification': 'consistent', 'reference_count': None,
              'supporting_releases': 0}
    if not album['tracks'] or not album.get('release_id'):
        return {**result, 'classification': 'unresolved',
                'explanation': 'There is no selected canonical release to audit.'}
    reference = standard_release_reference(releases, album.get('release_date') or '')
    selected = next((r for r in releases if r.get('id') == album['release_id']), None)
    selection_label = ((selected.get('title','') + ' ' + selected.get('disambiguation',''))
                       if selected else result['release_label'])
    expanded_label = bool(EXPANSION.search(selection_label))
    if reference:
        result.update(reference_count=reference['track_count'],
                      supporting_releases=reference['supporting_releases'],
                      reference_release_id=reference['reference_release_id'])
        if result['saved_tracks'] > reference['track_count']:
            return {**result, 'classification': 'likely_extras',
                    'excess_count': result['saved_tracks'] - reference['track_count'],
                    'explanation': 'Several independent original-era, non-expanded '
                                   'MusicBrainz releases have a shorter tracklist.'}
        if expanded_label:
            return {**result, 'classification': 'review_edition',
                    'explanation': 'The selected release is labelled as expanded, '
                                   'even though its track count matches the reference.'}
        if result['saved_tracks'] != reference['track_count']:
            return {**result, 'classification': 'inconclusive',
                    'explanation': 'The saved release differs from the corroborated '
                                   'standard length; it might be a legitimate regional variant.'}
        return {**result, 'explanation': 'Track count agrees with corroborated '
                                        'original-era releases, but individual '
                                        'recordings have not been independently audited.'}
    if expanded_label:
        return {**result, 'classification': 'review_edition',
                'explanation': 'The selected edition is labelled expanded or deluxe; '
                               'there is insufficient independent evidence to choose '
                               'a canonical shorter release automatically.'}
    return {**result, 'classification': 'inconclusive',
            'explanation': 'Insufficient corroborating original-era release counts. '
                           'Do not infer that the shortest release is original.'}


def audit_report(store):
    job = store.setting(STATUS, {}) or {}
    entries = store.setting(RESULTS, []) or []
    lines = ['AudioShelf original tracklist integrity audit',
             'Report generated: ' + time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
             'Status: ' + str(job.get('state','not started')),
             'Scanned: ' + str(job.get('processed',0)) + ' / ' + str(job.get('total',0)),
             'Likely bonus/extra editions: ' + str(job.get('likely_extras',0)),
             'Other editions needing review: ' + str(job.get('review_edition',0)),
             'Inconclusive comparisons: ' + str(job.get('inconclusive',0)),
             'Already unresolved: ' + str(job.get('unresolved',0)),
             'Errors: ' + str(job.get('errors',0)),
             '', 'This is a read-only MusicBrainz metadata audit. A complete Spotify match '
             'does NOT prove the original tracklist was chosen. No albums have been edited.',
             '']
    order = {'likely_extras':0, 'review_edition':1, 'inconclusive':2,
             'error':3, 'unresolved':4, 'consistent':5}
    for entry in sorted(entries, key=lambda a:(order.get(a['classification'],9),
                                               a['artist'].casefold(), a['title'].casefold())):
        status = entry['classification']
        # The report includes *all* shelf entries, including apparently
        # consistent ones, so the user can account for the entire collection.
        lines.append(entry['artist'] + ' — ' + entry['title'] + ' [' + status + ']')
        lines.append('  MusicBrainz album: https://musicbrainz.org/release-group/' + entry['album_id'])
        if entry.get('release_id'):
            lines.append('  Selected release: https://musicbrainz.org/release/' + entry['release_id'])
        lines.append('  Saved canonical tracks: ' + str(entry['saved_tracks']) +
                     '; Spotify verified: ' + ('yes' if entry['fully_spotify_verified'] else 'no') +
                     '; reviewed by user: ' + ('yes' if entry['canonical_reviewed'] else 'no'))
        if entry.get('reference_count') is not None:
            lines.append('  Corroborated original-era count: ' + str(entry['reference_count']) +
                         ' (' + str(entry['supporting_releases']) + ' releases)')
            lines.append('  Reference pressing: https://musicbrainz.org/release/' +
                         entry['reference_release_id'])
        if entry.get('excess_count'):
            lines.append('  Possible extra tracks: ' + str(entry['excess_count']))
        lines.append('  Reason: ' + entry.get('explanation',''))
        lines.append('')
    return '\n'.join(lines)


class ShelfEditionAudit:
    def __init__(self):
        self.lock = threading.RLock()
        self.active = {}

    def status(self, account_id, store):
        with self.lock:
            job = self.active.get(account_id)
            persisted = store.setting(STATUS)
            return dict(job) if job else (
                dict(persisted, state='interrupted')
                if persisted and persisted.get('state') == 'running'
                else persisted)

    def start(self, account_id, store, musicbrainz, enabled=None):
        with self.lock:
            if self.active.get(account_id, {}).get('state') == 'running':
                raise AppError('An original-tracklist audit is already running.', 409)
            albums = store.albums(owned=True)
            if not albums:
                raise AppError('There are no shelf albums to audit.', 409)
            job = {'id': secrets.token_hex(8), 'state':'running',
                   'total':len(albums),'processed':0,'likely_extras':0,
                   'review_edition':0,'inconclusive':0,'consistent':0,
                   'unresolved':0,'errors':0,'started_at':int(time.time())}
            store.set_setting(RESULTS, [])
            store.set_setting(STATUS, job)
            self.active[account_id] = job
            worker = threading.Thread(target=self._run,
                args=(account_id, store, musicbrainz,
                      [album['id'] for album in albums], enabled, job['id']),
                name='audioshelf-original-edition-audit', daemon=True)
            worker.start()
            return dict(job)

    def _run(self, account_id, store, musicbrainz, album_ids, enabled, job_id):
        results = []
        for album_id in album_ids:
            album = None
            if enabled is not None and not enabled(account_id):
                self._finish(account_id, store, 'interrupted',
                             'Account disabled during audit.')
                return
            with self.lock:
                if self.active.get(account_id, {}).get('id') != job_id:
                    return
            try:
                album = store.album(album_id)
                if album['tracks'] and album.get('release_id'):
                    releases = musicbrainz.releases(album_id)
                    item = inspect_album(album, releases)
                else:
                    item = inspect_album(album, [])
            except (AppError, OSError, KeyError) as exc:
                item = {'album_id':album_id,
                        'artist': ', '.join(a['name'] for a in album.get('artists', [])) if album is not None else '',
                        'title': album.get('title','') if album is not None else '',
                        'saved_tracks':len(album.get('tracks',[])) if album is not None else 0,
                        'fully_spotify_verified':False, 'canonical_reviewed':False,
                        'classification':'error', 'explanation':str(exc)}
            results.append(item)
            with self.lock:
                job = self.active.get(account_id)
                if not job or job.get('id') != job_id:
                    return
                job['processed'] += 1
                category = 'errors' if item['classification'] == 'error' else item['classification']
                job[category] += 1
                store.set_setting(RESULTS, results)
                store.set_setting(STATUS, job)
        self._finish(account_id, store, 'complete')

    def _finish(self, account_id, store, state, reason=None):
        with self.lock:
            job = self.active.get(account_id)
            if not job:
                return
            job['state'] = state
            job['finished_at'] = int(time.time())
            if reason:
                job['reason'] = reason
            store.set_setting(STATUS, job)
            self.active.pop(account_id, None)
