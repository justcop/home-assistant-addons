"""Read-only links into the signed-in account's collection."""
from urllib.parse import quote
from .matching import normalize


def shelf_matches(store, kind, artist, name, albums):
    artist_key = normalize(artist)
    if kind == 'artist':
        matches = [a for a in store.artists() if normalize(a['name']) == artist_key]
        # Names shared by different artists need a choice rather than a guessed ID.
        return [dict(label=a['name'], path='#shelf/'+quote(a['id'], safe='')) for a in matches]
    candidates = [a for a in store.albums(owned=True)
                  if any(normalize(c['name']) == artist_key for c in a['artists'])]
    if kind == 'album':
        candidates = [a for a in candidates if normalize(a['title'], True) == normalize(name, True)]
    else:
        # Keep live/remix distinctions. Prefer albums present in listening history.
        candidates = [a for a in candidates if any(
            normalize(t['title'], True) == normalize(name, True)
            for t in store.album(a['id'])['tracks'])]
        album_keys = [normalize(a, True) for a in albums]
        preferred = [a for a in candidates if normalize(a['title'], True) in album_keys]
        if preferred:
            candidates = sorted(preferred, key=lambda a: album_keys.index(normalize(a['title'], True)))
    return [dict(label=a['title'], path='#album/'+quote(a['id'], safe='')) for a in candidates]
