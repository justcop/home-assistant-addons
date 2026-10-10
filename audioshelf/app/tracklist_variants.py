"""Condense MusicBrainz pressings by musical tracklist before contacting Spotify.

A release is a manufactured/territorial edition, not a distinct set of songs.
Group by ordered titles and track performers (not recording IDs or format), but
retain disc layouts and reliable printed vinyl sides as playback choices.
"""
import hashlib
import json
import re
import unicodedata
from collections import Counter

from .musicbrainz import credited_artists
from .release_filters import VIDEO_FORMATS, format_family

# Only strip edition descriptors that do not change what song is being played.
# Never erase 'live', 'demo', 'acoustic', 'radio edit', 'remix' or 'alternate take'.
REMASTER = re.compile(
    r'\s*[(\[]\s*(?:(?:19|20)\d{2}\s+)?(?:re-?master(?:ed)?(?:\s+(?:version|edition))?)'
    r'(?:\s+(?:19|20)\d{2})?\s*[)\]]\s*$', re.I)
SIDE_NUMBER = re.compile(r'^([A-Z])\s*[-.]?\s*\d{1,3}$')


def title_key(value):
    value = unicodedata.normalize('NFKC', value or '')
    value = REMASTER.sub('', value)
    return ' '.join(re.findall(r'[^\W_]+', value.casefold(), re.UNICODE))


def signature(tracks):
    # Recording IDs and small timing differences may vary across reissues.
    # Per-track credits protect genuinely different performers.
    canonical = [
        (title_key(t['title']), tuple(title_key(name) for name in t.get('artist_names', [])))
        for t in tracks
    ]
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def compact_tracks(tracks):
    return [{'title': t['title'], 'disc_number': t['disc_number'],
             'artist_names': t.get('artist_names', [])} for t in tracks]


def disc_layout(tracks):
    counts = []
    for track in tracks:
        number = track['disc_number']
        if not counts or counts[-1][0] != number:
            counts.append([number, 0])
        counts[-1][1] += 1
    return counts


def vinyl_sides(release):
    """Use printed MusicBrainz side numbers; never guess from track durations."""
    segments = []
    global_pos = 0
    for medium in sorted(release.get('media', []), key=lambda item: item.get('position', 1)):
        if medium.get('format') in VIDEO_FORMATS:
            continue
        tracks = [t for t in sorted(medium.get('tracks', []), key=lambda t: t.get('position', 1))
                  if not (t.get('recording') or {}).get('video')]
        first = global_pos + 1
        global_pos += len(tracks)
        if format_family(medium.get('format')) != 'vinyl' or not tracks:
            continue
        letters = []
        for track in tracks:
            match = SIDE_NUMBER.fullmatch(str(track.get('number') or '').strip().upper())
            if match is None:
                letters = []
                break
            letters.append(match.group(1))
        # A medium normally contains sides A/B; avoid incorrectly splitting a
        # missing/inconsistent numbering scheme or non-contiguous side labels.
        distinct = list(dict.fromkeys(letters))
        if len(distinct) < 2 or len(distinct) != len(set(distinct)):
            continue
        if any(letters[i] in letters[i+1:] and letters[i] != letters[i+1]
               for i in range(len(letters)-1)):
            continue
        for side in distinct:
            positions = [first + i for i, label in enumerate(letters) if label == side]
            if positions:
                segments.append({'disc_number': medium.get('position', 1),
                                 'side': side, 'positions': positions})
    return segments


def layout_from_release(release, tracks):
    discs = disc_layout(tracks)
    sides = vinyl_sides(release)
    layout_key = json.dumps([discs, [(s['disc_number'], s['side'], len(s['positions']))
                                     for s in sides]], separators=(',', ':'))
    formats = list(dict.fromkeys(m.get('format') or 'Audio' for m in release.get('media', [])
                                 if m.get('format') not in VIDEO_FORMATS))
    return {'key': layout_key, 'release_id': release['id'], 'country': release.get('country'),
            'date': release.get('date'), 'formats': formats, 'discs': discs,
            'sides': sides, 'pressings': 1}


def group_releases(previews):
    """Ordered, deterministic grouping, with deduplication of equivalent layouts."""
    variants = []
    groups = {}
    for release, tracks in previews:
        key = signature(tracks)
        layout = layout_from_release(release, tracks)
        if key not in groups:
            variant = {'signature': key, 'tracks': compact_tracks(tracks),
                       'track_count': len(tracks), 'pressings': 0, 'layouts': []}
            groups[key] = variant
            variants.append(variant)
        variant = groups[key]
        variant['pressings'] += 1
        previous = next((entry for entry in variant['layouts']
                         if entry['key'] == layout['key']), None)
        if previous:
            previous['pressings'] += 1
        else:
            variant['layouts'].append(layout)
    return variants


def difference(reference, alternative):
    """Explain musical differences without relying on release marketing labels."""
    first = [title_key(t['title']) for t in reference]
    second = [title_key(t['title']) for t in alternative]
    old, new = Counter(first), Counter(second)
    added = list((new-old).elements())
    removed = list((old-new).elements())
    # Report reader-friendly source titles, not normalized fingerprint text.
    remaining_add = Counter(added)
    remaining_remove = Counter(removed)
    add_titles, remove_titles = [], []
    for track in alternative:
        key = title_key(track['title'])
        if remaining_add[key] > 0:
            add_titles.append(track['title'])
            remaining_add[key] -= 1
    for track in reference:
        key = title_key(track['title'])
        if remaining_remove[key] > 0:
            remove_titles.append(track['title'])
            remaining_remove[key] -= 1
    return {'added': add_titles, 'removed': remove_titles,
            'reordered': not added and not removed and first != second,
            'same': not added and not removed and first == second}
