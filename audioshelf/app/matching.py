import re
import unicodedata
from difflib import SequenceMatcher

# Remaster labels change editions, while live/acoustic/demo/edit labels change recordings.
REMASTER = re.compile(r'\s*(?:[-–—/]\s*|[\[(])(?:(?:\d{4})\s+)?(?:re-?master(?:ed)?)(?:\s+\d{4})?(?:\s+version)?[\])]?\s*$', re.I)
PRODUCTION_MIX = re.compile(r'\s*(?:[-–—]\s*|[\[(])(?:(?:new|stereo|mono)\s+)?(?:\d{4}\s+(?:(?:stereo|mono)\s+)?mix|(?:stereo|mono)\s+mix(?:\s+\d{4})?)[\])]?\s*$', re.I)
EDITION_YEAR = re.compile(r'(?:\b(19\d{2}|20\d{2})\s+(?:(?:stereo|mono)\s+)?(?:re-?master(?:ed)?|mix)\b|\b(?:re-?master(?:ed)?|mix)\s+(19\d{2}|20\d{2})\b)', re.I)
VERSION = re.compile(r'\b(live|demo|acoustic|instrumental|remix|radio edit|single edit|rerecord(?:ed|ing)|re-record(?:ed|ing))\b', re.I)
MIX_FORMAT = re.compile(r'\s*[-–—/]\s*(?:original\s+)?(?:mono|stereo)\s*$', re.I)
PART_NUMBER = re.compile(r'\bpt\.?\s*(?=(?:[0-9]+|[ivx]+)\b)', re.I)
MEDLEY_LABEL = re.compile(r'\s*[-–—/]\s*medley\s*VERSION_QUALIFIER = re.compile(r'\b(acoustic|live|demo|instrumental)\s+version\b', re.I)
ROMAN_PART = re.compile(r'\bpart\s+(i|ii|iii|iv|v|vi|vii|viii|ix|x)\b', re.I)
ROMAN_PARTS = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5,
               'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10}



def normalize(value, track=False):
    value = value.strip()
    if track:
        for _ in range(3):
            reduced = MIX_FORMAT.sub('', PRODUCTION_MIX.sub('', REMASTER.sub('', value)))
            if reduced == value:
                break
            value = reduced
        if '/' in value:
            value = MEDLEY_LABEL.sub('', value)
        value = PART_NUMBER.sub('part ', value)
        value = VERSION_QUALIFIER.sub(lambda m: m.group(1), value)
        # Limit Roman-numeral rewriting to explicit numbered "Part" labels.
        # Never change standalone "I", song titles or unrelated Roman numerals.
        value = ROMAN_PART.sub(lambda m: 'part ' + str(ROMAN_PARTS[m.group(1).lower()]), value)
    value = unicodedata.normalize('NFKD', value).casefold().replace('&', ' and ')
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.findall(r'\w+', value))


def artist_credit_matches(allowed_names, credited_names):
    """Handle a jointly credited act printed as one name or two Spotify artists.

    All components must be present. Substring matching or one-member matching
    would incorrectly admit solo versions and unrelated cover recordings.
    """
    allowed = {normalize(name) for name in allowed_names if name}
    credited = {normalize(name) for name in credited_names if name}
    if allowed.intersection(credited):
        return True
    for name in allowed_names:
        members = re.split(r'\s+(?:and|&)\s+', name.strip(), flags=re.I)
        if len(members) == 2 and all(len(normalize(member)) >= 3 for member in members):
            if {normalize(member) for member in members}.issubset(credited):
                return True
    return False


def printed_subtitle_match(canonical_title, spotify_title):
    """Long editorial subtitles can be absent from the Spotify display title.

    Never erase a mix/version, collaborator, disc number, part or track name.
    Require independent artist, duration, and position checks downstream.
    """
    for long_title, short_title in ((canonical_title, spotify_title),
                                    (spotify_title, canonical_title)):
        match = PRINTED_SUBTITLE.match(long_title.strip())
        if not match:
            continue
        primary, detail = match.groups()
        if len(detail.strip()) < 8 or PRINTED_VERSION.search(detail):
            continue
        if len(normalize(primary, True)) >= 4 and normalize(primary, True) == normalize(short_title, True):
            return True
    return False


def credited_guest_matches(allowed_names, spotify, album_artist_names):
    """Corroborate a MusicBrainz 'Artist feat. Guest' credit printed as one name.

    Spotify may list the principal act with the guest separately or in the
    printed feature credit. Mere substring matching is never enough.
    """
    credited = {normalize(a['name']) for a in spotify.get('artists', [])
                if a.get('name')}
    album = {normalize(name) for name in album_artist_names}
    printed = normalize(spotify.get('name') or '')
    for artist in allowed_names:
        members = FEATURED_ARTISTS.split(artist, maxsplit=1)
        if len(members) != 2:
            continue
        lead, guest = (normalize(x) for x in members)
        if not lead or not guest or lead not in credited or lead not in album:
            continue
        if guest in credited or (FEATURED_TITLE.search(spotify.get('name') or '')
                                 and guest in printed):
            return True
    return False


def compact_title(value):
    """Space and punctuation differences cannot change the printed word sequence."""
    return ''.join(normalize(value, True).split())


