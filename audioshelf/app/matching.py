import re
import unicodedata
from difflib import SequenceMatcher

# Remaster labels change editions, while live/acoustic/demo/edit labels change recordings.
REMASTER = re.compile(r'\s*(?:[-–—]\s*|[\[(])(?:(?:\d{4})\s+)?(?:re-?master(?:ed)?)(?:\s+\d{4})?(?:\s+version)?[\])]?\s*$', re.I)
PRODUCTION_MIX = re.compile(r'\s*(?:[-–—]\s*|[\[(])(?:(?:new|stereo|mono)\s+)?(?:\d{4}\s+(?:(?:stereo|mono)\s+)?mix|(?:stereo|mono)\s+mix(?:\s+\d{4})?)[\])]?\s*$', re.I)
EDITION_YEAR = re.compile(r'(?:\b(19\d{2}|20\d{2})\s+(?:(?:stereo|mono)\s+)?(?:re-?master(?:ed)?|mix)\b|\b(?:re-?master(?:ed)?|mix)\s+(19\d{2}|20\d{2})\b)', re.I)
VERSION = re.compile(r'\b(live|demo|acoustic|instrumental|remix|radio edit|single edit|rerecord(?:ed|ing)|re-record(?:ed|ing))\b', re.I)


def normalize(value, track=False):
    value = value.strip()
    if track:
        value = PRODUCTION_MIX.sub('', REMASTER.sub('', value))
    value = unicodedata.normalize('NFKD', value).casefold().replace('&', ' and ')
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.findall(r'\w+', value))


def artist_identity(name):
    """Normalize performer aliases, not arbitrary collaborator/cover-band names."""
    value = normalize(name)
    # MusicBrainz credits often retain 'and His Orchestra' while Spotify
    # lists the same conductor/performer under their individual artist name.
    return re.sub(r' (?:and|with) (?:his|her|their|the) orchestra
    left, right = normalize(canonical['title'], True), normalize(spotify['name'], True)
    recording_title = normalize(canonical.get('recording_title') or '', True)
    # Alternate names are evidence only for this linked recording. An alias must
    # never erase a live/demo/edit distinction present in either canonical title.
    versions = set(VERSION.findall(left)) | set(VERSION.findall(recording_title))
    if versions != set(VERSION.findall(right)):
        return {'score': 0, 'verified': False, 'reason': 'recording_version_mismatch'}
    exact = left == right
    alternate_titles = {normalize(title, True) for title in
                        [recording_title, *canonical.get('recording_aliases', [])] if title}
    recording_match = bool(canonical.get('recording_id') and right in alternate_titles)
    similarity = SequenceMatcher(None, left, right).ratio()
    isrc = spotify.get('external_ids', {}).get('isrc')
    same_isrc = bool(isrc and isrc in canonical.get('isrcs', []))
    if not exact and not recording_match and not same_isrc and similarity < .85:
        return {'score': 0, 'verified': False, 'reason': 'title_mismatch', 'canonical_title': left, 'spotify_title': right}
    duration = canonical.get('duration_ms')
    actual = spotify.get('duration_ms')
    delta = abs(actual-duration) if duration and actual else None
    plausible_duration = delta is None or delta <= max(8000, duration*.04)
    if delta is not None and delta > max(30000, duration*.18):
        return {'score': 0, 'verified': False, 'reason': 'duration_mismatch', 'duration_delta_ms': delta}
    score = (95 if exact or recording_match or same_isrc else similarity*80) + (5 if delta is not None and delta<2000 else 0)
    # Alternate titles require duration corroboration as well as the artist check.
    verified = (exact or same_isrc or (recording_match and delta is not None)) and plausible_duration
    return {'score': score, 'verified': verified,
            'reason': ('exact_title' if exact else 'same_recording_title' if recording_match else 'same_isrc') if verified else 'needs_review',
            'duration_delta_ms': delta, 'canonical_title': left, 'spotify_title': right}


def track_score(canonical, spotify, artist_names):
    assessment = track_assessment(canonical, spotify, artist_names)
    return assessment['score'], assessment['verified']


def align(canonical, spotify_tracks, artist_names):
    """Ordered one-to-one sequence alignment permits bonus tracks without ever adding them."""
    n, m = len(canonical), len(spotify_tracks)
    dp = [[0.]*(m+1) for _ in range(n+1)]
    steps = {}
    for i in range(1, n+1):
        for j in range(1, m+1):
            score, verified = track_score(canonical[i-1], spotify_tracks[j-1], artist_names)
            options = [(dp[i][j-1], 'skip_spotify'), (dp[i-1][j], 'skip_canonical')]
            if score:
                options.append((dp[i-1][j-1]+score+1000, 'match'))
            dp[i][j], steps[i,j] = max(options, key=lambda x:x[0])
    result = [{'position':t['position'], 'spotify_id':None, 'score':0, 'verified':False} for t in canonical]
    i,j = n,m
    while i and j:
        step = steps[i,j]
        if step == 'match':
            score, verified = track_score(canonical[i-1], spotify_tracks[j-1], artist_names)
            result[i-1].update(spotify_id=spotify_tracks[j-1]['id'], score=score, verified=verified)
            i,j = i-1,j-1
        elif step == 'skip_spotify':
            j -= 1
        else:
            i -= 1
    return result


def edition_year(source, mappings=None):
    selected = {m['spotify_id'] for m in mappings if m['spotify_id']} if mappings is not None else None
    labels = [source.get('name','')] + [t.get('name','') for t in source.get('all_tracks',[]) if selected is None or t.get('id') in selected]
    years = [int(a or b) for label in labels for a,b in EDITION_YEAR.findall(label)]
    labelled = any(re.search(r'\bre-?master(?:ed)?\b', label, re.I) for label in labels)
    date = source.get('release_date','')[:4]
    if labelled and not years and date.isdigit():
        years.append(int(date))
    return max(years, default=0)


def candidate(album, source):
    mappings = align(album['tracks'], source['all_tracks'], [a['name'] for a in album['artists']])
    artists = [a['name'] for a in album['artists']]
    by_id = {t['id']: t for t in source['all_tracks']}
    for track, mapping in zip(album['tracks'], mappings):
        if mapping['spotify_id']:
            item = by_id[mapping['spotify_id']]
            mapping.update(spotify_title=item['name'], assessment=track_assessment(track, item, artists))
        else:
            considered = sorted(({'id': t['id'], 'title': t['name'], **track_assessment(track, t, artists)}
                                for t in source['all_tracks']), key=lambda t: t['score'], reverse=True)
            mapping.update(assessment={'reason': 'no_ordered_match'}, alternatives=considered[:3])
    verified = sum(m['verified'] for m in mappings)
    matched = sum(bool(m['spotify_id']) for m in mappings)
    clean_title = normalize(album['title']) == normalize(source['name'])
    preferred_year = edition_year(source,mappings)
    quality = sum(m['score'] for m in mappings)/max(1,len(mappings))
    # Completeness wins first, then the latest explicitly labelled remaster or dated mix.
    # A recent upload/reissue date alone is not evidence of a new mastering.
    score = verified*1000000000000 + matched*1000000000 + preferred_year*10000 + quality
    score += 30*clean_title + 10*(source.get('release_date','')[:4] == album['release_date'][:4])
    score -= min(20, max(0, len(source['all_tracks'])-len(mappings)))
    return {'id':source['id'], 'name':source['name'], 'release_date':source.get('release_date',''),
            'edition_year':preferred_year, 'track_count':len(source['all_tracks']), 'matched':matched, 'verified':verified,
            'score':score, 'mappings':mappings, 'url':'https://open.spotify.com/album/'+source['id']}
, '', value)


