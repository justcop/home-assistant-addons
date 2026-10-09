"""Spotify-verified MusicBrainz edition discovery for new shelf albums.

Do not alter stored canonical tracks while previewing candidate releases. MusicBrainz
limits uncached requests to roughly one per second, so each page verifies at most
four editions and returns a continuation cursor for the rest.
"""
from .errors import AppError
from .matching import candidate
from .musicbrainz import mbid, release_tracks
from .release_filters import matches_filters


class PlayableReleases:
    PAGE_SIZE = 4

    def __init__(self, store, musicbrainz, spotify):
        self.store = store
        self.musicbrainz = musicbrainz
        self.spotify = spotify

    def inspect(self, album_id, release_id):
        album = self.store.album(mbid(album_id))
        release = self.musicbrainz.get('release/' + mbid(release_id),
                                      {'inc': 'recordings+release-groups+isrcs'})
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
        # Reuse the same exhaustive Spotify search and matching rules as the
        # ordinary album resolver. Spotify album details are cached per market.
        options = self.spotify.candidates(album)
        return [self.spotify.album(item['id']) for item in options]

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

    def select(self, album_id, release_id, spotify_album_id, reviewed=True, add_to_shelf=False):
        if not self.spotify.connected:
            raise AppError('Connect Spotify before choosing a playable edition.', 409)
        # Prove full match against both remote sources BEFORE mutating canonical
        # tracks, verified Spotify IDs, review status or shelf membership.
        probe = self.inspect(album_id, release_id)
        choice = self.assess(probe, spotify_album_id)
        self.musicbrainz.use_release(album_id, release_id, reviewed)
        self.store.mapping(album_id, choice)
        if add_to_shelf:
            self.store.shelf(album_id, True)
        return self.store.album(album_id)