def track_assessment(canonical, spotify, artist_names):
    if spotify.get('is_playable') is False or spotify.get('restrictions'):
        return {'score': 0, 'verified': False, 'reason': 'unavailable'}
    # Use MusicBrainz's release-track performers when supplied. A different
    # singer/conductor on one track is not a different edition of the album.
    # Without per-track credits, fall back to the release-group artist.
    allowed = canonical.get('artist_names') or artist_names
    credited = [a['name'] for a in spotify.get('artists', []) if a.get('name')]
    if not (artist_credit_matches(allowed, credited) or
            credited_guest_matches(allowed, spotify, artist_names)):
        return {'score': 0, 'verified': False, 'reason': 'artist_mismatch'}
    left, right = normalize(canonical['title'], True), normalize(spotify['name'], True)
    recording_title = normalize(canonical.get('recording_title') or '', True)
    # Alternate names are evidence only for this linked recording. An alias must
    # never erase a live/demo/edit distinction present in either canonical title.
    versions = set(VERSION.findall(left)) | set(VERSION.findall(recording_title))
    if versions != set(VERSION.findall(right)):
        return {'score': 0, 'verified': False, 'reason': 'recording_version_mismatch'}
    exact = left == right
    subtitle = not exact and printed_subtitle_match(canonical['title'], spotify['name'])
    # Spaced numerals and run-together words: 11am/11 am,
    # Cliffdiving/Cliff Diving, and punctuation-only contractions.
    compact = (not exact and len(left.replace(' ', '')) >= 4 and
               left.replace(' ', '') == right.replace(' ', ''))
    # A printed track title may omit a short trailing subtitle, such as the
    # end of a combined medley title. This is NOT a general fuzzy match: the
    # shorter title must be a four-word prefix, with at most three additional
    # words, and the track duration must independently corroborate it.
    left_words, right_words = left.split(), right.split()
    shorter, longer = sorted((left_words, right_words), key=len)
    expanded_title = (len(shorter) >= 4 and 0 < len(longer)-len(shorter) <= 3
                      and longer[:len(shorter)] == shorter
                      and len(' '.join(shorter)) >= 15)
    alternate_titles = {normalize(title, True) for title in
                        [recording_title, *canonical.get('recording_aliases', [])] if title}
    recording_match = bool(canonical.get('recording_id') and right in alternate_titles)
    similarity = SequenceMatcher(None, left, right).ratio()
    isrc = spotify.get('external_ids', {}).get('isrc')
    same_isrc = bool(isrc and isrc in canonical.get('isrcs', []))
    if not exact and not compact and not subtitle and not recording_match and not same_isrc and not expanded_title and similarity < .85:
        return {'score': 0, 'verified': False, 'reason': 'title_mismatch', 'canonical_title': left, 'spotify_title': right}
    duration = canonical.get('duration_ms')
    actual = spotify.get('duration_ms')
    delta = abs(actual-duration) if duration and actual else None
    plausible_duration = delta is None or delta <= max(8000, duration*.04)
    if delta is not None and delta > max(30000, duration*.18):
        return {'score': 0, 'verified': False, 'reason': 'duration_mismatch', 'duration_delta_ms': delta}
    score = (95 if exact or compact or subtitle or recording_match or same_isrc or expanded_title else similarity*80) + (5 if delta is not None and delta<2000 else 0)
    # A trailing-title expansion is only verified with independent duration
    # agreement. A similarly named edit/remix is still rejected above.
    verified = (exact or compact or same_isrc or (recording_match and delta is not None)
                or (expanded_title and delta is not None)
                or (subtitle and delta is not None and delta <= max(5000, duration*.025))) and plausible_duration
    return {'score': score, 'verified': verified,
            'reason': ('exact_title' if exact else 'compact_title' if compact else 'printed_subtitle' if subtitle else 'same_recording_title' if recording_match else 'same_isrc' if same_isrc else 'extended_title') if verified else 'needs_review',
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
                # Verification must dominate fuzzy resemblance. Otherwise a
                # plausible but unverified track can steal the ordered slot
                # from a fully verified recording later in the Spotify list.
                options.append((dp[i-1][j-1]+score+1000+(100000 if verified else 0), 'match'))
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


def _rescue_ordered_medley_prefix(album, source, mappings):
    """Verify an abbreviated medley only with matching artist, duration and order.

    A short first-title prefix alone is not evidence of recording identity.
    Require at least five independently verified neighbouring album tracks.
    """
    if sum(m['verified'] for m in mappings) < 5:
        return
    tracks = source['all_tracks']
    used = {m['spotify_id'] for m in mappings if m['spotify_id']}
    by_id = {track['id']: index for index, track in enumerate(tracks)}
    for index, (canonical, mapping) in enumerate(zip(album['tracks'], mappings)):
        if mapping['verified'] or mapping['spotify_id']:
            continue
        short = normalize(canonical['title'], True)
        if len(short.split()) < 2 or '/' in canonical['title']:
            continue
        options = []
        for position, spotify in enumerate(tracks):
            if spotify['id'] in used or spotify.get('is_playable') is False or spotify.get('restrictions'):
                continue
            name = spotify.get('name', '')
            if '/' not in name:
                continue
            first, _, remainder = name.partition('/')
            if not remainder or normalize(first, True) != short:
                continue
            if set(VERSION.findall(normalize(canonical['title'], True))) != set(
                    VERSION.findall(normalize(spotify['name'], True))):
                continue
            credited = [a['name'] for a in spotify.get('artists', []) if a.get('name')]
            if not artist_credit_matches([a['name'] for a in album['artists']], credited):
                continue
            duration, actual = canonical.get('duration_ms'), spotify.get('duration_ms')
            if not duration or not actual or abs(actual-duration) > max(8000, duration*.04):
                continue
            earlier = [by_id[m['spotify_id']] for m in mappings[:index]
                       if m['verified'] and m['spotify_id'] in by_id]
            later = [by_id[m['spotify_id']] for m in mappings[index+1:]
                     if m['verified'] and m['spotify_id'] in by_id]
            if (not earlier or position > max(earlier)) and (not later or position < min(later)):
                options.append(spotify)
        if len(options) == 1:
            item = options[0]
            mapping.update(spotify_id=item['id'], verified=True, score=95,
                           spotify_title=item['name'], assessment={
                               'reason': 'album_context_medley_prefix', 'verified': True,
                               'duration_delta_ms': abs(item['duration_ms']-canonical['duration_ms'])})
            used.add(item['id'])


def _rescue_contextual_title_variants(album, source, mappings):
    """Resolve minor print-title differences only within an independently matched album.

    A unique compatible Spotify track must occupy the expected gap between
    verified neighbours, match all credited performers and recording versions,
    and have a near-identical duration. Never reorder tracks or accept edits.
    """
    missing = [i for i, mapping in enumerate(mappings) if not mapping['verified']]
    if not missing or len(missing) > 3 or len(album['tracks']) < 6:
        return
    if sum(m['verified'] for m in mappings) < 5:
        return
    source_tracks = source['all_tracks']
    by_id = {track['id']: i for i, track in enumerate(source_tracks)}
    used = {m['spotify_id'] for m in mappings if m['verified'] and m['spotify_id']}
    artist_names = [a['name'] for a in album['artists']]
    for index in missing:
        mapping, canonical = mappings[index], album['tracks'][index]
        if mapping['verified']:
            continue
        earlier = [by_id[m['spotify_id']] for m in mappings[:index]
                   if m['verified'] and m['spotify_id'] in by_id]
        later = [by_id[m['spotify_id']] for m in mappings[index+1:]
                 if m['verified'] and m['spotify_id'] in by_id]
        if not earlier and not later:
            continue
        low = max(earlier, default=-1)
        high = min(later, default=len(source_tracks))
        if high-low > 4 or high <= low+1:
            continue
        options = []
        for position in range(low+1, high):
            track = source_tracks[position]
            if track.get('id') in used or track.get('is_playable') is False or track.get('restrictions'):
                continue
            allowed = canonical.get('artist_names') or artist_names
            if not artist_credit_matches(allowed, [a['name'] for a in track.get('artists', [])
                                                   if a.get('name')]):
                continue
            left, right = normalize(canonical['title'], True), normalize(track['name'], True)
            recording = normalize(canonical.get('recording_title') or '', True)
            if set(VERSION.findall(left)) | set(VERSION.findall(recording)) != set(VERSION.findall(right)):
                continue
            likeness = SequenceMatcher(None, left, right).ratio()
            if likeness < .84:
                continue
            duration, actual = canonical.get('duration_ms'), track.get('duration_ms')
            if not duration or not actual or abs(actual-duration) > min(3000, max(1000, duration*.012)):
                continue
            options.append((track, likeness, abs(actual-duration)))
        if len(options) != 1:
            continue
        track, likeness, delta = options[0]
        mapping.update(spotify_id=track['id'], score=90*likeness, verified=True,
                       spotify_title=track['name'], assessment={
                           'verified': True, 'reason': 'album_context_printed_title',
                           'duration_delta_ms': delta})
        used.add(track['id'])


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
            considered = sorted(({'id': t['id'], 'title': t['name'], 'spotify_position': n+1,
                                   **track_assessment(track, t, artists)}
                                for n,t in enumerate(source['all_tracks'])),
                                key=lambda t: (t['verified'], t['score'],
                                    SequenceMatcher(None, normalize(track['title'], True),
                                                    normalize(t['title'], True)).ratio()), reverse=True)
            mapping.update(assessment={'reason': 'no_ordered_match'}, alternatives=considered[:3])
    _rescue_ordered_medley_prefix(album, source, mappings)
    _rescue_contextual_title_variants(album, source, mappings)
    # An album with at least five tracks and every other song independently
    # verified provides useful context for one exactly named recording whose
    # boundary duration differs slightly between editions. Do not weaken the
    # standalone single-track verifier, or accept multiple borderline matches.
    missing = [(track, mapping) for track, mapping in zip(album['tracks'], mappings)
               if not mapping['verified']]
    if len(album['tracks']) >= 5 and len(missing) == 1:
        track, mapping = missing[0]
        evidence = mapping.get('assessment') or {}
        delta = evidence.get('duration_delta_ms')
        duration = track.get('duration_ms')
        if (mapping.get('spotify_id') and evidence.get('reason') == 'needs_review'
                and evidence.get('canonical_title') == evidence.get('spotify_title')
                and isinstance(delta, (int, float)) and isinstance(duration, (int, float))
                and duration > 0 and delta <= max(16000, min(20000, duration*.09))):
            mapping['verified'] = True
            mapping['assessment'] = {**evidence, 'verified': True,
                                     'reason': 'album_context_duration'}
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
, re.I)
PRINTED_SUBTITLE = re.compile(r'^(.+?)\\s*\\(([^()]*)\\)\\s*VERSION_QUALIFIER = re.compile(r'\b(acoustic|live|demo|instrumental)\s+version\b', re.I)
ROMAN_PART = re.compile(r'\bpart\s+(i|ii|iii|iv|v|vi|vii|viii|ix|x)\b', re.I)
ROMAN_PARTS = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5,
               'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10}



def normalize(value, track=False):
    value = value.strip()
    if track:
        for _ in range(3):
            reduced = MIX_FORMAT.sub('', PRODUCTION_MIX.sub('', REMASTER.sub('', value)))
            if reduced == value:
                break
            value = reduced
        if '/' in value:
            value = MEDLEY_LABEL.sub('', value)
        value = PART_NUMBER.sub('part ', value)
        value = VERSION_QUALIFIER.sub(lambda m: m.group(1), value)
        # Limit Roman-numeral rewriting to explicit numbered "Part" labels.
        # Never change standalone "I", song titles or unrelated Roman numerals.
        value = ROMAN_PART.sub(lambda m: 'part ' + str(ROMAN_PARTS[m.group(1).lower()]), value)
    value = unicodedata.normalize('NFKD', value).casefold().replace('&', ' and ')
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.findall(r'\w+', value))


