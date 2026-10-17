"""Opt-in Last.fm album-history import for a single AudioShelf account.

Last.fm's public listening history needs an API key, but not the user's
password or access to their scrobbling account. Imports run on a daemon worker
and never make an unverified name match silently.
"""
import hashlib
import re
import secrets
import threading
import time

import requests

from .errors import AppError
from .matching import normalize
from .musicbrainz import mbid

HOST = 'https://ws.audioscrobbler.com/2.0/'
PERIODS = {'overall', '12month', '6month', '3month'}
MAX_PREVIEW = 500
MAX_BATCH = 250
USERNAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}')
API_KEY = re.compile(r'[a-fA-F0-9]{32}')

# Edition labels do not identify separate studio albums. Subtitles such as
# Re:Wired, ...Naked and colour-album descriptions are deliberately preserved.
EDITION_LABEL = (
    r'(?:super\s+deluxe|deluxe(?:\s+(?:edition|version))?|'
    r're-?master(?:ed)?(?:\s+\d{4})?|(?:\d{4}\s+)?re-?master(?:ed)?|'
    r'(?:\d+(?:st|nd|rd|th)\s+)?anniversary(?:\s+(?:special\s+)?edition)?|'
    r'reissue|promo(?:tional)?|(?:disc|cd)\s*\d+)'
)
EDITION_SUFFIX = re.compile(r'\s*[\[(]\s*' + EDITION_LABEL + r'\s*[\])]\s*$', re.I)
CD_SUFFIX = re.compile(r'\s+CD\s*\d+\s*$', re.I)
EPONYM_SUFFIX = re.compile(r'^(.+?)\s*\([^)]{1,60}\)\s*$')


def same_artist(left, right):
    a, b = normalize(left), normalize(right)
    return a == b or (a.startswith('the ') and a[4:] == b) or (b.startswith('the ') and b[4:] == a)


def album_title_variants(title, artist):
    """Offer conservative catalogue titles without conflating distinct albums."""
    clean = title.strip()
    for _ in range(4):
        without_label = CD_SUFFIX.sub('', EDITION_SUFFIX.sub('', clean)).strip()
        if without_label == clean:
            break
        clean = without_label
    variants = [clean, title.strip()]
    parenthetical = EPONYM_SUFFIX.match(clean)
    if parenthetical and same_artist(parenthetical.group(1), artist):
        # Only resolve an informal descriptor on an eponymous album when
        # exactly one eligible MusicBrainz release group exists.
        variants.insert(0, parenthetical.group(1))
    if clean.endswith(' I') and same_artist(clean[:-2], artist):
        variants.insert(0, clean[:-2])
    result, seen = [], set()
    for variant in variants:
        key = normalize(variant)
        if key and key not in seen:
            result.append(variant)
            seen.add(key)
    return result


def album_key(artist, title):
    return hashlib.sha256((normalize(artist) + '\n' + normalize(title)).encode()).hexdigest()[:24]


def owned_pairs(store):
    return {(normalize(a['name']), normalize(record['title']))
            for record in store.albums(owned=True) for a in record['artists']}


