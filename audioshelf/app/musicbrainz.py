import re
import threading
import time
import uuid

import requests

from .errors import AppError
from .release_filters import DEFAULT_FILTERS, VIDEO_FORMATS, filter_description, matches_filters, preference_rank

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


def release_rank(release, original_date, filters=None):
    """Rank standard editions by shared country, original-year and format preferences."""
    date = release.get('date', '')
    original_year = original_date[:4]
    year_gap = abs(int(date[:4])-int(original_year)) if date[:4].isdigit() and original_year.isdigit() else 9999
    description = release.get('title','') + ' ' + release.get('disambiguation','')
    country_rank, format_rank = preference_rank(release, DEFAULT_FILTERS if filters is None else filters)
    return (bool(BAD_EDITION.search(description)), country_rank, year_gap,
            format_rank, date or '9999', release['id'])


def release_tracks(release):
    tracks = []
    for medium in sorted(release.get('media', []), key=lambda m: m.get('position', 1)):
        if medium.get('format') in VIDEO_FORMATS:
            continue
        for track in sorted(medium.get('tracks', []), key=lambda t: t.get('position', 1)):
            recording = track.get('recording', {})
            if recording.get('video'):
                continue
            tracks.append({'title': track.get('title') or recording.get('title', 'Untitled'),
                'disc_number': medium.get('position', 1), 'track_number': track.get('position', len(tracks)+1),
                'duration_ms': track.get('length') or recording.get('length'),
                'recording_id': recording.get('id'), 'isrcs': recording.get('isrcs', []),
                'recording_title': recording.get('title', ''),
                'recording_aliases': [a['name'] for a in recording.get('aliases', []) if a.get('name')]})
    return tracks


