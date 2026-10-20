import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests

from .errors import AppError
from .matching import candidate, normalize


def spotify_id(value, kind):
    value = str(value).strip()
    match = re.fullmatch(r'[A-Za-z0-9]{22}', value)
    if match:
        return value
    match = re.fullmatch(r'spotify:'+kind+r':([A-Za-z0-9]{22})', value)
    if match:
        return match[1]
    url = urlsplit(value)
    if url.scheme == 'https' and url.netloc == 'open.spotify.com':
        match = re.fullmatch(r'/(?:intl-[a-z]+/)?'+kind+r'/([A-Za-z0-9]{22})/?', url.path)
        if match:
            return match[1]
    raise AppError('Enter a Spotify '+kind+' link, URI or ID.')


def same_album_family_title(candidate_title, original_title):
    """Permit release/edition labels during DISCOVERY, never in track verification.

    These album suffixes don't establish recording equivalence. Matching still
    requires every original track in the correct order with verified audio.
    """
    original, offered = normalize(original_title), normalize(candidate_title)
    if offered == original:
        return True
    if not offered.startswith(original + ' '):
        return False
    suffix = offered[len(original)+1:]
    return bool(re.fullmatch(
        r'(?:explicit|clean|standard|original|uk|us|deluxe|expanded|extended|digital|special)'
        r'(?: (?:edition|version|release|album))?|'
        r'(?:remastered|remaster)(?: (?:edition|version))?|'
        r'\d{4} (?:remastered|remaster)(?: (?:edition|version))?',
        suffix))


def atomic_private_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(data, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o600)