class LastFmImporter:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}

    @staticmethod
    def configuration(store):
        value = store.setting('lastfm_account') or {}
        return value if isinstance(value, dict) else {}

    def status(self, store, account_id):
        config = self.configuration(store)
        with self.lock:
            running = self.jobs.get(account_id)
            result = dict(running) if running else store.setting('lastfm_import_status')
        if result and result.get('state') == 'running' and not running:
            result = dict(result, state='interrupted')
        return {'connected': bool(config.get('username') and config.get('api_key')),
                'username': config.get('username', ''), 'job': result}

    @staticmethod
    def call(config, method, **params):
        try:
            response = requests.get(HOST, params={
                'method': method, 'user': config['username'], 'api_key': config['api_key'],
                'format': 'json', **params}, timeout=(5, 20), allow_redirects=False)
        except requests.RequestException:
            raise AppError('Last.fm could not be reached. Please try again.', 502) from None
        if response.status_code == 429:
            raise AppError('Last.fm is rate limiting requests. Try again later.', 429)
        if not response.ok or response.status_code in {301, 302, 303, 307, 308}:
            raise AppError('Last.fm is temporarily unavailable.', 502)
        try:
            body = response.json()
        except ValueError:
            raise AppError('Last.fm returned an unreadable response.', 502) from None
        if not isinstance(body, dict):
            raise AppError('Last.fm returned an unexpected response.', 502)
        if 'error' in body:
            # Do not echo untrusted server text or API keys to the user.
            raise AppError('Last.fm rejected the username or API key. Check your details.', 400)
        return body

    def connect(self, store, username, api_key):
        if (not isinstance(username, str) or not USERNAME.fullmatch(username) or
                not isinstance(api_key, str) or not API_KEY.fullmatch(api_key)):
            raise AppError('Enter your Last.fm username and a 32-character Last.fm API key.')
        config = {'username': username, 'api_key': api_key.lower()}
        response = self.call(config, 'user.getinfo')
        user = response.get('user', {})
        if not isinstance(user, dict) or user.get('name', '').casefold() != username.casefold():
            raise AppError('Last.fm did not confirm that username.')
        store.set_setting('lastfm_account', config)
        return self.status(store, 'unused')

    def disconnect(self, store, account_id):
        with self.lock:
            if self.jobs.get(account_id, {}).get('state') == 'running':
                raise AppError('Wait for your current import to finish before disconnecting.', 409)
        store.set_setting('lastfm_account', None)

    def preview(self, store, period):
        if period not in PERIODS:
            raise AppError('Choose an available listening period.')
        config = self.configuration(store)
        if not config.get('username') or not config.get('api_key'):
            raise AppError('Connect Last.fm in Settings first.', 409)
        fingerprint = hashlib.sha256((config['username'].casefold() + ':' + config['api_key']).encode()).hexdigest()
        key = 'lastfm-topalbums-v2:' + fingerprint + ':' + period
        rows = store.cache_get(key)
        if rows is None:
            rows, seen = [], set()
            for page in range(1, 6):
                result = self.call(config, 'user.gettopalbums', period=period, limit=100, page=page)
                wrapper = result.get('topalbums', {})
                items = wrapper.get('album', []) if isinstance(wrapper, dict) else []
                if isinstance(items, dict):
                    items = [items]
                if not isinstance(items, list):
                    raise AppError('Last.fm returned an unreadable album list.', 502)
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = item.get('name')
                    credited = item.get('artist')
                    artist = credited.get('name') if isinstance(credited, dict) else credited
                    if not isinstance(name, str) or not isinstance(artist, str):
                        continue
                    name, artist = name.strip()[:200], artist.strip()[:200]
                    if not name or not artist or artist.casefold() == 'various artists':
                        continue
                    identity = album_key(artist, name)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    try:
                        plays = max(0, int(item.get('playcount', 0)))
                    except (TypeError, ValueError):
                        plays = 0
                    mb = item.get('mbid', '')
                    rows.append({'id': identity, 'title': name, 'artist': artist,
                                 'plays': plays, 'mbid': mb if isinstance(mb, str) else ''})
                    if len(rows) >= MAX_PREVIEW:
                        break
                try:
                    total_pages = int(wrapper.get('@attr', {}).get('totalPages', 1))
                except (ValueError, TypeError, AttributeError):
                    total_pages = 1
                if len(rows) >= MAX_PREVIEW or not items or page >= total_pages:
                    break
            store.cache_put(key, rows, ttl=600)
        owned = owned_pairs(store)
        return [{**row, 'on_shelf': (normalize(row['artist']), normalize(row['title'])) in owned}
                for row in rows]

    def start(self, account_id, store, musicbrainz, ids, period, enabled=None, on_complete=None):
        if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_BATCH or
                any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids)):
            raise AppError('Select between 1 and 250 different albums.')
        # Use server-fetched source rows. Clients cannot inject arbitrary
        # MusicBrainz IDs or artist names into a privileged background worker.
        available = {item['id']: item for item in self.preview(store, period)}
        if any(key not in available for key in ids):
            raise AppError('Your Last.fm selection expired. Refresh the list.', 409)
        selected = [available[key] for key in ids if not available[key]['on_shelf']]
        if not selected:
            raise AppError('Everything selected is already on your shelf.', 409)
        with self.lock:
            if self.jobs.get(account_id, {}).get('state') == 'running':
                raise AppError('An album import is already running for this account.', 409)
            job = {'id': secrets.token_hex(8), 'state': 'running', 'total': len(selected),
                   'processed': 0, 'added': 0, 'skipped': 0, 'failed': 0,
                   'issues': [], 'started_at': int(time.time())}
            self.jobs[account_id] = job
            store.set_setting('lastfm_import_status', job)
        worker = threading.Thread(target=self._run, args=(account_id, store, musicbrainz,
                                  selected, enabled, job['id'], on_complete), name='audioshelf-lastfm-import',
                                  daemon=True)
        worker.start()
        return dict(job)

    def _run(self, account_id, store, musicbrainz, rows, enabled, job_id, on_complete=None):
        owned = owned_pairs(store)
        for item in rows:
            outcome, reason = 'skipped', ''
            if enabled is not None and not enabled(account_id):
                reason = 'Account was disabled; import stopped.'
                self._update(account_id, store, job_id, 'failed', item, reason)
                break
            pair = (normalize(item['artist']), normalize(item['title']))
            try:
                if pair in owned:
                    reason = 'Already on shelf'
                else:
                    with store.catalogue_lock:
                        group = self._match_group(musicbrainz, item)
                        if group['on_shelf']:
                            reason = 'Already on shelf'
                        else:
                            # Preserve the original MusicBrainz edition rules
                            # and the unreviewed canonical tracklist; no
                            # invented Spotify mapping or silent bonus tracks.
                            # A shelf entry is not an approved tracklist. The
                            # independent Spotify worker must first locate a
                            # completely verified original MusicBrainz edition.
                            store.clear_unverified_edition(group['id'])
                            store.shelf(group['id'], True)
                            owned.add(pair)
                            outcome = 'added'
            except AppError as error:
                outcome, reason = 'failed', str(error)
            except Exception:
                outcome, reason = 'failed', 'Could not import this album.'
            self._update(account_id, store, job_id, outcome, item, reason)
        with self.lock:
            job = self.jobs.get(account_id)
            if job and job['id'] == job_id:
                job['state'] = 'complete' if job['processed'] == job['total'] else 'interrupted'
                job['finished_at'] = int(time.time())
                store.set_setting('lastfm_import_status', job)
        # Start the independent Spotify worker only after the catalogue import
        # has released its lock. It must never turn a successful import into a
        # reported failure if the downstream service is unavailable.
        if on_complete and job and job['state'] == 'complete':
            try:
                on_complete()
            except Exception:
                pass

    @staticmethod
    def _match_group(musicbrainz, item):
        artist = item['artist']
        titles = album_title_variants(item['title'], artist)

        def matches(record, title):
            return (normalize(record['title']) == normalize(title) and
                    any(same_artist(a['name'], artist) for a in record.get('artists', [])))

        identifier = item.get('mbid', '')
        if identifier:
            try:
                album = musicbrainz.ensure_group(mbid(identifier))
                if any(matches(album, title) for title in titles):
                    return album
            except (AppError, ValueError, KeyError):
                pass

        # Search by artist AND title so common titles are not crowded out
        # by the first results for unrelated artists.
        credited = [artist]
        if artist.casefold().startswith('the '):
            credited.append(artist[4:])
        else:
            credited.append('The ' + artist)
        for title in titles:
            candidates = {}
            for credited_artist in credited:
                for album in musicbrainz.search(title, 'album', album_artist=credited_artist):
                    if matches(album, title):
                        candidates[album['id']] = album
                if candidates:
                    break
            if not candidates:
                # Conservative fallback for unusual artist-search indexing.
                for album in musicbrainz.search(title, 'album'):
                    if matches(album, title):
                        candidates[album['id']] = album
            if len(candidates) == 1:
                return next(iter(candidates.values()))
            if len(candidates) > 1:
                raise AppError('Several MusicBrainz studio albums could match. Choose manually.')
        raise AppError('No matching MusicBrainz studio album was found. Check compilations, remixes or spelling.')

    def _update(self, account_id, store, job_id, outcome, item, reason):
        with self.lock:
            job = self.jobs.get(account_id)
            if not job or job['id'] != job_id:
                return
            job['processed'] += 1
            job[outcome] += 1
            if reason and outcome == 'failed' and len(job['issues']) < 80:
                job['issues'].append({'title': item['title'], 'artist': item['artist'],
                                      'reason': reason})
            store.set_setting('lastfm_import_status', job)
