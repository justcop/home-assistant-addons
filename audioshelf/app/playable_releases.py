"""Spotify-verified MusicBrainz edition discovery for new shelf albums.

Do not alter stored canonical tracks while previewing candidate releases. MusicBrainz
limits uncached requests to roughly one per second, so each page verifies at most
four editions and returns a continuation cursor for the rest.
"""
from .errors import AppError
from .matching import candidate
from .musicbrainz import mbid, release_tracks
from .release_filters import matches_filters
from .tracklist_variants import group_releases, vinyl_sides


class PlayableReleases:
    PAGE_SIZE = 4
    VARIANT_PAGE_SIZE = 8

    def __init__(self, store, musicbrainz, spotify):
        self.store = store
        self.musicbrainz = musicbrainz
        self.spotify = spotify

    def inspect(self, album_id, release_id):
        album = self.store.album(mbid(album_id))
        release = self.musicbrainz.get('release/' + mbid(release_id),
                                      {'inc': 'recordings+release-groups+isrcs+artist-credits'})
        if release.get('release-group', {}).get('id') != album_id or release.get('status') != 'Official':
            raise AppError('Choose an official MusicBrainz edition of this album.')
        if not matches_filters(release, self.store.release_filters(album_id)):
            raise AppError('This edition does not match your MusicBrainz release preferences.')
        tracks = release_tracks(release)
        if not tracks:
            raise AppError('This edition contains no playable audio tracks.')
        if len(tracks) > 100:
            raise AppError('This edition exceeds the 100-track playback limit.')
        probe = dict(album, tracks=[dict(track, position=number)
                                    for number, track in enumerate(tracks, 1)])
        return probe

    @staticmethod
    def fully_verified(assessment, count):
        return count > 0 and assessment['verified'] == count and all(
            entry['verified'] and entry['spotify_id'] for entry in assessment['mappings'])

    def assess(self, album, spotify_album_id):
        source = self.spotify.album(spotify_album_id)
        assessment = candidate(album, source)
        if not self.fully_verified(assessment, len(album['tracks'])):
            missing = len(album['tracks']) - assessment['verified']
            raise AppError(f'This MusicBrainz edition has {missing} track(s) without a verified Spotify match. Choose another edition.')
        return assessment

    def sources(self, album):
        # Resolve each album's Spotify search only once across paginated
        # MusicBrainz choices. Spotify.album already caches each market-specific
        # album; cache just the candidate IDs to avoid repeated search calls.
        key = 'playable-sources:' + self.spotify.market + ':' + album['id']
        identifiers = self.store.cache_get(key)
        if identifiers is None:
            options = self.spotify.candidates(album)
            identifiers = [item['id'] for item in options]
            self.store.cache_put(key, identifiers, ttl=900)
        return [self.spotify.album(identifier) for identifier in identifiers]

    def page(self, album_id, offset=0):
        if not self.spotify.connected:
            raise AppError('Connect Spotify in Settings to find playable MusicBrainz editions.', 409)
        album = self.store.album(mbid(album_id))
        releases = self.musicbrainz.releases(album_id)
        if not isinstance(offset, int) or offset < 0 or offset > len(releases):
            raise AppError('Invalid edition cursor.')
        selected = releases[offset:offset + self.PAGE_SIZE]
        if not selected:
            return {'releases': [], 'next_offset': None, 'checked': 0}
        sources = self.sources(album)
        playable = []
        for item in selected:
            try:
                probe = self.inspect(album_id, item['id'])
            except AppError as error:
                if error.status >= 500:
                    raise
                continue
            compatible = [candidate(probe, source) for source in sources]
            compatible = [choice for choice in compatible if self.fully_verified(choice, len(probe['tracks']))]
            if compatible:
                best = max(compatible, key=lambda c: c['score'])
                playable.append({**item, 'spotify_album_id': best['id'], 'spotify_album_name': best['name'],
                                 'matched_tracks': len(probe['tracks'])})
        next_offset = offset + len(selected)
        return {'releases': playable, 'next_offset': next_offset if next_offset < len(releases) else None,
                'checked': len(selected)}

    def variants(self, album_id, offset=0):
        """Explore MusicBrainz pressings without doing any Spotify lookup.

        Page through the release list in preferred-country/format order.
        Only inspect up to eight tracklists at a time, reusing the persistent
        MusicBrainz HTTP cache. The client combines matching fingerprints across
        pages. A release is not committed merely by opening this picker.
        """
        album_id = mbid(album_id)
        releases = self.musicbrainz.releases(album_id)
        if type(offset) is not int or offset < 0 or offset > len(releases):
            raise AppError('Invalid tracklist cursor.')
        selected = releases[offset:offset + self.VARIANT_PAGE_SIZE]
        previews = []
        for item in selected:
            try:
                probe = self.inspect(album_id, item['id'])
                # inspect() has already validated the release group, filters,
                # track count and playable audio media.
                release = self.musicbrainz.get(
                    'release/' + item['id'],
                    {'inc': 'recordings+release-groups+isrcs+artist-credits'})
                previews.append((release, probe['tracks']))
            except AppError as error:
                if error.status >= 500 or error.status == 429:
                    raise
        next_offset = offset + len(selected)
        return {'variants': group_releases(previews),
                'checked': len(selected), 'total': len(releases),
                'next_offset': next_offset if next_offset < len(releases) else None}

    def matches(self, album_id, release_id):
        """Spotify verification begins only after a user chose a tracklist."""
        if not self.spotify.connected:
            raise AppError('Connect Spotify in Settings to match the chosen tracklist.', 409)
        album = self.inspect(mbid(album_id), release_id)
        candidates = [candidate(album, source) for source in self.sources(album)]
        matches = [match for match in candidates
                   if self.fully_verified(match, len(album['tracks']))]
        matches.sort(key=lambda match: match['score'], reverse=True)
        return {'matches': [{'spotify_album_id': match['id'],
                             'name': match['name'],
                             'matched_tracks': len(album['tracks']),
                             'score': match['score']} for match in matches[:12]]}

    def select(self, album_id, release_id, spotify_album_id, reviewed=True, add_to_shelf=False, split_sides=False):
        if not self.spotify.connected:
            raise AppError('Connect Spotify before choosing a playable edition.', 409)
        if type(split_sides) is not bool:
            raise AppError('Choose whether to split vinyl sides using true or false.')
        # Prove full match against both remote sources BEFORE mutating canonical
        # tracks, verified Spotify IDs, review status or shelf membership.
        probe = self.inspect(album_id, release_id)
        choice = self.assess(probe, spotify_album_id)
        sides = []
        if split_sides:
            release = self.musicbrainz.get(
                'release/' + mbid(release_id),
                {'inc': 'recordings+release-groups+isrcs+artist-credits'})
            sides = vinyl_sides(release)
            if not sides:
                raise AppError('This MusicBrainz edition has no reliably numbered vinyl sides.')
        self.musicbrainz.use_release(album_id, release_id, reviewed)
        self.store.mapping(album_id, choice)
        self.store.set_setting('playback_sides:'+album_id, sides)
        if add_to_shelf:
            self.store.shelf(album_id, True)
        return self.store.album(album_id)