def artist_credit_matches(allowed_names, credited_names):
    """Handle a jointly credited act printed as one name or two Spotify artists.

    All components must be present. Substring matching or one-member matching
    would incorrectly admit solo versions and unrelated cover recordings.
    """
    allowed = {normalize(name) for name in allowed_names if name}
    credited = {normalize(name) for name in credited_names if name}
    if allowed.intersection(credited):
        return True
    for name in allowed_names:
        members = re.split(r'\s+(?:and|&)\s+', name.strip(), flags=re.I)
        if len(members) == 2 and all(len(normalize(member)) >= 3 for member in members):
            if {normalize(member) for member in members}.issubset(credited):
                return True
    return False


def compact_title(value):
    """Space and punctuation differences cannot change the printed word sequence."""
    return ''.join(normalize(value, True).split())


def track_assessment(canonical, spotify, artist_names):
    if spotify.get('is_playable') is False or spotify.get('restrictions'):
        return {'score': 0, 'verified': False, 'reason': 'unavailable'}
    # Use MusicBrainz's release-track performers when supplied. A different
    # singer/conductor on one track is not a different edition of the album.
    # Without per-track credits, fall back to the release-group artist.
    allowed = canonical.get('artist_names') or artist_names
    credited = [a['name'] for a in spotify.get('artists', []) if a.get('name')]
    if not artist_credit_matches(allowed, credited):
        return {'score': 0, 'verified': False, 'reason': 'artist_mismatch'}
    left, right = normalize(canonical['title'], True), normalize(spotify['name'], True)
    recording_title = normalize(canonical.get('recording_title') or '', True)
    # Alternate names are evidence only for this linked recording. An alias must
    # never erase a live/demo/edit distinction present in either canonical title.
    versions = set(VERSION.findall(left)) | set(VERSION.findall(recording_title))
    if versions != set(VERSION.findall(right)):
        return {'score': 0, 'verified': False, 'reason': 'recording_version_mismatch'}
    exact = left == right
    # Spaced numerals and run-together words: 11am/11 am,
    # Cliffdiving/Cliff Diving, and punctuation-only contractions.
    compact = (not exact and len(left.replace(' ', '')) >= 4 and
               left.replace(' ', '') == right.replace(' ', ''))
    # A printed track title may omit a short trailing subtitle, such as the
    # end of a combined medley title. This is NOT a general fuzzy match: the
    # shorter title must be a four-word prefix, with at most three additional
    # words, and the track duration must independently corroborate it.
    left_words, right_words = left.split(), right.split()
    shorter, longer = sorted((left_words, right_words), key=len)
    expanded_title = (len(shorter) >= 4 and 0 < len(longer)-len(shorter) <= 3
                      and longer[:len(shorter)] == shorter
                      and len(' '.join(shorter)) >= 15)
    alternate_titles = {normalize(title, True) for title in
                        [recording_title, *canonical.get('recording_aliases', [])] if title}
    recording_match = bool(canonical.get('recording_id') and right in alternate_titles)
    similarity = SequenceMatcher(None, left, right).ratio()
    isrc = spotify.get('external_ids', {}).get('isrc')
    same_isrc = bool(isrc and isrc in canonical.get('isrcs', []))
    if not exact and not compact and not recording_match and not same_isrc and not expanded_title and similarity < .85:
        return {'score': 0, 'verified': False, 'reason': 'title_mismatch', 'canonical_title': left, 'spotify_title': right}
    duration = canonical.get('duration_ms')
    actual = spotify.get('duration_ms')
    delta = abs(actual-duration) if duration and actual else None
    plausible_duration = delta is None or delta <= max(8000, duration*.04)
    if delta is not None and delta > max(30000, duration*.18):
        return {'score': 0, 'verified': False, 'reason': 'duration_mismatch', 'duration_delta_ms': delta}
    score = (95 if exact or compact or recording_match or same_isrc or expanded_title else similarity*80) + (5 if delta is not None and delta<2000 else 0)
    # A trailing-title expansion is only verified with independent duration
    # agreement. A similarly named edit/remix is still rejected above.
    verified = (exact or compact or same_isrc or (recording_match and delta is not None)
                or (expanded_title and delta is not None)) and plausible_duration
    return {'score': score, 'verified': verified,
            'reason': ('exact_title' if exact else 'compact_title' if compact else 'same_recording_title' if recording_match else 'same_isrc' if same_isrc else 'extended_title') if verified else 'needs_review',
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
                # Verification must dominate fuzzy resemblance. Otherwise a
                # plausible but unverified track can steal the ordered slot
                # from a fully verified recording later in the Spotify list.
                options.append((dp[i-1][j-1]+score+1000+(100000 if verified else 0), 'match'))
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


def _rescue_ordered_medley_prefix(album, source, mappings):
    """Verify an abbreviated medley only with matching artist, duration and order.

    A short first-title prefix alone is not evidence of recording identity.
    Require at least five independently verified neighbouring album tracks.
    """
    if sum(m['verified'] for m in mappings) < 5:
        return
    tracks = source['all_tracks']
    used = {m['spotify_id'] for m in mappings if m['spotify_id']}
    by_id = {track['id']: index for index, track in enumerate(tracks)}
    for index, (canonical, mapping) in enumerate(zip(album['tracks'], mappings)):
        if mapping['verified'] or mapping['spotify_id']:
            continue
        short = normalize(canonical['title'], True)
        if len(short.split()) < 2 or '/' in canonical['title']:
            continue
        options = []
        for position, spotify in enumerate(tracks):
            if spotify['id'] in used or spotify.get('is_playable') is False or spotify.get('restrictions'):
                continue
            name = spotify.get('name', '')
            if '/' not in name:
                continue
            first, _, remainder = name.partition('/')
            if not remainder or normalize(first, True) != short:
                continue
            if set(VERSION.findall(normalize(canonical['title'], True))) != set(
                    VERSION.findall(normalize(spotify['name'], True))):
                continue
            credited = [a['name'] for a in spotify.get('artists', []) if a.get('name')]
            if not artist_credit_matches([a['name'] for a in album['artists']], credited):
                continue
            duration, actual = canonical.get('duration_ms'), spotify.get('duration_ms')
            if not duration or not actual or abs(actual-duration) > max(8000, duration*.04):
                continue
            earlier = [by_id[m['spotify_id']] for m in mappings[:index]
                       if m['verified'] and m['spotify_id'] in by_id]
            later = [by_id[m['spotify_id']] for m in mappings[index+1:]
                     if m['verified'] and m['spotify_id'] in by_id]
            if (not earlier or position > max(earlier)) and (not later or position < min(later)):
                options.append(spotify)
        if len(options) == 1:
            item = options[0]
            mapping.update(spotify_id=item['id'], verified=True, score=95,
                           spotify_title=item['name'], assessment={
                               'reason': 'album_context_medley_prefix', 'verified': True,
                               'duration_delta_ms': abs(item['duration_ms']-canonical['duration_ms'])})
            used.add(item['id'])


def _rescue_contextual_title_variants(album, source, mappings):
    """Resolve minor print-title differences only within an independently matched album.

    A unique compatible Spotify track must occupy the expected gap between
    verified neighbours, match all credited performers and recording versions,
    and have a near-identical duration. Never reorder tracks or accept edits.
    """
    missing = [i for i, mapping in enumerate(mappings) if not mapping['verified']]
    if not missing or len(missing) > 3 or len(album['tracks']) < 6:
        return
    if sum(m['verified'] for m in mappings) < 5:
        return
    source_tracks = source['all_tracks']
    by_id = {track['id']: i for i, track in enumerate(source_tracks)}
    used = {m['spotify_id'] for m in mappings if m['verified'] and m['spotify_id']}
    artist_names = [a['name'] for a in album['artists']]
    for index in missing:
        mapping, canonical = mappings[index], album['tracks'][index]
        if mapping['verified']:
            continue
        earlier = [by_id[m['spotify_id']] for m in mappings[:index]
                   if m['verified'] and m['spotify_id'] in by_id]
        later = [by_id[m['spotify_id']] for m in mappings[index+1:]
                 if m['verified'] and m['spotify_id'] in by_id]
        if not earlier and not later:
            continue
        low = max(earlier, default=-1)
        high = min(later, default=len(source_tracks))
        if high-low > 4 or high <= low+1:
            continue
        options = []
        for position in range(low+1, high):
            track = source_tracks[position]
            if track.get('id') in used or track.get('is_playable') is False or track.get('restrictions'):
                continue
            allowed = canonical.get('artist_names') or artist_names
            if not artist_credit_matches(allowed, [a['name'] for a in track.get('artists', [])
                                                   if a.get('name')]):
                continue
            left, right = normalize(canonical['title'], True), normalize(track['name'], True)
            recording = normalize(canonical.get('recording_title') or '', True)
            if set(VERSION.findall(left)) | set(VERSION.findall(recording)) != set(VERSION.findall(right)):
                continue
            likeness = SequenceMatcher(None, left, right).ratio()
            if likeness < .84:
                continue
            duration, actual = canonical.get('duration_ms'), track.get('duration_ms')
            if not duration or not actual or abs(actual-duration) > min(3000, max(1000, duration*.012)):
                continue
            options.append((track, likeness, abs(actual-duration)))
        if len(options) != 1:
            continue
        track, likeness, delta = options[0]
        mapping.update(spotify_id=track['id'], score=90*likeness, verified=True,
                       spotify_title=track['name'], assessment={
                           'verified': True, 'reason': 'album_context_printed_title',
                           'duration_delta_ms': delta})
        used.add(track['id'])


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
            considered = sorted(({'id': t['id'], 'title': t['name'], 'spotify_position': n+1,
                                   **track_assessment(track, t, artists)}
                                for n,t in enumerate(source['all_tracks'])),
                                key=lambda t: (t['verified'], t['score'],
                                    SequenceMatcher(None, normalize(track['title'], True),
                                                    normalize(t['title'], True)).ratio()), reverse=True)
            mapping.update(assessment={'reason': 'no_ordered_match'}, alternatives=considered[:3])
    _rescue_ordered_medley_prefix(album, source, mappings)
    _rescue_contextual_title_variants(album, source, mappings)
    # An album with at least five tracks and every other song independently
    # verified provides useful context for one exactly named recording whose
    # boundary duration differs slightly between editions. Do not weaken the
    # standalone single-track verifier, or accept multiple borderline matches.
    missing = [(track, mapping) for track, mapping in zip(album['tracks'], mappings)
               if not mapping['verified']]
    if len(album['tracks']) >= 5 and len(missing) == 1:
        track, mapping = missing[0]
        evidence = mapping.get('assessment') or {}
        delta = evidence.get('duration_delta_ms')
        duration = track.get('duration_ms')
        if (mapping.get('spotify_id') and evidence.get('reason') == 'needs_review'
                and evidence.get('canonical_title') == evidence.get('spotify_title')
                and isinstance(delta, (int, float)) and isinstance(duration, (int, float))
                and duration > 0 and delta <= max(16000, min(20000, duration*.09))):
            mapping['verified'] = True
            mapping['assessment'] = {**evidence, 'verified': True,
                                     'reason': 'album_context_duration'}
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
)
PRINTED_VERSION = re.compile(r'\\b(?:feat\\.?|featuring|ft\\.?|live|demo|acoustic|instrumental|remix|edit|remaster|mix|version|take|radio|mono|stereo|part|pt\\.?|bonus|hidden|reprise|alternate|alternative|unplugged|session|clean|explicit|extended|acapella)\\b', re.I)
FEATURED_TITLE = re.compile(r'\\s*[\\[(]\\s*(?:feat\\.?|featuring|ft\\.?)\\s+[^\\])]+[\\])]\\s*VERSION_QUALIFIER = re.compile(r'\b(acoustic|live|demo|instrumental)\s+version\b', re.I)
ROMAN_PART = re.compile(r'\bpart\s+(i|ii|iii|iv|v|vi|vii|viii|ix|x)\b', re.I)
ROMAN_PARTS = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5,
               'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10}



def normalize(value, track=False):
    value = value.strip()
    if track:
        for _ in range(3):
            reduced = MIX_FORMAT.sub('', PRODUCTION_MIX.sub('', REMASTER.sub('', value)))
            if reduced == value:
                break
            value = reduced
        if '/' in value:
            value = MEDLEY_LABEL.sub('', value)
        value = PART_NUMBER.sub('part ', value)
        value = VERSION_QUALIFIER.sub(lambda m: m.group(1), value)
        # Limit Roman-numeral rewriting to explicit numbered "Part" labels.
        # Never change standalone "I", song titles or unrelated Roman numerals.
        value = ROMAN_PART.sub(lambda m: 'part ' + str(ROMAN_PARTS[m.group(1).lower()]), value)
    value = unicodedata.normalize('NFKD', value).casefold().replace('&', ' and ')
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.findall(r'\w+', value))


