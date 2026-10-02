import re
import unicodedata
from difflib import SequenceMatcher

# Remaster labels change editions, while live/acoustic/demo/edit labels change recordings.
REMASTER = re.compile(r'\s*(?:[-–—]\s*|[\[(])(?:(?:\d{4})\s+)?(?:re-?master(?:ed)?)(?:\s+\d{4})?(?:\s+version)?[\])]?\s*$', re.I)
VERSION = re.compile(r'\b(live|demo|acoustic|instrumental|remix|radio edit|single edit|rerecord(?:ed|ing)|re-record(?:ed|ing))\b', re.I)


def normalize(value, track=False):
    value = value.strip()
    if track:
        value = REMASTER.sub('', value)
    value = unicodedata.normalize('NFKD', value).casefold()
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.findall(r'\w+', value))


def track_score(canonical, spotify, artist_names):
    if spotify.get('is_playable') is False or spotify.get('restrictions'):
        return 0, False
    credited = {normalize(a['name']) for a in spotify.get('artists', [])}
    if not credited.intersection({normalize(a) for a in artist_names}):
        return 0, False
    left, right = normalize(canonical['title'], True), normalize(spotify['name'], True)
    if set(VERSION.findall(left)) != set(VERSION.findall(right)):
        return 0, False
    exact = left == right
    similarity = SequenceMatcher(None, left, right).ratio()
    isrc = spotify.get('external_ids', {}).get('isrc')
    same_isrc = bool(isrc and isrc in canonical.get('isrcs', []))
    if not exact and not same_isrc and similarity < .85:
        return 0, False
    duration = canonical.get('duration_ms')
    actual = spotify.get('duration_ms')
    delta = abs(actual-duration) if duration and actual else None
    plausible_duration = delta is None or delta <= max(8000, duration*.04)
    if delta is not None and delta > max(30000, duration*.18):
        return 0, False
    score = (95 if exact or same_isrc else similarity*80) + (5 if delta is not None and delta<2000 else 0)
    return score, (exact or same_isrc) and plausible_duration


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


def candidate(album, source):
    mappings = align(album['tracks'], source['all_tracks'], [a['name'] for a in album['artists']])
    verified = sum(m['verified'] for m in mappings)
    matched = sum(bool(m['spotify_id']) for m in mappings)
    clean_title = normalize(album['title']) == normalize(source['name'])
    score = verified*1000 + matched*100 + sum(m['score'] for m in mappings)/max(1,len(mappings))
    score += 30*clean_title + 10*(source.get('release_date','')[:4] == album['release_date'][:4])
    score -= max(0, len(source['all_tracks'])-len(mappings))
    return {'id':source['id'], 'name':source['name'], 'release_date':source.get('release_date',''),
            'track_count':len(source['all_tracks']), 'matched':matched, 'verified':verified,
            'score':score, 'mappings':mappings, 'url':'https://open.spotify.com/album/'+source['id']}