def track_assessment(canonical, spotify, artist_names):
    if spotify.get('is_playable') is False or spotify.get('restrictions'):
        return {'score': 0, 'verified': False, 'reason': 'unavailable'}
    # When the official MusicBrainz release credits a different performer on a
    # particular track, use that credit instead of forcing the album artist.
    # Older stored tracks (and MusicBrainz entries with no track credit) retain
    # the original strict album-artist safeguard.
    allowed = canonical.get('artist_names') or artist_names
    credited = {artist_identity(a.get('name','')) for a in spotify.get('artists', []) if a.get('name')}
    if not credited.intersection({artist_identity(name) for name in allowed if name}):
        return {'score': 0, 'verified': False, 'reason': 'artist_mismatch'}
    left, right = normalize(canonical['title'], True), normalize(spotify['name'], True)
    recording_title = normalize(canonical.get('recording_title') or '', True)
    # Alternate names are evidence only for this linked recording. An alias must
    # never erase a live/demo/edit distinction present in either canonical title.
    versions = set(VERSION.findall(left)) | set(VERSION.findall(recording_title))
    if versions != set(VERSION.findall(right)):
        return {'score': 0, 'verified': False, 'reason': 'recording_version_mismatch'}
    exact = left == right
    alternate_titles = {normalize(title, True) for title in
                        [recording_title, *canonical.get('recording_aliases', [])] if title}
    recording_match = bool(canonical.get('recording_id') and right in alternate_titles)
    similarity = SequenceMatcher(None, left, right).ratio()
    isrc = spotify.get('external_ids', {}).get('isrc')
    same_isrc = bool(isrc and isrc in canonical.get('isrcs', []))
    if not exact and not recording_match and not same_isrc and similarity < .85:
        return {'score': 0, 'verified': False, 'reason': 'title_mismatch', 'canonical_title': left, 'spotify_title': right}
    duration = canonical.get('duration_ms')
    actual = spotify.get('duration_ms')
    delta = abs(actual-duration) if duration and actual else None
    plausible_duration = delta is None or delta <= max(8000, duration*.04)
    if delta is not None and delta > max(30000, duration*.18):
        return {'score': 0, 'verified': False, 'reason': 'duration_mismatch', 'duration_delta_ms': delta}
    score = (95 if exact or recording_match or same_isrc else similarity*80) + (5 if delta is not None and delta<2000 else 0)
    # Alternate titles require duration corroboration as well as the artist check.
    verified = (exact or same_isrc or (recording_match and delta is not None)) and plausible_duration
    return {'score': score, 'verified': verified,
            'reason': ('exact_title' if exact else 'same_recording_title' if recording_match else 'same_isrc') if verified else 'needs_review',
            'duration_delta_ms': delta, 'canonical_title': left, 'spotify_title': right}


