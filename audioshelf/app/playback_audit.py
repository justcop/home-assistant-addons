"""Read-only, per-attempt Spotify catalogue evidence for a dispatched record.

A Spotify HTTP 204 is only command acceptance, not proof that Spotify can
resolve the queued track IDs. This probe never sends playback commands or
modifies the verified canonical mapping. Only bounded, safe metadata reaches
the helper status or add-on logs.
"""
import hashlib
import logging
import re

from .errors import AppError

LOG = logging.getLogger('audioshelf')


def tag(value):
    return hashlib.sha256(value.encode()).hexdigest()[:10] if isinstance(value, str) and value else None


def label(value):
    return ''.join(c if c.isprintable() else ' ' for c in value).strip()[:64] if isinstance(value, str) else ''


def catalogue_entry(track, position, item=None, error=None):
    """Spotify market response may be relinked; never equate name with availability."""
    saved = track.get('spotify_id')
    row = {'position': position, 'title': label(track.get('title')),
           'saved_tag': tag(saved), 'status': 'error' if error else 'missing',
           'playable': 'unknown', 'relinked': False, 'returned_tag': None,
           'restriction': None, 'spotify_title_matches': None}
    if error is not None:
        row['status'] = ('not_found' if isinstance(error, AppError) and error.status == 404
                         else 'rate_limited' if isinstance(error, AppError) and error.status == 429
                         else 'access_denied' if isinstance(error, AppError) and error.status in (401,403)
                         else 'request_failed')
        return row
    if not isinstance(item, dict) or not item.get('id'):
        return row
    reported = item['id']
    linked = (item.get('linked_from') or {}).get('id')
    restriction = (item.get('restrictions') or {}).get('reason')
    row['status'] = 'found'
    row['playable'] = ('no' if item.get('is_playable') is False or restriction
                       else 'yes' if item.get('is_playable') is True else 'unknown')
    row['relinked'] = bool(reported != saved and linked == saved)
    row['returned_tag'] = tag(reported)
    row['restriction'] = restriction if restriction in ('market','product','explicit') else 'other' if restriction else None
    original = label(track.get('title')).casefold()
    spotify_title = label(item.get('name')).casefold()
    row['spotify_title_matches'] = original == spotify_title if original and spotify_title else None
    return row


def inspect_dispatched_tracks(spotify, album, disc, wanted_device_id, publish):
    """Probe first, second and final queued IDs separately; never block Play.

    GET /tracks/{id}?market=GB can relink a track, but a user access token's
    account country takes precedence. The returned playable flag may be absent
    in some Spotify responses; report unknown rather than assuming true.
    """
    tracks, uris = spotify.play_tracks(album, disc)
    if not isinstance(tracks, list) or not isinstance(uris, list) or not tracks:
        return
    market = spotify.market if isinstance(spotify.market, str) and re.fullmatch('[A-Z]{2}', spotify.market) else 'unknown'
    indices = list(dict.fromkeys([0, 1 if len(tracks)>1 else 0, len(tracks)-1]))
    audit = {'state': 'checking', 'market': market, 'track_count': len(uris),
             'queue_tag': tag('|'.join(uris)),
             'samples': [], 'phone_visible_after_play': 'unknown',
             'phone_active_after_play': 'unknown'}
    publish(dict(audit, samples=list(audit['samples'])))
    for index in indices:
        track = tracks[index]
        try:
            item = spotify.api('GET', 'tracks/'+track['spotify_id'], {'market': market})
            sample = catalogue_entry(track, index+1, item=item)
        except AppError as error:
            sample = catalogue_entry(track, index+1, error=error)
        except Exception:
            sample = catalogue_entry(track, index+1, error=RuntimeError())
        audit['samples'].append(sample)
        publish(dict(audit, samples=list(audit['samples'])))
    try:
        devices = spotify.devices()
        selected = [d for d in devices if d.get('id') == wanted_device_id]
        audit['phone_visible_after_play'] = 'yes' if selected else 'no'
        audit['phone_active_after_play'] = ('yes' if selected[0].get('is_active') else 'no'
                                            if selected else 'unknown')
    except Exception:
        pass
    audit['state'] = 'complete'
    publish(dict(audit, samples=list(audit['samples'])))
    # No credentials, exact track/device IDs, entire Spotify API objects or
    # web URLs in either HA add-on logs or the helper's bearer-protected view.
    LOG.info('Spotify playback catalogue audit: %s', audit)
