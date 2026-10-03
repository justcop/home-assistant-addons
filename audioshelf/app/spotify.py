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
from .matching import candidate


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

    def candidates(self, album):
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
        sources = search_pages(f'album:"{title}" artist:"{artist}"')
        if not sources:
            sources = search_pages(f'{title} {artist}')
        candidates, seen = [], set()
        for source in sources:
            if source['id'] not in seen:
                seen.add(source['id'])
                candidates.append(candidate(album, self.album(source['id'])))
        return sorted(candidates, key=lambda c:c['score'], reverse=True)

    def devices(self):
        return [{k: d.get(k) for k in ('id', 'name', 'type', 'is_active', 'is_restricted')}
                for d in self.api('GET', 'me/player/devices').get('devices', []) if d.get('id')]

    def play_tracks(self, album, disc_number=None):
        if not album['canonical_reviewed']:
            raise AppError('Review the MusicBrainz tracklist and choose “This tracklist is correct” before first playback. This prevents a regional bonus edition becoming your original album.')
        tracks = album['tracks']
        if disc_number is not None:
            if type(disc_number) is not int or disc_number < 1:
                raise AppError('Choose a valid disc number.')
            tracks = [t for t in tracks if (t.get('disc_number') or 1) == disc_number]
            if not tracks:
                raise AppError('That disc does not exist on this edition.')
        if (disc_number is None and not album['playable']) or not all(t.get('verified') and t.get('spotify_id') for t in tracks):
            raise AppError('Every canonical track needs a verified Spotify mapping before this album can play.')
        uris = ['spotify:track:'+spotify_id(t['spotify_id'],'track') for t in tracks]
        if len(uris) > 100:
            raise AppError('This album exceeds the MVP limit of 100 tracks.')
        return tracks, uris

    def play(self, album, disc_number=None, preferred_device=None, guard=None, dispatch=None):
        tracks, uris = self.play_tracks(album, disc_number)
        with self.playback_lock:
            if guard:
                guard()
            state = self.api('GET','me/player')
            device = state.get('device',{})
            preferred = preferred_device or self.store.setting('preferred_device')
            if preferred:
                devices = self.devices()
                device = next((d for d in devices if d['id'] == preferred['id']), None)
                if device is None:
                    matches = [d for d in devices if d['name'] == preferred['name'] and d['type'] == preferred['type']]
                    device = matches[0] if len(matches) == 1 else None
                if not device or device.get('is_restricted'):
                    raise AppError('Your preferred Spotify device is unavailable. Open Spotify on it, or choose another device.', 409)
            elif not device.get('id') or device.get('is_restricted'):
                raise AppError('Choose your playback device. Open Spotify on your phone if it is missing, then refresh the device list.', 409)
            if state.get('device', {}).get('id') != device['id'] or state.get('device', {}).get('is_restricted'):
                if guard:
                    guard()
                self.api('PUT', 'me/player', body={'device_ids': [device['id']], 'play': False})
                for attempt in range(10):
                    time.sleep(.3)
                    state = self.api('GET', 'me/player')
                    if state.get('device', {}).get('id') == device['id'] and not state.get('device', {}).get('is_restricted'):
                        break
                else:
                    raise AppError('Spotify has not activated your device yet. Return here and retry.', 409)
            params = {'device_id':device['id']}
            if guard:
                guard()
            if state.get('shuffle_state'):
                self.api('PUT','me/player/shuffle', dict(params,state='false'))
            if state.get('repeat_state') != 'off':
                self.api('PUT','me/player/repeat', dict(params,state='off'))
            if state.get('shuffle_state') or state.get('repeat_state') != 'off':
                for attempt in range(5):
                    time.sleep(.3)
                    state = self.api('GET','me/player')
                    if state.get('device', {}).get('id') == device['id'] and state.get('shuffle_state') is False and state.get('repeat_state') == 'off':
                        break
                else:
                    raise AppError('Turn Shuffle and Repeat off in Spotify, then try Play Album again.')
            # Explicit ordered URIs, never an album context and never bonus-track slicing.
            if guard:
                guard()
            def send():
                return self.api('PUT','me/player/play', params, {'uris':uris,'position_ms':0})
            dispatch(send) if dispatch else send()
            self.store.set_setting('last_played_album', album['id'])
            return {'started':True,'started_at':time.time(),'track_count':len(uris),'device':device.get('name','Spotify'),
                    'first_track':{'id':tracks[0]['spotify_id'],'title':tracks[0]['title'],'duration_ms':tracks[0].get('duration_ms')}}
