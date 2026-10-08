"""Resolve Spotify Now Playing artwork and links to canonical AudioShelf albums.

Matching runs on cover taps, not in the frequent playback-status request.
"""
import re
import unicodedata
from urllib.parse import urlsplit

EDITION = re.compile(r'\b(?:deluxe|expanded|remaster(?:ed)?|anniversary|special edition|'
    r'collector.s edition|bonus tracks?|reissue|mono|stereo|\d{4}\s+mix)\b', re.I)
SUFFIX = re.compile(r'\s*[-–—]\s*(?:\d{4}\s*)?(?:deluxe|expanded|remaster(?:ed)?|'
    r'anniversary|special edition|collector.s edition|bonus tracks?|reissue|mono|stereo|mix)\b.*$', re.I)


def canonical_title(title):
    value = str(title or '').strip()[:160]
    for _ in range(3):
        original = value
        match = re.search(r'\s*[\[(]([^\[\]()]+)[\])]\s*$', value)
        if match and EDITION.search(match.group(1)):
            value = value[:match.start()].rstrip()
        value = SUFFIX.sub('', value).rstrip()
        if value == original:
            break
    return value or str(title or '').strip()[:160]


def normalize(text):
    value = unicodedata.normalize('NFKD', str(text or '').casefold())
    return ''.join(c for c in value if c.isalnum() and not unicodedata.combining(c))


def spotify_artwork(images):
    """Use only Spotify CDN HTTPS images, preferring >=300px for retina screens."""
    valid = []
    for image in images if isinstance(images, list) else []:
        if not isinstance(image, dict) or not isinstance(image.get('url'), str):
            continue
        url = image['url']
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in {'i.scdn.co', 'mosaic.scdn.co'} or parsed.username or parsed.password:
            continue
        width = image.get('width')
        valid.append((width if isinstance(width, int) and width > 0 else 0, url))
    if not valid:
        return None
    return min((item for item in valid if item[0] >= 300), default=max(valid), key=lambda item: item[0])[1]


def resolve_album(store, musicbrainz, track):
    """Prefer verified mappings; otherwise match exact artist and canonical title.

    Ambiguous/non-studio material opens an AudioShelf Record Store search.
    """
    source = track.get('album') or {}
    spotify_id = source.get('id') if isinstance(source.get('id'), str) else None
    artists = [a.get('name', '') for a in source.get('artists', []) if isinstance(a, dict)]
    if not artists:
        artists = [a.get('name', '') for a in track.get('artists', []) if isinstance(a, dict)]
    title = canonical_title(source.get('name', ''))
    artist = artists[0] if artists else ''
    ids = [v for v in (track.get('id'), (track.get('linked_from') or {}).get('id')) if isinstance(v, str)]
    with store.connect() as db:
        matches = set()
        if spotify_id:
            matches.update(row[0] for row in db.execute('SELECT id FROM albums WHERE spotify_album_id=?', (spotify_id,)))
        if ids:
            placeholders = ','.join('?' for _ in ids)
            matches.update(row[0] for row in db.execute(
                f'SELECT DISTINCT album_id FROM tracks WHERE verified=1 AND spotify_id IN ({placeholders})', ids))
    if len(matches) == 1:
        album = store.album(next(iter(matches)))
        return {'view': 'album', 'album_id': album['id'], 'on_shelf': bool(album['on_shelf'])}
    if title and artist and not matches:
        results = musicbrainz.search(title, 'album')
        exact = [a for a in results if normalize(canonical_title(a['title'])) == normalize(title)
                 and any(normalize(c.get('name')) == normalize(artist) for c in a.get('artists', []))]
        if len(exact) == 1:
            album = exact[0]
            return {'view': 'album', 'album_id': album['id'], 'on_shelf': bool(album['on_shelf'])}
    return {'view': 'store-search', 'query': title or source.get('name') or track.get('name', '')}