def artist_credit_matches(allowed_names, credited_names):
    """Handle a jointly credited act printed as one name or two Spotify artists.

    All components must be present. Substring matching or one-member matching
    would incorrectly admit solo versions and unrelated cover recordings.
    """
    allowed = {normalize(name) for name in allowed_names if name}
    credited = {normalize(name) for name in credited_names if name}
    if allowed.intersection(credited):
        return True
    for name in allowed_names:
        members = re.split(r'\s+(?:and|&)\s+', name.strip(), flags=re.I)
        if len(members) == 2 and all(len(normalize(member)) >= 3 for member in members):
            if {normalize(member) for member in members}.issubset(credited):
                return True
    return False


def compact_title(value):
    """Space and punctuation differences cannot change the printed word sequence."""
    return ''.join(normalize(value, True).split())


def track_assessment(canonical, spotify, artist_names):
    if spotify.get('is_playable') is False or spotify.get('restrictions'):
        return {'score': 0, 'verified': False, 'reason': 'unavailable'}
    # Use MusicBrainz's release-track performers when supplied. A different
    # singer/conductor on one track is not a different edition of the album.
    # Without per-track credits, fall back to the release-group artist.
    allowed = canonical.get('artist_names') or artist_names
    credited = [a['name'] for a in spotify.get('artists', []) if a.get('name')]
    if not artist_credit_matches(allowed, credited):
        return {'score': 0, 'verified': False, 'reason': 'artist_mismatch'}
    left, right = normalize(canonical['title'], True), normalize(spotify['name'], True)
    recording_title = normalize(canonical.get('recording_title') or '', True)
    # Alternate names are evidence only for this linked recording. An alias must
    # never erase a live/demo/edit distinction present in either canonical title.
    versions = set(VERSION.findall(left)) | set(VERSION.findall(recording_title))
    if versions != set(VERSION.findall(right)):
        return {'score': 0, 'verified': False, 'reason': 'recording_version_mismatch'}
    exact = left == right
    # Spaced numerals and run-together words: 11am/11 am,
    # Cliffdiving/Cliff Diving, and punctuation-only contractions.
    compact = (not exact and len(left.replace(' ', '')) >= 4 and
               left.replace(' ', '') == right.replace(' ', ''))
    # A printed track title may omit a short trailing subtitle, such as the
    # end of a combined medley title. This is NOT a general fuzzy match: the
    # shorter title must be a four-word prefix, with at most three additional
    # words, and the track duration must independently corroborate it.
    left_words, right_words = left.split(), right.split()
    shorter, longer = sorted((left_words, right_words), key=len)
    expanded_title = (len(shorter) >= 4 and 0 < len(longer)-len(shorter) <= 3
                      and longer[:len(shorter)] == shorter
                      and len(' '.join(shorter)) >= 15)
    alternate_titles = {normalize(title, True) for title in
                        [recording_title, *canonical.get('recording_aliases', [])] if title}
    recording_match = bool(canonical.get('recording_id') and right in alternate_titles)
    similarity = SequenceMatcher(None, left, right).ratio()
    isrc = spotify.get('external_ids', {}).get('isrc')
    same_isrc = bool(isrc and isrc in canonical.get('isrcs', []))
    if not exact and not compact and not recording_match and not same_isrc and not expanded_title and similarity < .85:
        return {'score': 0, 'verified': False, 'reason': 'title_mismatch', 'canonical_title': left, 'spotify_title': right}
    duration = canonical.get('duration_ms')
    actual = spotify.get('duration_ms')
    delta = abs(actual-duration) if duration and actual else None
    plausible_duration = delta is None or delta <= max(8000, duration*.04)
    if delta is not None and delta > max(30000, duration*.18):
        return {'score': 0, 'verified': False, 'reason': 'duration_mismatch', 'duration_delta_ms': delta}
    score = (95 if exact or compact or recording_match or same_isrc or expanded_title else similarity*80) + (5 if delta is not None and delta<2000 else 0)
    # A trailing-title expansion is only verified with independent duration
    # agreement. A similarly named edit/remix is still rejected above.
    verified = (exact or compact or same_isrc or (recording_match and delta is not None)
                or (expanded_title and delta is not None)) and plausible_duration
    return {'score': score, 'verified': verified,
            'reason': ('exact_title' if exact else 'compact_title' if compact else 'same_recording_title' if recording_match else 'same_isrc' if same_isrc else 'extended_title') if verified else 'needs_review',
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
                # Verification must dominate fuzzy resemblance. Otherwise a
                # plausible but unverified track can steal the ordered slot
                # from a fully verified recording later in the Spotify list.
                options.append((dp[i-1][j-1]+score+1000+(100000 if verified else 0), 'match'))
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


def _rescue_ordered_medley_prefix(album, source, mappings):
    """Verify an abbreviated medley only with matching artist, duration and order.

    A short first-title prefix alone is not evidence of recording identity.
    Require at least five independently verified neighbouring album tracks.
    """
    if sum(m['verified'] for m in mappings) < 5:
        return
    tracks = source['all_tracks']
    used = {m['spotify_id'] for m in mappings if m['spotify_id']}
    by_id = {track['id']: index for index, track in enumerate(tracks)}
    for index, (canonical, mapping) in enumerate(zip(album['tracks'], mappings)):
        if mapping['verified'] or mapping['spotify_id']:
            continue
        short = normalize(canonical['title'], True)
        if len(short.split()) < 2 or '/' in canonical['title']:
            continue
        options = []
        for position, spotify in enumerate(tracks):
            if spotify['id'] in used or spotify.get('is_playable') is False or spotify.get('restrictions'):
                continue
            name = spotify.get('name', '')
            if '/' not in name:
                continue
            first, _, remainder = name.partition('/')
            if not remainder or normalize(first, True) != short:
                continue
            if set(VERSION.findall(normalize(canonical['title'], True))) != set(
                    VERSION.findall(normalize(spotify['name'], True))):
                continue
            credited = [a['name'] for a in spotify.get('artists', []) if a.get('name')]
            if not artist_credit_matches([a['name'] for a in album['artists']], credited):
                continue
            duration, actual = canonical.get('duration_ms'), spotify.get('duration_ms')
            if not duration or not actual or abs(actual-duration) > max(8000, duration*.04):
                continue
            earlier = [by_id[m['spotify_id']] for m in mappings[:index]
                       if m['verified'] and m['spotify_id'] in by_id]
            later = [by_id[m['spotify_id']] for m in mappings[index+1:]
                     if m['verified'] and m['spotify_id'] in by_id]
            if (not earlier or position > max(earlier)) and (not later or position < min(later)):
                options.append(spotify)
        if len(options) == 1:
            item = options[0]
            mapping.update(spotify_id=item['id'], verified=True, score=95,
                           spotify_title=item['name'], assessment={
                               'reason': 'album_context_medley_prefix', 'verified': True,
                               'duration_delta_ms': abs(item['duration_ms']-canonical['duration_ms'])})
            used.add(item['id'])


def _rescue_contextual_title_variants(album, source, mappings):
    """Resolve minor print-title differences only within an independently matched album.

    A unique compatible Spotify track must occupy the expected gap between
    verified neighbours, match all credited performers and recording versions,
    and have a near-identical duration. Never reorder tracks or accept edits.
    """
    missing = [i for i, mapping in enumerate(mappings) if not mapping['verified']]
    if not missing or len(missing) > 3 or len(album['tracks']) < 6:
        return
    if sum(m['verified'] for m in mappings) < 5:
        return
    source_tracks = source['all_tracks']
    by_id = {track['id']: i for i, track in enumerate(source_tracks)}
    used = {m['spotify_id'] for m in mappings if m['verified'] and m['spotify_id']}
    artist_names = [a['name'] for a in album['artists']]
    for index in missing:
        mapping, canonical = mappings[index], album['tracks'][index]
        if mapping['verified']:
            continue
        earlier = [by_id[m['spotify_id']] for m in mappings[:index]
                   if m['verified'] and m['spotify_id'] in by_id]
        later = [by_id[m['spotify_id']] for m in mappings[index+1:]
                 if m['verified'] and m['spotify_id'] in by_id]
        if not earlier and not later:
            continue
        low = max(earlier, default=-1)
        high = min(later, default=len(source_tracks))
        if high-low > 4 or high <= low+1:
            continue
        options = []
        for position in range(low+1, high):
            track = source_tracks[position]
            if track.get('id') in used or track.get('is_playable') is False or track.get('restrictions'):
                continue
            allowed = canonical.get('artist_names') or artist_names
            if not artist_credit_matches(allowed, [a['name'] for a in track.get('artists', [])
                                                   if a.get('name')]):
                continue
            left, right = normalize(canonical['title'], True), normalize(track['name'], True)
            recording = normalize(canonical.get('recording_title') or '', True)
            if set(VERSION.findall(left)) | set(VERSION.findall(recording)) != set(VERSION.findall(right)):
                continue
            likeness = SequenceMatcher(None, left, right).ratio()
            if likeness < .84:
                continue
            duration, actual = canonical.get('duration_ms'), track.get('duration_ms')
            if not duration or not actual or abs(actual-duration) > min(3000, max(1000, duration*.012)):
                continue
            options.append((track, likeness, abs(actual-duration)))
        if len(options) != 1:
            continue
        track, likeness, delta = options[0]
        mapping.update(spotify_id=track['id'], score=90*likeness, verified=True,
                       spotify_title=track['name'], assessment={
                           'verified': True, 'reason': 'album_context_printed_title',
                           'duration_delta_ms': delta})
        used.add(track['id'])


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
            considered = sorted(({'id': t['id'], 'title': t['name'], 'spotify_position': n+1,
                                   **track_assessment(track, t, artists)}
                                for n,t in enumerate(source['all_tracks'])),
                                key=lambda t: (t['verified'], t['score'],
                                    SequenceMatcher(None, normalize(track['title'], True),
                                                    normalize(t['title'], True)).ratio()), reverse=True)
            mapping.update(assessment={'reason': 'no_ordered_match'}, alternatives=considered[:3])
    _rescue_ordered_medley_prefix(album, source, mappings)
    _rescue_contextual_title_variants(album, source, mappings)
    # An album with at least five tracks and every other song independently
    # verified provides useful context for one exactly named recording whose
    # boundary duration differs slightly between editions. Do not weaken the
    # standalone single-track verifier, or accept multiple borderline matches.
    missing = [(track, mapping) for track, mapping in zip(album['tracks'], mappings)
               if not mapping['verified']]
    if len(album['tracks']) >= 5 and len(missing) == 1:
        track, mapping = missing[0]
        evidence = mapping.get('assessment') or {}
        delta = evidence.get('duration_delta_ms')
        duration = track.get('duration_ms')
        if (mapping.get('spotify_id') and evidence.get('reason') == 'needs_review'
                and evidence.get('canonical_title') == evidence.get('spotify_title')
                and isinstance(delta, (int, float)) and isinstance(duration, (int, float))
                and duration > 0 and delta <= max(16000, min(20000, duration*.09))):
            mapping['verified'] = True
            mapping['assessment'] = {**evidence, 'verified': True,
                                     'reason': 'album_context_duration'}
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
, re.I)
FEATURED_ARTISTS = re.compile(r'\\s+(?:feat\\.?|featuring|ft\\.?)\\s+', re.I)
VERSION_QUALIFIER = re.compile(r'\b(acoustic|live|demo|instrumental)\s+version\b', re.I)
ROMAN_PART = re.compile(r'\bpart\s+(i|ii|iii|iv|v|vi|vii|viii|ix|x)\b', re.I)
ROMAN_PARTS = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5,
               'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10}



