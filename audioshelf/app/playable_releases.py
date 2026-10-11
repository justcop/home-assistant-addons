"""Spotify-verified MusicBrainz edition discovery for new shelf albums.

Do not alter stored canonical tracks while previewing candidate releases. MusicBrainz
limits uncached requests to roughly one per second, so each page verifies at most
four editions and returns a continuation cursor for the rest.
"""
import hashlib
import json

from .errors import AppError
from .matching import candidate
from .musicbrainz import mbid, release_tracks
from .release_filters import matches_filters
from .tracklist_variants import group_releases, vinyl_sides


class PlayableReleases:
    PAGE_SIZE = 4
    # Keep every HTTP response within proxy budgets even on a cold MusicBrainz cache.
    VARIANT_PAGE_SIZE = 2
    PICKER_COUNTRIES = frozenset({'GB', 'US', 'XW'})

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
            enriched = self.enrich_probe(album, assessment)
            if enriched is not album:
                assessment = candidate(enriched, source)
        if not self.fully_verified(assessment, len(album['tracks'])):
            missing = len(album['tracks']) - assessment['verified']
            raise AppError(f'This MusicBrainz edition has {missing} track(s) without a verified Spotify match. Choose another edition.')
        return assessment

    def enrich_probe(self, album, assessment):
        """Enrich a prospective release in memory; never mutate saved shelf tracks.

        The existing MusicBrainz enrichment method writes recording metadata to
        the stored album, so it is unsafe for edition previews.
        """
        tracks = [dict(track) for track in album['tracks']]
        positions = {mapping['position'] for mapping in assessment['mappings'] if not mapping['verified']}
        seen, changed = set(), False
        for track in tracks:
            recording_id = track.get('recording_id')
            if track['position'] not in positions or not recording_id or recording_id in seen:
                continue
            seen.add(recording_id)
            try:
                recording = self.musicbrainz.get('recording/' + mbid(recording_id),
                                                {'inc': 'aliases+isrcs'})
                if recording.get('id') != recording_id:
                    continue
                track['recording_title'] = recording.get('title', '')
                track['recording_aliases'] = [a['name'] for a in recording.get('aliases', [])
                                               if a.get('name')]
                track['isrcs'] = sorted(set(track.get('isrcs', []) + recording.get('isrcs', [])))
                changed = True
            except AppError as error:
                if error.status >= 500 or error.status == 429:
                    break
        return dict(album, tracks=tracks) if changed else album

    def sources(self, album, broad=False):
        # Resolve each album's Spotify search only once across paginated
        # MusicBrainz choices. Spotify.album already caches each market-specific
        # album; cache just the candidate IDs to avoid repeated search calls.
        key = 'playable-sources:' + self.spotify.market + ':' + album['id'] + (':broad' if broad else '')
        identifiers = self.store.cache_get(key)
        if identifiers is None:
            options = self.spotify.candidates(album, broad=True) if broad else self.spotify.candidates(album)
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

    def verified_choices(self, probe, album):
        """Match one musical tracklist, broadening the search only if necessary."""
        sources = self.sources(album)
        assessments = [candidate(probe, source) for source in sources]
        matches = [c for c in assessments if self.fully_verified(c, len(probe['tracks']))]
        if not matches:
            existing = {source['id'] for source in sources}
            extra = [source for source in self.sources(album, broad=True)
                     if source['id'] not in existing]
            assessments.extend(candidate(probe, source) for source in extra)
            matches = [c for c in assessments if self.fully_verified(c, len(probe['tracks']))]
        if not matches and assessments:
            # Recording aliases/ISRCs can rescue an otherwise good, differently
            # printed title. Do this ONLY for near matches, and never mutate the DB.
            best = max(assessments, key=lambda c: (c['verified'], c['matched'], c['score']))
            if best['verified'] > 0:
                enriched = self.enrich_probe(probe, best)
                if enriched is not probe:
                    sources_by_id = {s['id']: s for s in sources}
                    sources_by_id.update({s['id']: s for s in extra})
                    assessments = [candidate(enriched, source) for source in sources_by_id.values()]
                    matches = [c for c in assessments if self.fully_verified(c, len(probe['tracks']))]
        matches.sort(key=lambda c: c['score'], reverse=True)
        return matches, assessments

    @staticmethod
    def match_summaries(matches, count):
        return [{'spotify_album_id': match['id'], 'name': match['name'],
                 'matched_tracks': count, 'score': match['score'],
                 # Only combine variants if Spotify verifies the exact same
                 # ordered recordings. Track-name similarity alone is unsafe.
                 'recording_signature': hashlib.sha256(
                     json.dumps([mapping['spotify_id'] for mapping in match['mappings']],
                                separators=(',', ':')).encode()).hexdigest()}
                for match in matches[:12]]

    @staticmethod
    def failure_summary(probe, assessments):
        if not assessments:
            return {'missing': len(probe['tracks']), 'tracks': [],
                    'reason': 'No Spotify album candidates were found.'}
        best = max(assessments, key=lambda c: (c['verified'], c['matched'], c['score']))
        missing = []
        for track, mapping in zip(probe['tracks'], best['mappings']):
            if mapping['verified']:
                continue
            assessment = mapping.get('assessment') or {}
            if assessment.get('reason') == 'no_ordered_match':
                alternative = next(iter(mapping.get('alternatives') or []), {})
                reason = alternative.get('reason') or 'no_ordered_match'
            else:
                reason = assessment.get('reason') or 'no_verified_match'
            missing.append({'title': track['title'], 'reason': reason})
        return {'missing': len(missing), 'tracks': missing[:6],
                'reason': 'No Spotify edition matched every track in order.'}

    def variants(self, album_id, offset=0):
        """Show ONLY grouped tracklists that have a complete Spotify match.

        A page inspects two MusicBrainz releases. Cold recording lookups are
        rate-limited upstream; larger pages can exceed reverse-proxy timeouts.
        Cache successful pages so the client can retry or resume without
        repeating remote calls. Rejected tracks never enter the chooser.
        """
        if not self.spotify.connected:
            raise AppError('Connect Spotify in Settings to find playable tracklists.', 409)
        album_id = mbid(album_id)
        album = self.store.album(album_id)
        all_releases = self.musicbrainz.releases(album_id)
        # The tracklist picker scans all eligible editions, not just the first
        # page. These are standard regional catalogue choices; do not change
        # the user's stored global/album-specific MusicBrainz preferences.
        def eligible(release):
            countries = {release.get('country')}
            for event in release.get('release-events', []):
                countries.update((event.get('area') or {}).get('iso-3166-1-codes', []))
            return bool(countries.intersection(self.PICKER_COUNTRIES))
        releases = [release for release in all_releases if eligible(release)]
        if type(offset) is not int or offset < 0 or offset > len(releases):
            raise AppError('Invalid tracklist cursor.')
        # Account-specific metadata cache: cached page results include verified
        # mappings, so the key includes market, preferences, and an algorithm
        # revision. Never cache an interrupted/incomplete request.
        cache_key = 'verified-variant-page-v3:' + self.spotify.market + ':' + album_id + ':' + str(offset) + ':' + hashlib.sha256(
            json.dumps(self.store.release_filters(album_id), sort_keys=True).encode()).hexdigest()
        cached = self.store.cache_get(cache_key)
        if cached is not None:
            return cached
        selected = releases[offset:offset + self.VARIANT_PAGE_SIZE]
        previews, probes = [], {}
        for item in selected:
            try:
                probe = self.inspect(album_id, item['id'])
                release = self.musicbrainz.get(
                    'release/' + item['id'],
                    {'inc': 'recordings+release-groups+isrcs+artist-credits'})
                previews.append((release, probe['tracks']))
                probes[release['id']] = probe
            except AppError as error:
                if error.status >= 500 or error.status == 429:
                    raise
        playable, rejected = [], []
        for variant in group_releases(previews):
            verified_layouts = []
            failed = None
            for layout in variant['layouts']:
                # Same physical layout can appear on multiple pressings whose
                # recording metadata/durations differ. Try the alternate release
                # IDs before declaring an otherwise playable layout unavailable.
                for release_id in layout['release_ids']:
                    probe = probes[release_id]
                    matches, assessments = self.verified_choices(probe, album)
                    if matches:
                        verified_layouts.append({**{k: v for k, v in layout.items()
                                                    if k != 'release_ids'},
                            'release_id': release_id,
                            'spotify_matches': self.match_summaries(matches, len(probe['tracks']))})
                        break
                    failure = self.failure_summary(probe, assessments)
                    if failed is None or failure['missing'] < failed['missing']:
                        failed = failure
            if verified_layouts:
                playable.append({**variant, 'layouts': verified_layouts,
                                 'pressings': sum(item['pressings'] for item in verified_layouts)})
            else:
                rejected.append({'signature': variant['signature'], 'track_count': variant['track_count'],
                                 **(failed or {'missing': variant['track_count'], 'tracks': [],
                                               'reason': 'No verified Spotify recording found.'})})
        next_offset = offset + len(selected)
        result = {'variants': playable, 'rejected': rejected, 'checked': len(selected),
                  'total': len(releases), 'all_total': len(all_releases),
                  'countries': ['GB', 'XW', 'US'],
                  'next_offset': next_offset if next_offset < len(releases) else None}
        self.store.cache_put(cache_key, result, ttl=1800)
        return result

    def matches(self, album_id, release_id):
        """Recheck a selected release, without saving or changing existing tracks."""
        if not self.spotify.connected:
            raise AppError('Connect Spotify in Settings to match the chosen tracklist.', 409)
        album = self.inspect(mbid(album_id), release_id)
        matches, assessments = self.verified_choices(album, self.store.album(mbid(album_id)))
        return {'matches': self.match_summaries(matches, len(album['tracks'])),
                'failure': None if matches else self.failure_summary(album, assessments)}

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