class MusicBrainz:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.last_call = 0
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'AudioShelf/0.5.1 (https://github.com/justcop/home-assistant-addons)',
                                     'Accept': 'application/json'})

    def membership(self, group, artist_id=None):
        override = self.store.setting('catalogue:'+group['id'])
        if override is not None:
            return override == 'include'
        selections = []
        for credit in group.get('artist-credit', []):
            if isinstance(credit, dict) and 'artist' in credit:
                if artist_id and credit['artist']['id'] != artist_id:
                    continue
                series = self.store.catalogue_series(credit['artist']['id'])
                if series:
                    selections.append(group['id'] in self.series_members(series))
        if selections:
            return (any(selections) and group.get('primary-type') == 'Album'
                    and not (EXCLUDED_TYPES-{'Soundtrack'}).intersection(group.get('secondary-types', [])))
        return studio(group)

    def series_members(self, series_id):
        key = 'series-members:'+series_id
        cached = self.store.cache_get(key)
        if cached is not None:
            if not self.store.setting('series_snapshot:'+series_id):
                self.store.set_setting('series_snapshot:'+series_id,
                    {'id': series_id, 'name': 'Curated catalogue', 'members': cached, 'saved_at': time.time()})
            return set(cached)
        try:
            detail = self.get('series/'+mbid(series_id), {'inc': 'release-group-rels'})
            if detail.get('type') not in {'Release group', 'Release group series'}:
                raise AppError('Choose a MusicBrainz release-group series.')
            members = {r['release-group']['id'] for r in detail.get('relations', []) if 'release-group' in r}
            if not members:
                raise AppError('That series has no album members.')
            self.store.cache_put(key, sorted(members))
            # Keep a last successful snapshot for every chosen catalogue, rather
            # than shipping artist-specific album lists. It survives cache deletion.
            self.store.set_setting('series_snapshot:'+series_id,
                {'id': series_id, 'name': detail.get('name', 'Curated catalogue'),
                 'members': sorted(members), 'saved_at': time.time()})
        except AppError as error:
            saved = self.store.setting('series_snapshot:'+series_id)
            if not saved or error.status < 500 and error.status != 429:
                raise
            members = set(saved['members'])
            self.store.cache_put(key, sorted(members), ttl=300)
        return members

    def catalogue_candidates(self, artist_id, query=None):
        """Discover release-group series without treating a name as a canonical rule."""
        detail = self.get('artist/'+mbid(artist_id))
        query = (query or detail['name']).strip()[:160]
        result = self.get('series', {'query': 'series:'+literal(query), 'limit': 30})
        return {'query': query, 'series': [
            {key: s.get(key, '') for key in ('id', 'name', 'type', 'disambiguation')}
            for s in result.get('series', []) if s.get('type') in {'Release group', 'Release group series'}]}

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
        groups = [g for g in result.get('release-groups', []) if self.membership(g)]
        self.store.catalogue(groups)
        return [self.store.album(g['id']) for g in groups]

    def artist_albums(self, artist_id, manage=False):
        mbid(artist_id)
        offset, groups = 0, []
        while True:
            page = self.get('release-group', {'artist': artist_id, 'type': 'album', 'release-group-status':'website-default',
                                           'inc': 'artist-credits', 'limit':100, 'offset':offset})
            items = page.get('release-groups', [])
            groups.extend(g for g in items if g.get('primary-type') == 'Album')
            offset += len(items)
            if not items or offset >= page.get('release-group-count', offset):
                break
        self.store.catalogue(groups)
        catalogue = {g['id']: g for g in groups}
        result = []
        for album in self.store.albums(artist_id):
            group = catalogue.get(album['id'])
            if group is None:
                continue
            included = self.membership(group, artist_id)
            if included or manage:
                album.update(catalogue_included=included, catalogue_override=self.store.setting('catalogue:'+group['id']),
                             secondary_types=group.get('secondary-types', []))
                result.append(album)
        return result

    def ensure_group(self, album_id):
        group = self.get('release-group/'+mbid(album_id), {'inc':'artist-credits'})
        if group.get('primary-type') != 'Album' or not self.membership(group):
            raise AppError('The MVP collects studio albums only.')
        self.store.catalogue([group])
        return self.store.album(album_id)

    def release_page(self, album_id, offset=0):
        album = self.store.album(album_id)
        page = self.get('release', {'release-group': mbid(album_id), 'status': 'official', 'inc': 'media',
                                  'limit': 100, 'offset': offset})
        batch = page.get('releases', [])
        next_offset = offset+len(batch)
        filters = self.store.release_filters(album_id)
        items = sorted(({**r, 'preference_rank': list(release_rank(r, album['release_date'], filters))}
                        for r in batch if matches_filters(r, filters)),
                       key=lambda r: release_rank(r, album['release_date'], filters))
        self.store.diagnostic(album_id, 'release_page', {'offset': offset, 'scanned': len(batch), 'matched': len(items),
            'filters': filters, 'considered': [{'id': r['id'], 'country': r.get('country'), 'date': r.get('date'),
                'formats': [m.get('format') for m in r.get('media', [])], 'included': matches_filters(r, filters)} for r in batch]})
        return {'releases': items, 'next_offset': next_offset if batch and next_offset < page.get('release-count', next_offset) else None,
                'filters': filters}

    def releases(self, album_id):
        album = self.store.album(album_id)
        offset, items = 0, []
        while True:
            page = self.release_page(album_id, offset)
            items.extend(page['releases'])
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
        filters = self.store.release_filters(album_id)
        return sorted((r for r in items if matches_filters(r, filters)),
                      key=lambda r: release_rank(r, album['release_date'], filters))

    def use_release(self, album_id, release_id, reviewed=False):
        release = self.get('release/'+mbid(release_id), {'inc':'recordings+release-groups+isrcs'})
        if release.get('release-group',{}).get('id') != album_id or release.get('status') != 'Official':
            raise AppError('Choose an official edition of this album.')
        if not matches_filters(release, self.store.release_filters(album_id)):
            raise AppError('This edition does not match your MusicBrainz release filters. Change them in Settings first.')
        tracks = release_tracks(release)
        if not tracks:
            raise AppError('This edition has no usable audio tracklist. Please choose another.')
        release['label'] = ' · '.join(filter(None, [release.get('country'),release.get('date'),
            ', '.join(m.get('format','') for m in release.get('media',[])), release.get('disambiguation')]))
        self.store.set_tracks(album_id, release, tracks, reviewed)
        self.store.diagnostic(album_id, 'selected_release', {'id': release_id, 'label': release['label'],
            'track_count': len(tracks), 'reviewed': reviewed})
        return self.store.album(album_id)

    def ensure_tracks(self, album_id):
        album = self.store.album(album_id)
        if album['tracks']:
            return album
        releases = self.releases(album_id)
        if not releases:
            raise AppError('No MusicBrainz editions match your release filters (' +
                           filter_description(self.store.release_filters(album_id)) + '). Change them in Settings.')
        for release in releases[:8]:
            try:
                return self.use_release(album_id, release['id'])
            except AppError as error:
                self.store.diagnostic(album_id, 'unusable_release', {'id': release['id'], 'reason': str(error)})
                if error.status != 400:
                    raise
        raise AppError('No complete tracklist found. Choose an original MusicBrainz edition in album settings.')

    def enrich_recordings(self, album, positions):
        """Resolve uncertain names from their exact recording IDs, including old shelves.

        Lookups use the shared rate limiter/cache. Metadata-only writes preserve
        printed titles, order, review state and every existing Spotify mapping.
        """
        changed = False
        seen = set()
        for track in album['tracks']:
            recording_id = track.get('recording_id')
            if (track['position'] not in positions or not recording_id or recording_id in seen
                    or (track.get('method') == 'manual' and track.get('verified'))):
                continue
            seen.add(recording_id)
            try:
                recording = self.get('recording/'+mbid(recording_id), {'inc': 'aliases+isrcs'})
                if recording.get('id') != recording_id:
                    continue
                title = recording.get('title', '')
                aliases = [a['name'] for a in recording.get('aliases', []) if a.get('name')]
                isrcs = sorted(set(track.get('isrcs', []) + recording.get('isrcs', [])))
                if (title, aliases, isrcs) != (track.get('recording_title'), track.get('recording_aliases'), track.get('isrcs')):
                    self.store.recording_metadata(album['id'], recording_id, title, aliases, isrcs)
                    changed = True
            except AppError as error:
                self.store.diagnostic(album['id'], 'recording_metadata_unavailable',
                                      {'recording_id': recording_id, 'message': str(error)})
                # An outage should not multiply into one failed request per track.
                if error.status >= 500 or error.status == 429:
                    break
        return self.store.album(album['id']) if changed else album