class Spotify:
    def __init__(self, store, options, private_dir):
        self.store, self.options = store, options
        self.client_id = options.get('spotify_client_id','').strip()
        self.redirect_uri = options.get('spotify_redirect_uri','').strip()
        self.market = options.get('spotify_market','GB').upper()
        if not re.fullmatch('[A-Z]{2}', self.market):
            raise RuntimeError('spotify_market must be a two-letter country code, for example GB.')
        self.token_path = Path(private_dir) / 'spotify.json'
        self.lock = threading.RLock()
        self.playback_lock = threading.Lock()
        self.tokens = {}
        if self.token_path.exists():
            try:
                stored = json.loads(self.token_path.read_text())
                if stored.get('client_id') == self.client_id:
                    self.tokens = stored
            except (ValueError, OSError):
                raise RuntimeError('Spotify token file is unreadable. Restore it or remove it and reconnect.') from None

    @property
    def configured(self):
        url = urlsplit(self.redirect_uri)
        return bool(self.client_id and url.path.endswith('/auth/spotify/callback') and
                    (url.scheme == 'https' or (url.scheme == 'http' and url.hostname in {'127.0.0.1','[::1]','::1'})))

    @property
    def connected(self):
        return bool(self.tokens.get('refresh_token'))

    def authorization(self):
        if not self.configured:
            raise AppError('Set spotify_client_id and an HTTPS spotify_redirect_uri ending in /auth/spotify/callback in add-on configuration.')
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        with self.store.connect() as db:
            db.execute('DELETE FROM oauth_states WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO oauth_states VALUES (?,?,?)', (hashlib.sha256(state.encode()).hexdigest(), verifier, time.time()+600))
        params = {'client_id':self.client_id,'response_type':'code','redirect_uri':self.redirect_uri,
                  'scope':'user-read-playback-state user-modify-playback-state','state':state,'show_dialog':'true',
                  'code_challenge_method':'S256','code_challenge':challenge}
        return 'https://accounts.spotify.com/authorize?' + urlencode(params)

    def _token_request(self, data):
        try:
            response = requests.post('https://accounts.spotify.com/api/token', data=data, timeout=(8,20))
        except requests.RequestException:
            raise AppError('Spotify authorization is unreachable. Please try again.', 502) from None
        if not response.ok:
            if data.get('grant_type') == 'refresh_token' and response.status_code == 400:
                self.disconnect()
            raise AppError('Spotify authorization failed. Reconnect Spotify and check the developer app configuration.', 401)
        try:
            tokens = response.json()
            if not tokens.get('access_token'):
                raise ValueError()
        except ValueError:
            raise AppError('Spotify returned an invalid authorization response.', 502) from None
        return tokens

    def _save(self, tokens):
        with self.lock:
            refresh = tokens.get('refresh_token') or self.tokens.get('refresh_token')
            if not refresh:
                raise AppError('Spotify did not provide a refresh token. Please reconnect.', 401)
            self.tokens = {**tokens,'refresh_token':refresh, 'expires_at':time.time()+tokens.get('expires_in',3600), 'client_id':self.client_id}
            atomic_private_json(self.token_path, self.tokens)

    def callback(self, code, state, denied=False):
        state_key = hashlib.sha256(state.encode()).hexdigest()
        with self.store.connect() as db:
            # Consume once even for expired or denied requests. A concurrent replay cannot win.
            row = db.execute('DELETE FROM oauth_states WHERE state=? RETURNING *', (state_key,)).fetchone()
        if not row or row['expires'] < time.time():
            raise AppError('Spotify connection expired or was already used. Start Connect Spotify again.')
        if denied or not code:
            raise AppError('Spotify connection was cancelled. You can reconnect from Settings.')
        with self.lock:
            tokens = self._token_request({'grant_type':'authorization_code','code':code,'redirect_uri':self.redirect_uri,
                'client_id':self.client_id,'code_verifier':row['verifier']})
            self._save(tokens)

    def disconnect(self):
        with self.lock:
            self.tokens = {}
            self.token_path.unlink(missing_ok=True)

    def token(self, force=False):
        with self.lock:
            if not self.connected:
                raise AppError('Connect Spotify in Settings first.', 401)
            if force or self.tokens.get('expires_at',0) < time.time()+60:
                self._save(self._token_request({'grant_type':'refresh_token','refresh_token':self.tokens['refresh_token'],
                                               'client_id':self.client_id}))
            return self.tokens['access_token']

    def api(self, method, path, params=None, body=None):
        refresh_needed = False
        for attempt in range(3):
            token = self.token(force=attempt == 1 and refresh_needed)
            refresh_needed = False
            try:
                response = requests.request(method, 'https://api.spotify.com/v1/'+path,
                    params=params, json=body, headers={'Authorization':'Bearer '+token}, timeout=(8,30))
            except requests.RequestException:
                raise AppError('Spotify is unreachable. Check the device before trying playback again.', 502) from None
            if response.status_code == 401 and attempt == 0:
                refresh_needed = True
                continue
            if response.status_code == 429:
                try:
                    delay = max(1,int(response.headers.get('Retry-After','5')))
                except ValueError:
                    delay = 5
                if delay <= 8 and attempt < 2:
                    time.sleep(delay)
                    continue
                raise AppError(f'Spotify rate limit reached. Try again in {delay} seconds.', 429)
            if response.status_code == 403:
                raise AppError('Spotify denied access. Check Premium, the app user allowlist and granted playback permissions.', 403)
            if response.status_code == 404:
                raise AppError('Spotify could not find the release or active player. Open Spotify and start playback on your device.', 404)
            if not response.ok:
                raise AppError('Spotify could not complete this request. Please try again.', 502)
            if response.status_code == 204 or not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                raise AppError('Spotify returned an unreadable response.', 502) from None
        raise AppError('Spotify authorization failed. Please reconnect.', 401)

    def album(self, album_id):
        album_id = spotify_id(album_id, 'album')
        key = 'spotify:'+self.market+':'+album_id
        cached = self.store.cache_get(key)
        if cached is not None:
            return cached
        result = self.api('GET','albums/'+album_id, {'market':self.market})
        tracks = list(result.get('tracks',{}).get('items', []))
        page = result.get('tracks',{})
        while page.get('next'):
            page = self.api('GET','albums/'+album_id+'/tracks', {'market':self.market,'limit':50,'offset':len(tracks)})
            batch = page.get('items',[])
            if not batch:
                raise AppError('Spotify returned an incomplete album tracklist.', 502)
            tracks.extend(batch)
        result['all_tracks'] = tracks
        self.store.cache_put(key, result, ttl=3600)
        return result

    def track(self, track_id):
        """Fetch a complete Spotify track only when independent ISRC evidence is needed."""
        track_id = spotify_id(track_id, 'track')
        key = 'spotify:' + self.market + ':track:' + track_id
        cached = self.store.cache_get(key)
        if cached is not None:
            return cached
        result = self.api('GET', 'tracks/' + track_id, {'market': self.market})
        self.store.cache_put(key, result, ttl=3600)
        return result

    def candidates(self, album, broad=False):
        artists = [a['name'] for a in album['artists']]
        if not artists:
            raise AppError('This album has no MusicBrainz artist credit. Choose another catalogue entry.')
        title = album['title'].replace('"','')
        artist = artists[0].replace('"','')
        def search_pages(query):
            sources = []
            for offset in (0, 10, 20):
                result = self.api('GET', 'search', {'q': query, 'type': 'album', 'limit': 10,
                    'offset': offset, 'market': self.market}).get('albums', {})
                sources.extend(result.get('items', []))
                if not result.get('next'):
                    break
            return sources
        # The first, fielded search can return albums without the chosen
        # tracklist. The caller can explicitly retry with an unfielded query.
        # Do not mistake "some search results" for "a verified match".
        sources = search_pages(f'{title} {artist}') if broad else search_pages(f'album:"{title}" artist:"{artist}"')
        if not sources and not broad:
            sources = search_pages(f'{title} {artist}')
        candidates, seen = [], set()
        def consider(options):
            for source in options:
                if source['id'] not in seen:
                    seen.add(source['id'])
                    candidates.append(candidate(album, self.album(source['id'])))
        consider(sources)
        # Spotify sometimes credits joint acts as individual performers or
        # uses a different album-artist identity. When ordinary search fails
        # to verify a complete album, search the *album title alone* and
        # require an exact normalised title before checking all performers.
        # A matching name alone is never sufficient to save a recording.
        if broad and not any(c['verified'] == len(album['tracks']) and album['tracks']
                             for c in candidates):
            bare = normalize(album['title'])
            title_options = [item for item in search_pages(f'album:"{title}"')
                             if same_album_family_title(item.get('name', ''), album['title'])]
            consider(title_options)
        return sorted(candidates, key=lambda c:c['score'], reverse=True)

    def labelled_editions(self, album):
        """Discover alternate Spotify edition labels once per unresolved album.

        Album searches can rank a 16-track release before a standard 14-track
        Explicit Version. Exact fielded title + artist search also misses
        region-labelled UK editions. Probe the general labels, not album IDs,
        and let the full recording verifier decide whether any are suitable.
        """
        name = album['title'].replace('"', '')
        queries = [
            f'album:"{name} (Explicit Version)"',
            f'album:"{name} (UK edition)"',
            f'album:"{name} (Deluxe Edition)"',
        ]
        items, seen = [], set()
        for query in queries:
            page = self.api('GET', 'search', {
                'q': query, 'type': 'album', 'market': self.market,
                'limit': 20, 'offset': 0}).get('albums', {})
            for source in page.get('items', []):
                identifier = source.get('id')
                if (identifier and identifier not in seen and
                        same_album_family_title(source.get('name', ''), album['title'])):
                    seen.add(identifier)
                    items.append(identifier)
        return items

    def devices(self):
        return [{k: d.get(k) for k in ('id', 'name', 'type', 'is_active', 'is_restricted')}
                for d in self.api('GET', 'me/player/devices').get('devices', []) if d.get('id')]

    def play_tracks(self, album, disc_number=None):
        if not album['canonical_reviewed']:
            raise AppError('Review the MusicBrainz tracklist and choose “This tracklist is correct” before first playback. This prevents a regional bonus edition becoming your original album.')
        tracks = album['tracks']
        if disc_number is not None:
            if type(disc_number) is int and disc_number > 0:
                tracks = [t for t in tracks if (t.get('disc_number') or 1) == disc_number]
                if not tracks:
                    raise AppError('That disc does not exist on this edition.')
            elif isinstance(disc_number, str) and re.fullmatch(r'[1-9]\d*:[A-Z]', disc_number):
                number, side = disc_number.split(':')
                matching = next((part for part in album.get('playback_sides', [])
                                 if part['disc_number'] == int(number) and part['side'] == side), None)
                if not matching:
                    raise AppError('That vinyl side is not available on this edition.')
                positions = set(matching['positions'])
                tracks = [t for t in tracks if t['position'] in positions]
                if not tracks:
                    raise AppError('That vinyl side has no tracks.')
            else:
                raise AppError('Choose a valid disc number or numbered vinyl side.')
        if (disc_number is None and not album['playable']) or not all(t.get('verified') and t.get('spotify_id') for t in tracks):
            raise AppError('Every canonical track needs a verified Spotify mapping before this album can play.')
        uris = ['spotify:track:'+spotify_id(t['spotify_id'],'track') for t in tracks]
        if len(uris) > 100:
            raise AppError('This album exceeds the MVP limit of 100 tracks.')
        return tracks, uris

    def play(self, album, disc_number=None, preferred_device=None, guard=None,
             dispatch=None, resolved_device=None, progress=None):
        """Play the verified tracklist on one explicitly chosen Spotify device.

        Background handoffs issue one Play directly to the discovered phone,
        bypassing stale transfer acknowledgements. Its playback modes are
        corrected and verified by PlaybackHandoff after the phone is active.
        Legacy direct plays retain their existing transfer/mode safeguards.
        """
        tracks, uris = self.play_tracks(album, disc_number)
        def stage(name):
            if guard:
                guard()
            if progress:
                progress(name)
        with self.playback_lock:
            if resolved_device is not None:
                # The background handoff has *already* discovered this exact
                # Connect device. In particular, GET /me/player can describe
                # an old speaker (or an inactive/empty player) indefinitely.
                # Neither a paused transfer nor its acknowledgement is needed
                # to dispatch PUT /me/player/play with a device_id.
                stage('preparing_device_check')
                preferred = preferred_device or self.store.setting('preferred_device')
                device = resolved_device
                if (not preferred or not device.get('id') or device.get('is_restricted')
                        or (device['id'] != preferred['id']
                            and (device.get('name') != preferred.get('name')
                                 or device.get('type') != preferred.get('type')))):
                    raise AppError('Your preferred Spotify device is unavailable. Open Spotify on it, or choose another device.', 409)
                params = {'device_id': device['id']}
                stage('preparing_explicit_play')
                def send_to_discovered_phone():
                    return self.api('PUT', 'me/player/play', params, {'uris': uris, 'position_ms': 0})
                dispatch(send_to_discovered_phone) if dispatch else send_to_discovered_phone()
                self.store.set_setting('last_played_album', album['id'])
                return {'started': True, 'started_at': time.time(), 'track_count': len(uris),
                        'device': device.get('name', 'Spotify'), 'device_id': device['id'],
                        'first_track': {'id': tracks[0]['spotify_id'], 'title': tracks[0]['title'],
                                        'duration_ms': tracks[0].get('duration_ms')},
                        # The server must confirm modes *on this phone*, not
                        # on the stale current-player snapshot before Play.
                        'modes_need_confirmation': True}
            stage('preparing_player_state')
            try:
                state = self.api('GET', 'me/player')
            except AppError as error:
                if error.status != 404:
                    raise
                # Some Spotify clients have no active player despite appearing
                # in the device list. A missing player isn't a missing device.
                state = {}
            preferred = preferred_device or self.store.setting('preferred_device')
            if preferred:
                stage('preparing_device_check')
                if resolved_device is None:
                    devices = self.devices()
                    device = next((d for d in devices if d['id'] == preferred['id']), None)
                    if device is None:
                        matches = [d for d in devices if d['name'] == preferred['name'] and d['type'] == preferred['type']]
                        device = matches[0] if len(matches) == 1 else None
                else:
                    device = resolved_device
                    if device.get('id') != preferred['id'] and (
                            device.get('name') != preferred['name']
                            or device.get('type') != preferred['type']):
                        device = None
                if not device or not device.get('id') or device.get('is_restricted'):
                    raise AppError('Your preferred Spotify device is unavailable. Open Spotify on it, or choose another device.', 409)
            else:
                device = state.get('device') or {}
                if not device.get('id') or device.get('is_restricted'):
                    raise AppError('Choose your playback device. Open Spotify on your phone if it is missing, then refresh the device list.', 409)
            if (state.get('device') or {}).get('id') != device['id'] or (state.get('device') or {}).get('is_restricted'):
                stage('preparing_device_transfer')
                self.api('PUT', 'me/player', body={'device_ids': [device['id']], 'play': False})
                activated = False
                for attempt in range(10):
                    stage('preparing_transfer_ack')
                    time.sleep(.3)
                    try:
                        state = self.api('GET', 'me/player')
                    except AppError as error:
                        if error.status != 404:
                            raise
                        state = {}
                    if (state.get('device') or {}).get('id') == device['id'] and not (state.get('device') or {}).get('is_restricted'):
                        activated = True
                        break
                if not activated:
                    # A paused Spotify Connect transfer can succeed while GET
                    # /me/player keeps reporting the old player. Don't loop
                    # indefinitely if the exact target is still available AND
                    # shuffle/repeat are explicitly known to be disabled.
                    # Never guess if those playback-mode flags are unknown.
                    stage('preparing_transfer_fallback')
                    available = [d for d in self.devices()
                                 if d.get('id') == device['id'] and not d.get('is_restricted')]
                    if len(available) != 1 or (state.get('shuffle_state') is not False
                                                or state.get('repeat_state') != 'off'):
                        raise AppError('Spotify has not activated your device yet. Return here and retry.', 409)
                    # Explicit device_id is still used in Play; any ambiguous
                    # Play result is NEVER retried or targeted to another device.
            params = {'device_id': device['id']}
            stage('preparing_playback_modes')
            if state.get('shuffle_state'):
                self.api('PUT', 'me/player/shuffle', dict(params, state='false'))
            if state.get('repeat_state') != 'off':
                self.api('PUT', 'me/player/repeat', dict(params, state='off'))
            if state.get('shuffle_state') or state.get('repeat_state') != 'off':
                for attempt in range(5):
                    stage('preparing_modes_ack')
                    time.sleep(.3)
                    state = self.api('GET', 'me/player')
                    if ((state.get('device') or {}).get('id') == device['id']
                            and state.get('shuffle_state') is False
                            and state.get('repeat_state') == 'off'):
                        break
                else:
                    raise AppError('Turn Shuffle and Repeat off in Spotify, then try Play Album again.')
            # Explicit ordered URIs, never an album context or bonus tracks.
            stage('preparing_explicit_play')
            def send():
                return self.api('PUT', 'me/player/play', params, {'uris': uris, 'position_ms': 0})
            dispatch(send) if dispatch else send()
            self.store.set_setting('last_played_album', album['id'])
            return {'started': True, 'started_at': time.time(), 'track_count': len(uris),
                    'device': device.get('name', 'Spotify'), 'device_id': device['id'],
                    'first_track': {'id': tracks[0]['spotify_id'], 'title': tracks[0]['title'],
                                    'duration_ms': tracks[0].get('duration_ms')}}