def normalize(value, track=False):
    value = value.strip()
    if track:
        for _ in range(3):
            reduced = MIX_FORMAT.sub('', PRODUCTION_MIX.sub('', REMASTER.sub('', value)))
            if reduced == value:
                break
            value = reduced
        if '/' in value:
            value = MEDLEY_LABEL.sub('', value)
        value = PART_NUMBER.sub('part ', value)
        value = VERSION_QUALIFIER.sub(lambda m: m.group(1), value)
        # Limit Roman-numeral rewriting to explicit numbered "Part" labels.
        # Never change standalone "I", song titles or unrelated Roman numerals.
        value = ROMAN_PART.sub(lambda m: 'part ' + str(ROMAN_PARTS[m.group(1).lower()]), value)
    value = unicodedata.normalize('NFKD', value).casefold().replace('&', ' and ')
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.findall(r'\w+', value))


def artist_credit_matches(allowed_names, credited_names):
    """Handle a jointly credited act printed as one name or two Spotify artists.

    All components must be present. Substring matching or one-member matching
    would incorrectly admit solo versions and unrelated cover recordings.
    """
    allowed = {normalize(name) for name in allowed_names if name}
    credited = {normalize(name) for name in credited_names if name}
    if allowed.intersection(credited):
        return True
    for name in allowed_names:
        members = re.split(r'\s+(?:and|&)\s+', name.strip(), flags=re.I)
        if len(members) == 2 and all(len(normalize(member)) >= 3 for member in members):
            if {normalize(member) for member in members}.issubset(credited):
                return True
    return False