def track_score(canonical, spotify, artist_names):
    assessment = track_assessment(canonical, spotify, artist_names)
    return assessment['score'], assessment['verified']


def align(canonical, spotify_tracks, artist_names):
    """Ordered one-to-one sequence alignment permits bonus tracks without ever adding them."""
    n, m = len(canonical), len(spotify_tracks)
    dp = [[0.]*(m+1) for _ in range(n+1)]
    steps = {}
    for i in range(1, n+1):
        for j in range(1, m+1):
            score, verified = track_score(canonical[i-1], spotify_tracks[j-1], artist_names)
            options = [(dp[i][j-1], 'skip_spotify'), (dp[i-1][j], 'skip_canonical')]
            if score:
                options.append((dp[i-1][j-1]+score+1000, 'match'))
            dp[i][j], steps[i,j] = max(options, key=lambda x:x[0])
    result = [{'position':t['position'], 'spotify_id':None, 'score':0, 'verified':False} for t in canonical]
    i,j = n,m
    while i and j:
        step = steps[i,j]
        if step == 'match':
            score, verified = track_score(canonical[i-1], spotify_tracks[j-1], artist_names)
            result[i-1].update(spotify_id=spotify_tracks[j-1]['id'], score=score, verified=verified)
            i,j = i-1,j-1
        elif step == 'skip_spotify':
            j -= 1
        else:
            i -= 1
    return result


def edition_year(source, mappings=None):
    selected = {m['spotify_id'] for m in mappings if m['spotify_id']} if mappings is not None else None
    labels = [source.get('name','')] + [t.get('name','') for t in source.get('all_tracks',[]) if selected is None or t.get('id') in selected]
    years = [int(a or b) for label in labels for a,b in EDITION_YEAR.findall(label)]
    labelled = any(re.search(r'\bre-?master(?:ed)?\b', label, re.I) for label in labels)
    date = source.get('release_date','')[:4]
    if labelled and not years and date.isdigit():
        years.append(int(date))
    return max(years, default=0)


def candidate(album, source):
    mappings = align(album['tracks'], source['all_tracks'], [a['name'] for a in album['artists']])
    artists = [a['name'] for a in album['artists']]
    by_id = {t['id']: t for t in source['all_tracks']}
    for track, mapping in zip(album['tracks'], mappings):
        if mapping['spotify_id']:
            item = by_id[mapping['spotify_id']]
            mapping.update(spotify_title=item['name'], assessment=track_assessment(track, item, artists))
        else:
            considered = sorted(({'id': t['id'], 'title': t['name'], **track_assessment(track, t, artists)}
                                for t in source['all_tracks']), key=lambda t: t['score'], reverse=True)
            mapping.update(assessment={'reason': 'no_ordered_match'}, alternatives=considered[:3])
    verified = sum(m['verified'] for m in mappings)
    matched = sum(bool(m['spotify_id']) for m in mappings)
    clean_title = normalize(album['title']) == normalize(source['name'])
    preferred_year = edition_year(source,mappings)
    quality = sum(m['score'] for m in mappings)/max(1,len(mappings))
    # Completeness wins first, then the latest explicitly labelled remaster or dated mix.
    # A recent upload/reissue date alone is not evidence of a new mastering.
    score = verified*1000000000000 + matched*1000000000 + preferred_year*10000 + quality
    score += 30*clean_title + 10*(source.get('release_date','')[:4] == album['release_date'][:4])
    score -= min(20, max(0, len(source['all_tracks'])-len(mappings)))
    return {'id':source['id'], 'name':source['name'], 'release_date':source.get('release_date',''),
            'edition_year':preferred_year, 'track_count':len(source['all_tracks']), 'matched':matched, 'verified':verified,
            'score':score, 'mappings':mappings, 'url':'https://open.spotify.com/album/'+source['id']}
