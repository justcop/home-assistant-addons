import re
import threading
import time
import uuid

import requests

from .errors import AppError

BAD_EDITION = re.compile(r'bonus|deluxe|expanded|anniversary|collector|remaster|special edition|limited edition', re.I)
EXCLUDED_TYPES = {'Compilation', 'Live', 'Remix', 'Soundtrack', 'Demo', 'DJ-mix', 'Mixtape/Street',
                  'Interview', 'Spokenword', 'Audiobook', 'Audio drama', 'Field recording'}


def mbid(value):
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise AppError('Invalid MusicBrainz identifier.') from None
    if parsed != value.lower():
        raise AppError('Invalid MusicBrainz identifier.')
    return parsed


def literal(value):
    return '"' + re.sub(r'([+\-!(){}\[\]^"~*?:\\/|&])', r'\\\1', value) + '"'


def studio(group):
    return group.get('primary-type') == 'Album' and not EXCLUDED_TYPES.intersection(group.get('secondary-types', []))


def release_rank(release, original_date):
    """Prefer an unexpanded original-year home-market edition, never the shortest album."""
    date = release.get('date', '')
    original_year = original_date[:4]
    year_gap = abs(int(date[:4])-int(original_year)) if date[:4].isdigit() and original_year.isdigit() else 9999
    description = release.get('title','') + ' ' + release.get('disambiguation','')
    media = release.get('media', [])
    formats = [m.get('format', '') for m in media]
    return (bool(BAD_EDITION.search(description)), year_gap,
            release.get('country') == 'JP',
            {'GB': 0, 'XW': 1, 'US': 2}.get(release.get('country'), 3),
            date or '9999', 'CD' not in formats, release['id'])


def release_tracks(release):
    tracks = []
    for medium in sorted(release.get('media', []), key=lambda m: m.get('position', 1)):
        if medium.get('format') in {'DVD', 'DVD-Video', 'Blu-ray', 'VHS', 'VCD', 'Video'}:
            continue
        for track in sorted(medium.get('tracks', []), key=lambda t: t.get('position', 1)):
            recording = track.get('recording', {})
            if recording.get('video'):
                continue
            tracks.append({'title': track.get('title') or recording.get('title', 'Untitled'),
                'disc_number': medium.get('position', 1), 'track_number': track.get('position', len(tracks)+1),
                'duration_ms': track.get('length') or recording.get('length'),
                'recording_id': recording.get('id'), 'isrcs': recording.get('isrcs', [])})
    return tracks


class MusicBrainz:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.last_call = 0
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'AudioShelf/0.1.0 (https://github.com/justcop/home-assistant-addons)',
                                     'Accept': 'application/json'})

    def get(self, entity, params=None):
        params = dict(params or {}, fmt='json')
        key = 'mb:' + entity + ':' + str(sorted(params.items()))
        cached = self.store.cache_get(key)
        if cached is not None:
            return cached
        for attempt in range(3):
            # Shared process-wide instance serialises ALL cache misses and retries.
            with self.lock:
                cached = self.store.cache_get(key)
                if cached is not None:
                    return cached
                time.sleep(max(0, 1.05-(time.monotonic()-self.last_call)))
                self.last_call = time.monotonic()
                try:
                    response = self.session.get('https://musicbrainz.org/ws/2/'+entity, params=params, timeout=(8,30))
                except requests.RequestException:
                    raise AppError('MusicBrainz is unreachable. Please try again.', 502) from None
            if response.status_code in {429, 503} and attempt < 2:
                time.sleep(1+attempt)
                continue
            if response.status_code == 404:
                raise AppError('That MusicBrainz entry could not be found.', 404)
            if not response.ok:
                raise AppError('MusicBrainz is temporarily unavailable. Please try again.', 502)
            try:
                result = response.json()
            except ValueError:
                raise AppError('MusicBrainz returned an unreadable response.', 502) from None
            self.store.cache_put(key, result)
            return result

    def search(self, query, kind='artist'):
        field = 'artist' if kind == 'artist' else 'releasegroup'
        expression = field + ':' + literal(query)
        if kind == 'album':
            expression += ' AND primarytype:album AND status:official'
        result = self.get('artist' if kind == 'artist' else 'release-group', {'query': expression, 'limit': 30})
        if kind == 'artist':
            return result.get('artists', [])
        groups = [g for g in result.get('release-groups', []) if studio(g)]
        self.store.catalogue(groups)
        return [self.store.album(g['id']) for g in groups]

    def artist_albums(self, artist_id):
        mbid(artist_id)
        offset, groups = 0, []
        while True:
            page = self.get('release-group', {'artist': artist_id, 'type': 'album', 'release-group-status':'website-default',
                                           'inc': 'artist-credits', 'limit':100, 'offset':offset})
            items = page.get('release-groups', [])
            groups.extend(g for g in items if studio(g))
            offset += len(items)
            if not items or offset >= page.get('release-group-count', offset):
                break
        self.store.catalogue(groups)
        return self.store.albums(artist_id)

    def ensure_group(self, album_id):
        group = self.get('release-group/'+mbid(album_id), {'inc':'artist-credits'})
        if not studio(group):
            raise AppError('The MVP collects studio albums only.')
        self.store.catalogue([group])
        return self.store.album(album_id)

    def releases(self, album_id):
        album = self.store.album(album_id)
        offset, items = 0, []
        while True:
            page = self.get('release', {'release-group':mbid(album_id), 'status':'official', 'inc':'media',
                                        'limit':100, 'offset':offset})
            batch = page.get('releases', [])
            items.extend(batch)
            offset += len(batch)
            if not batch or offset >= page.get('release-count', offset):
                break
        return sorted(items, key=lambda r: release_rank(r, album['release_date']))

    def use_release(self, album_id, release_id, reviewed=False):
        release = self.get('release/'+mbid(release_id), {'inc':'recordings+release-groups+isrcs'})
        if release.get('release-group',{}).get('id') != album_id or release.get('status') != 'Official':
            raise AppError('Choose an official edition of this album.')
        tracks = release_tracks(release)
        if not tracks:
            raise AppError('This edition has no usable audio tracklist. Please choose another.')
        release['label'] = ' · '.join(filter(None, [release.get('country'),release.get('date'),
            ', '.join(m.get('format','') for m in release.get('media',[])), release.get('disambiguation')]))
        self.store.set_tracks(album_id, release, tracks, reviewed)
        return self.store.album(album_id)

    def ensure_tracks(self, album_id):
        album = self.store.album(album_id)
        if album['tracks']:
            return album
        releases = self.releases(album_id)
        for release in releases[:8]:
            try:
                return self.use_release(album_id, release['id'])
            except AppError as error:
                if error.status != 400:
                    raise
        raise AppError('No complete tracklist found. Choose an original MusicBrainz edition in album settings.')