def compact_title(value):
    """Space and punctuation differences cannot change the printed word sequence."""
    return ''.join(normalize(value, True).split())


def track_assessment(canonical, spotify, artist_names):
    if spotify.get('is_playable') is False or spotify.get('restrictions'):
        return {'score': 0, 'verified': False, 'reason': 'unavailable'}
    # Use MusicBrainz's release-track performers when supplied. A different
    # singer/conductor on one track is not a different edition of the album.
    # Without per-track credits, fall back to the release-group artist.
    allowed = canonical.get('artist_names') or artist_names
    credited = [a['name'] for a in spotify.get('artists', []) if a.get('name')]
    if not artist_credit_matches(allowed, credited):
        return {'score': 0, 'verified': False, 'reason': 'artist_mismatch'}
    left, right = normalize(canonical['title'], True), normalize(spotify['name'], True)
    recording_title = normalize(canonical.get('recording_title') or '', True)
    # Alternate names are evidence only for this linked recording. An alias must
    # never erase a live/demo/edit distinction present in either canonical title.
    versions = set(VERSION.findall(left)) | set(VERSION.findall(recording_title))
    if versions != set(VERSION.findall(right)):
        return {'score': 0, 'verified': False, 'reason': 'recording_version_mismatch'}
    exact = left == right
    # Spaced numerals and run-together words: 11am/11 am,
    # Cliffdiving/Cliff Diving, and punctuation-only contractions.
    compact = (not exact and len(left.replace(' ', '')) >= 4 and
               left.replace(' ', '') == right.replace(' ', ''))
    # A printed track title may omit a short trailing subtitle, such as the
    # end of a combined medley title. This is NOT a general fuzzy match: the
    # shorter title must be a four-word prefix, with at most three additional
    # words, and the track duration must independently corroborate it.
    left_words, right_words = left.split(), right.split()
    shorter, longer = sorted((left_words, right_words), key=len)
    expanded_title = (len(shorter) >= 4 and 0 < len(longer)-len(shorter) <= 3
                      and longer[:len(shorter)] == shorter
                      and len(' '.join(shorter)) >= 15)
    alternate_titles = {normalize(title, True) for title in
                        [recording_title, *canonical.get('recording_aliases', [])] if title}
    recording_match = bool(canonical.get('recording_id') and right in alternate_titles)
    similarity = SequenceMatcher(None, left, right).ratio()
    isrc = spotify.get('external_ids', {}).get('isrc')
    same_isrc = bool(isrc and isrc in canonical.get('isrcs', []))
    if not exact and not compact and not recording_match and not same_isrc and not expanded_title and similarity < .85:
        return {'score': 0, 'verified': False, 'reason': 'title_mismatch', 'canonical_title': left, 'spotify_title': right}
    duration = canonical.get('duration_ms')
    actual = spotify.get('duration_ms')
    delta = abs(actual-duration) if duration and actual else None
    plausible_duration = delta is None or delta <= max(8000, duration*.04)
    if delta is not None and delta > max(30000, duration*.18):
        return {'score': 0, 'verified': False, 'reason': 'duration_mismatch', 'duration_delta_ms': delta}
    score = (95 if exact or compact or recording_match or same_isrc or expanded_title else similarity*80) + (5 if delta is not None and delta<2000 else 0)
    # A trailing-title expansion is only verified with independent duration
    # agreement. A similarly named edit/remix is still rejected above.
    verified = (exact or compact or same_isrc or (recording_match and delta is not None)
                or (expanded_title and delta is not None)) and plausible_duration
    return {'score': score, 'verified': verified,
            'reason': ('exact_title' if exact else 'compact_title' if compact else 'same_recording_title' if recording_match else 'same_isrc' if same_isrc else 'extended_title') if verified else 'needs_review',
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
                # Verification must dominate fuzzy resemblance. Otherwise a
                # plausible but unverified track can steal the ordered slot
                # from a fully verified recording later in the Spotify list.
                options.append((dp[i-1][j-1]+score+1000+(100000 if verified else 0), 'match'))
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


def _rescue_ordered_medley_prefix(album, source, mappings):
    """Verify an abbreviated medley only with matching artist, duration and order.

    A short first-title prefix alone is not evidence of recording identity.
    Require at least five independently verified neighbouring album tracks.
    """
    if sum(m['verified'] for m in mappings) < 5:
        return
    tracks = source['all_tracks']
    used = {m['spotify_id'] for m in mappings if m['spotify_id']}
    by_id = {track['id']: index for index, track in enumerate(tracks)}
    for index, (canonical, mapping) in enumerate(zip(album['tracks'], mappings)):
        if mapping['verified'] or mapping['spotify_id']:
            continue
        short = normalize(canonical['title'], True)
        if len(short.split()) < 2 or '/' in canonical['title']:
            continue
        options = []
        for position, spotify in enumerate(tracks):
            if spotify['id'] in used or spotify.get('is_playable') is False or spotify.get('restrictions'):
                continue
            name = spotify.get('name', '')
            if '/' not in name:
                continue
            first, _, remainder = name.partition('/')
            if not remainder or normalize(first, True) != short:
                continue
            if set(VERSION.findall(normalize(canonical['title'], True))) != set(
                    VERSION.findall(normalize(spotify['name'], True))):
                continue
            credited = [a['name'] for a in spotify.get('artists', []) if a.get('name')]
            if not artist_credit_matches([a['name'] for a in album['artists']], credited):
                continue
            duration, actual = canonical.get('duration_ms'), spotify.get('duration_ms')
            if not duration or not actual or abs(actual-duration) > max(8000, duration*.04):
                continue
            earlier = [by_id[m['spotify_id']] for m in mappings[:index]
                       if m['verified'] and m['spotify_id'] in by_id]
            later = [by_id[m['spotify_id']] for m in mappings[index+1:]
                     if m['verified'] and m['spotify_id'] in by_id]
            if (not earlier or position > max(earlier)) and (not later or position < min(later)):
                options.append(spotify)
        if len(options) == 1:
            item = options[0]
            mapping.update(spotify_id=item['id'], verified=True, score=95,
                           spotify_title=item['name'], assessment={
                               'reason': 'album_context_medley_prefix', 'verified': True,
                               'duration_delta_ms': abs(item['duration_ms']-canonical['duration_ms'])})
            used.add(item['id'])


def _rescue_contextual_title_variants(album, source, mappings):
    """Resolve minor print-title differences only within an independently matched album.

    A unique compatible Spotify track must occupy the expected gap between
    verified neighbours, match all credited performers and recording versions,
    and have a near-identical duration. Never reorder tracks or accept edits.
    """
    missing = [i for i, mapping in enumerate(mappings) if not mapping['verified']]
    if not missing or len(missing) > 3 or len(album['tracks']) < 6:
        return
    if sum(m['verified'] for m in mappings) < 5:
        return
    source_tracks = source['all_tracks']
    by_id = {track['id']: i for i, track in enumerate(source_tracks)}
    used = {m['spotify_id'] for m in mappings if m['verified'] and m['spotify_id']}
    artist_names = [a['name'] for a in album['artists']]
    for index in missing:
        mapping, canonical = mappings[index], album['tracks'][index]
        if mapping['verified']:
            continue
        earlier = [by_id[m['spotify_id']] for m in mappings[:index]
                   if m['verified'] and m['spotify_id'] in by_id]
        later = [by_id[m['spotify_id']] for m in mappings[index+1:]
                 if m['verified'] and m['spotify_id'] in by_id]
        if not earlier and not later:
            continue
        low = max(earlier, default=-1)
        high = min(later, default=len(source_tracks))
        if high-low > 4 or high <= low+1:
            continue
        options = []
        for position in range(low+1, high):
            track = source_tracks[position]
            if track.get('id') in used or track.get('is_playable') is False or track.get('restrictions'):
                continue
            allowed = canonical.get('artist_names') or artist_names
            if not artist_credit_matches(allowed, [a['name'] for a in track.get('artists', [])
                                                   if a.get('name')]):
                continue
            left, right = normalize(canonical['title'], True), normalize(track['name'], True)
            recording = normalize(canonical.get('recording_title') or '', True)
            if set(VERSION.findall(left)) | set(VERSION.findall(recording)) != set(VERSION.findall(right)):
                continue
            likeness = SequenceMatcher(None, left, right).ratio()
            if likeness < .84:
                continue
            duration, actual = canonical.get('duration_ms'), track.get('duration_ms')
            if not duration or not actual or abs(actual-duration) > min(3000, max(1000, duration*.012)):
                continue
            options.append((track, likeness, abs(actual-duration)))
        if len(options) != 1:
            continue
        track, likeness, delta = options[0]
        mapping.update(spotify_id=track['id'], score=90*likeness, verified=True,
                       spotify_title=track['name'], assessment={
                           'verified': True, 'reason': 'album_context_printed_title',
                           'duration_delta_ms': delta})
        used.add(track['id'])


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
            considered = sorted(({'id': t['id'], 'title': t['name'], 'spotify_position': n+1,
                                   **track_assessment(track, t, artists)}
                                for n,t in enumerate(source['all_tracks'])),
                                key=lambda t: (t['verified'], t['score'],
                                    SequenceMatcher(None, normalize(track['title'], True),
                                                    normalize(t['title'], True)).ratio()), reverse=True)
            mapping.update(assessment={'reason': 'no_ordered_match'}, alternatives=considered[:3])
    _rescue_ordered_medley_prefix(album, source, mappings)
    _rescue_contextual_title_variants(album, source, mappings)
    # An album with at least five tracks and every other song independently
    # verified provides useful context for one exactly named recording whose
    # boundary duration differs slightly between editions. Do not weaken the
    # standalone single-track verifier, or accept multiple borderline matches.
    missing = [(track, mapping) for track, mapping in zip(album['tracks'], mappings)
               if not mapping['verified']]
    if len(album['tracks']) >= 5 and len(missing) == 1:
        track, mapping = missing[0]
        evidence = mapping.get('assessment') or {}
        delta = evidence.get('duration_delta_ms')
        duration = track.get('duration_ms')
        if (mapping.get('spotify_id') and evidence.get('reason') == 'needs_review'
                and evidence.get('canonical_title') == evidence.get('spotify_title')
                and isinstance(delta, (int, float)) and isinstance(duration, (int, float))
                and duration > 0 and delta <= max(16000, min(20000, duration*.09))):
            mapping['verified'] = True
            mapping['assessment'] = {**evidence, 'verified': True,
                                     'reason': 'album_context_duration'}
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
