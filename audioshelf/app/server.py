import io
import hashlib
import json
import logging
import os
import secrets
import time
import requests
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException

from .artwork import Artwork
from .errors import AppError
from .matching import candidate, track_score
from .musicbrainz import MusicBrainz, mbid
from .release_filters import matches_filters, validate_filters
from .spotify import Spotify, atomic_private_json, spotify_id
from .storage import Store
from .themes import THEMES, THEME_IDS
from .security import Security

LOG = logging.getLogger('audioshelf')


def load_options():
    path = Path(os.environ.get('AUDIOSHELF_OPTIONS','/data/options.json'))
    options = json.loads(path.read_text()) if path.exists() else {}
    for key in ('data_directory','cache_directory','spotify_client_id','spotify_redirect_uri','spotify_market','web_password'):
        value = os.environ.get('AUDIOSHELF_'+key.upper())
        if value is not None:
            options[key] = value
    options.setdefault('data_directory','/share/audioshelf')
    return options


def create_app(options=None):
    options = options if options is not None else load_options()
    cache_directory = options.get('cache_directory') or os.environ.get('AUDIOSHELF_CACHE_DIRECTORY')
    if not cache_directory:
        cache_directory = str(Path(options['private_directory']).parent/'cache') if options.get('private_directory') else '/share/audioshelf-cache'
    collection = Path(options.get('data_directory','/share/audioshelf')).resolve()
    cache = Path(cache_directory).resolve()
    if cache == collection or collection in cache.parents or cache in collection.parents:
        raise RuntimeError('Keep cache_directory separate from data_directory, without either containing the other.')
    store = Store(collection,cache)
    private_dir = Path(options.get('private_directory') or os.environ.get('AUDIOSHELF_PRIVATE_DIRECTORY','/data/audioshelf-private'))
    private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret_path = private_dir/'session.json'
    if not secret_path.exists():
        atomic_private_json(secret_path, {'key':secrets.token_hex(32)})
    app = Flask(__name__)
    app.config.update(SECRET_KEY=json.loads(secret_path.read_text())['key'], MAX_CONTENT_LENGTH=7*1024*1024,
                      SESSION_COOKIE_NAME='audioshelf_session', SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=True, PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    musicbrainz, spotify = MusicBrainz(store), Spotify(store, options, private_dir)
    artwork = Artwork(store,spotify,musicbrainz)
    app.extensions.update(store=store, musicbrainz=musicbrainz, spotify=spotify, artwork=artwork)
    build_path = Path(__file__).resolve().parents[1]/'build.json'
    build = json.loads(build_path.read_text()) if build_path.exists() else {'version':'0.1.0','channel':'local','revision':'local'}
    build['version'] = os.environ.get('AUDIOSHELF_VERSION',build['version'])
    assets = Path(__file__).resolve().parent / 'static'
    digest = hashlib.sha256(b''.join((assets / name).read_bytes() for name in ('app.js', 'style.css', 'vinyl.js', 'vinyl.css', 'sw.js'))).hexdigest()[:16]
    build['asset_version'] = build['version']+'-'+digest
    password = options.get('web_password','')
    security = Security(private_dir, password, options.get('allow_support_access', False))
    if security.recover_from_options(options.get('two_factor_reset_request', '')):
        LOG.warning('Home Assistant configuration reset two-factor authentication and revoked existing access. The standalone password is still required.')
    app.extensions['security'] = security

    def ingress():
        # Only the Supervisor's ingress proxy can vouch for HA authentication.
        return request.remote_addr == '172.30.32.2' and bool(request.headers.get('X-Ingress-Path'))

    def identity():
        if ingress():
            return {'role': 'owner', 'ingress': True}
        if not password:
            return None
        return security.identity(session.get('sid'))

    def authenticated():
        return identity() is not None

    @app.before_request
    def protect():
        if request.method in {'POST','PUT','PATCH','DELETE'}:
            if request.headers.get('X-AudioShelf-Request') != '1' or not request.is_json:
                raise AppError('Please perform this action from AudioShelf.', 403)
            if not isinstance(request.get_json(silent=True), dict):
                raise AppError('Supply a JSON object.', 400)
            origin = request.headers.get('Origin')
            allowed_hosts = {request.host}
            external = urlsplit(options.get('spotify_redirect_uri', ''))
            if external.scheme == 'https' and external.netloc:
                allowed_hosts.add(external.netloc)
            if origin and not ingress() and urlsplit(origin).netloc not in allowed_hosts:
                raise AppError('Use the same AudioShelf address for this action.', 403)
        if request.path.startswith('/api/') and request.path not in {'/api/status','/api/login'}:
            g.identity = identity()
            if g.identity is None:
                raise AppError('Enter your AudioShelf password. If none is set, configure one in Home Assistant or use ingress.', 401)
            if g.identity['role'] != 'owner':
                forbidden = (request.path.startswith('/api/security') or request.path in {
                    '/api/spotify/connect', '/api/spotify/disconnect', '/api/export', '/api/backup'}
                    or request.path.endswith('/diagnostics'))
                if forbidden or (g.identity['role'] == 'view' and request.method not in {'GET','HEAD'} and request.path != '/api/logout'):
                    raise AppError('This temporary login does not permit that action.', 403)

    def owner_reauth():
        if not g.identity or g.identity['role'] != 'owner':
            raise AppError('Owner access required.', 403)
        if ingress():
            return
        security.throttle(request.remote_addr)
        if not security.owner_credentials(request.json.get('password'), request.json.get('code')):
            raise AppError('Confirm your owner password and a fresh authenticator or recovery code.', 401)
        security.clear_attempts(request.remote_addr)

    def owner_session():
        session.clear()
        session['sid'] = security.create_session()
        session.permanent = True

    @app.after_request
    def headers(response):
        if request.path.startswith(('/api/','/auth/')) or request.path == '/':
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' https://coverartarchive.org https://*.archive.org https://*.scdn.co data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'"
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        return response

    @app.errorhandler(AppError)
    def expected(error):
        parts = request.path.split('/')
        if len(parts) > 3 and parts[1:3] == ['api', 'albums'] and parts[-1] != 'diagnostics':
            store.diagnostic(parts[3], 'request_error', {'action': parts[4:] or ['open'], 'status': error.status, 'message': str(error)})
        return jsonify(error=str(error)), error.status

    @app.errorhandler(KeyError)
    def missing(error):
        return jsonify(error='That item could not be found.'), 404

    @app.errorhandler(Exception)
    def unexpected(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        LOG.exception('AudioShelf request failed')
        parts = request.path.split('/')
        if len(parts) > 3 and parts[1:3] == ['api', 'albums'] and parts[-1] != 'diagnostics':
            store.diagnostic(parts[3], 'request_error', {'action': parts[4:] or ['open'], 'status': 500,
                'message': 'AudioShelf could not complete this request. Check the add-on log.'})
        return jsonify(error='AudioShelf could not complete this request. Check the add-on log.'), 500

    @app.get('/')
    def index():
        prefix = request.headers.get('X-Ingress-Path','') if ingress() else ''
        if not prefix.startswith('/') or any(c in prefix for c in ['<','>','"',"'",'\\']) or prefix.startswith('//'):
            prefix = ''
        return render_template('index.html', base=prefix.rstrip('/')+'/', asset_version=build['asset_version'])

    @app.get('/health')
    def health():
        with store.connect() as db:
            db.execute('SELECT 1').fetchone()
        return jsonify(ok=True, version=build['version'])

    @app.get('/sw.js')
    def service_worker():
        response = app.send_static_file('sw.js')
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/status')
    def status():
        result = {'build':build,'authenticated':authenticated(),'password_required':not ingress(), 'password_configured':bool(password), 'two_factor_enabled':bool(security.get('totp'))}
        if authenticated():
            result.update(role=identity()['role'], spotify_configured=spotify.configured, spotify_connected=spotify.connected,
                          spotify_redirect_uri=spotify.redirect_uri, data_directory=str(store.directory), cache_directory=str(store.cache_directory), market=spotify.market,
                          release_filters=store.release_filters(), interface=store.setting('interface', 'vinyl'), theme=store.setting('theme', 'record-store'), themes=THEMES, preferred_device=store.setting('preferred_device'))
        return jsonify(result)

    @app.get('/api/settings')
    def settings():
        return jsonify(release_filters=store.release_filters(), interface=store.setting('interface', 'vinyl'), theme=store.setting('theme', 'record-store'), themes=THEMES, preferred_device=store.setting('preferred_device'))

    @app.put('/api/settings')
    def save_settings():
        body = request.json
        if not isinstance(body, dict) or not set(body).intersection({'release_filters', 'theme', 'preferred_device', 'interface'}):
            raise AppError('Supply release filters, an appearance preference or a preferred device.')
        filters = validate_filters(body['release_filters']) if 'release_filters' in body else None
        theme = body.get('theme')
        if 'theme' in body and (not isinstance(theme, str) or theme not in THEME_IDS):
            raise AppError('Choose one of the available themes.')
        interface = body.get('interface')
        if 'interface' in body and interface not in ('vinyl', 'classic'):
            raise AppError('Choose the Vinyl or Classic interface.')
        preferred = body.get('preferred_device')
        if 'preferred_device' in body and preferred is not None:
            if not isinstance(preferred, dict) or set(preferred) != {'id', 'name', 'type'} or not all(isinstance(v, str) and 0 < len(v) <= 200 for v in preferred.values()):
                raise AppError('Choose an available Spotify device.')
            available = spotify.devices()
            preferred = next((d for d in available if d['id'] == preferred['id'] and not d['is_restricted']), None)
            if preferred is None:
                raise AppError('That device is unavailable. Refresh devices and try again.')
            preferred = {k: preferred[k] for k in ('id', 'name', 'type')}
        with store.catalogue_lock:
            if interface is not None:
                store.set_setting('interface', interface)
            if 'preferred_device' in body:
                store.set_setting('preferred_device', preferred)
            if filters is not None:
                store.set_release_filters(filters)
            if theme is not None:
                store.set_setting('theme', theme)
        return jsonify(release_filters=store.release_filters(), interface=store.setting('interface', 'vinyl'), theme=store.setting('theme', 'record-store'), preferred_device=store.setting('preferred_device'))

    @app.post('/api/login')
    def login():
        body = request.json
        old = session.get('sid')
        token, role = security.login(body.get('password'), body.get('code'), request.remote_addr,
                                     request.cookies.get('audioshelf_trusted'))
        security.logout(old)
        session.clear(); session['sid'] = token; session.permanent = True
        response = jsonify(ok=True, role=role)
        if role == 'owner' and body.get('remember') is True:
            security.forget_trust(request.cookies.get('audioshelf_trusted'))
            response.set_cookie('audioshelf_trusted', security.trust(), max_age=30*86400,
                                secure=True, httponly=True, samesite='Lax')
        return response

    @app.post('/api/logout')
    def logout():
        security.logout(session.get('sid')); security.forget_trust(request.cookies.get('audioshelf_trusted')); session.clear()
        response = jsonify(ok=True)
        response.delete_cookie('audioshelf_trusted', secure=True, httponly=True, samesite='Lax')
        return response

    @app.get('/api/security')
    def security_overview():
        return jsonify({**security.overview(), 'ingress':ingress()})

    @app.post('/api/security/totp/start')
    def totp_start():
        owner_reauth()
        if not password:
            raise AppError('Set a standalone web password in Home Assistant before enabling two-factor authentication.')
        security.throttle(request.remote_addr)
        return jsonify(security.start_totp())

    @app.post('/api/security/totp/confirm')
    def totp_confirm():
        owner_reauth(); security.throttle((request.remote_addr or '')+':setup')
        codes = security.confirm_totp(request.json.get('setup_code'))
        security.clear_attempts((request.remote_addr or '')+':setup'); owner_session()
        return jsonify(recovery_codes=codes)

    @app.post('/api/security/totp/disable')
    def totp_disable():
        owner_reauth(); security.disable_totp(); owner_session()
        return jsonify(ok=True)

    @app.post('/api/security/revoke-sessions')
    def revoke_sessions():
        owner_reauth(); security.invalidate(); owner_session()
        return jsonify(ok=True)

    @app.post('/api/security/support')
    def support_create():
        owner_reauth()
        if not password:
            raise AppError('Set a standalone password before creating support access.')
        return jsonify(security.create_grant(request.json.get('role', 'view'), request.json.get('hours', 1)))

    @app.post('/api/security/support/<grant_id>/revoke')
    def support_revoke(grant_id):
        owner_reauth(); security.revoke_grant(grant_id)
        return jsonify(ok=True)

    @app.get('/api/shelf')
    def shelf():
        return jsonify(artists=store.artists(),albums=store.albums(owned=True))

    @app.get('/api/search')
    def search():
        query = request.args.get('q','').strip()[:160]
        kind = request.args.get('kind','artist')
        if kind not in {'artist','album'}:
            raise AppError('Search artists or albums.')
        return jsonify(results=musicbrainz.search(query,kind) if query else [])

    @app.get('/api/artists/<artist_id>')
    def artist(artist_id):
        artist_id = mbid(artist_id)
        detail = musicbrainz.get('artist/'+artist_id)
        if request.args.get('store') == '1':
            albums = musicbrainz.artist_albums(artist_id)
        else:
            albums = store.albums(artist_id,owned=True)
        return jsonify(artist=detail,albums=albums)

    @app.get('/api/artists/<artist_id>/catalogue')
    def artist_catalogue(artist_id):
        artist_id = mbid(artist_id)
        albums = musicbrainz.artist_albums(artist_id, manage=True)
        series_id = store.catalogue_series(artist_id)
        return jsonify(albums=albums, series_id=series_id,
                       series_snapshot=store.setting('series_snapshot:'+series_id) if series_id else None)

    @app.get('/api/artists/<artist_id>/catalogue-series')
    def catalogue_series_search(artist_id):
        return jsonify(musicbrainz.catalogue_candidates(mbid(artist_id), request.args.get('q')))

    @app.put('/api/artists/<artist_id>/series')
    def choose_series(artist_id):
        artist_id = mbid(artist_id)
        series_id = request.json.get('series_id')
        if series_id is not None:
            series_id = mbid(series_id)
            members = musicbrainz.series_members(series_id)
            known = store.albums(artist_id)
            if not known:
                known = musicbrainz.artist_albums(artist_id, manage=True)
            if not members.intersection(a['id'] for a in known):
                raise AppError('That series contains none of this artist’s known albums. Choose another catalogue.')
        store.set_setting('series:'+artist_id, series_id)
        return jsonify(ok=True)

    @app.post('/api/albums/<album_id>/catalogue')
    def catalogue_override(album_id):
        album_id = mbid(album_id)
        store.album(album_id)
        choice = request.json.get('choice')
        if choice not in {'include', 'exclude', 'auto'}:
            raise AppError('Choose Auto, Include or Exclude.')
        store.set_setting('catalogue:'+album_id, None if choice == 'auto' else choice)
        return jsonify(ok=True)

    @app.put('/api/albums/<album_id>/release-countries')
    def album_countries(album_id):
        album_id = mbid(album_id)
        store.album(album_id)
        countries = request.json.get('countries')
        if countries is not None:
            countries = validate_filters({'countries': countries, 'formats': ['cd']})['countries']
        store.set_setting('release_countries:'+album_id, countries)
        return jsonify(store.album(album_id))

    @app.get('/api/albums/<album_id>')
    def album(album_id):
        album_id = mbid(album_id)
        with store.catalogue_lock:
            try:
                store.album(album_id)
            except KeyError:
                musicbrainz.ensure_group(album_id)
            return jsonify(musicbrainz.ensure_tracks(album_id))

    @app.get('/api/albums/<album_id>/artwork')
    def album_artwork(album_id):
        data,mime,source = artwork.get(mbid(album_id))
        response = send_file(io.BytesIO(data),mimetype=mime,max_age=0)
        response.headers['X-Artwork-Source'] = source
        return response

    @app.post('/api/albums/<album_id>/artwork')
    def replace_artwork(album_id):
        artwork.upload(mbid(album_id),request.json.get('image',''))
        return jsonify(ok=True)

    @app.get('/api/albums/<album_id>/artwork-preview/<release_id>')
    def artwork_preview(album_id, release_id):
        store.album(mbid(album_id))
        data, mime, source = artwork.preview(mbid(release_id))
        response = send_file(io.BytesIO(data), mimetype=mime, max_age=0)
        response.headers['X-Artwork-Source'] = source
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.delete('/api/albums/<album_id>/artwork')
    def reset_artwork(album_id):
        artwork.reset(mbid(album_id))
        return jsonify(ok=True)

    @app.post('/api/albums/<album_id>/artwork-release')
    def edition_artwork(album_id):
        album_id, release_id = mbid(album_id), mbid(request.json.get('release_id'))
        release = musicbrainz.get('release/'+release_id, {'inc': 'release-groups+media'})
        if release.get('release-group', {}).get('id') != album_id or release.get('status') != 'Official':
            raise AppError('Choose an official edition of this album.')
        if not matches_filters(release, store.release_filters(album_id)):
            raise AppError('That cover edition does not match your release filters.')
        try:
            artwork.choose_release(album_id, release_id)
        except (AppError, OSError, requests.RequestException) as error:
            raise AppError('This edition has no downloadable front cover. Choose another or upload your own.', 502) from error
        return jsonify(ok=True)

    @app.post('/api/albums/<album_id>/shelf')
    def add_shelf(album_id):
        album_id = mbid(album_id)
        with store.catalogue_lock:
            musicbrainz.ensure_group(album_id)
            musicbrainz.ensure_tracks(album_id)
            store.shelf(album_id,True)
            return jsonify(store.album(album_id))

    @app.delete('/api/albums/<album_id>/shelf')
    def remove_shelf(album_id):
        album_id = mbid(album_id)
        with artwork.locks[hash(album_id)%len(artwork.locks)]:
            store.shelf(album_id,False)
            artwork.evict(album_id)
        return jsonify(ok=True)

    @app.get('/api/albums/<album_id>/releases')
    def releases(album_id):
        if 'offset' in request.args:
            try:
                offset = int(request.args['offset'])
                if offset < 0 or offset > 100000:
                    raise ValueError()
            except ValueError:
                raise AppError('Invalid release page.') from None
            return jsonify(musicbrainz.release_page(mbid(album_id), offset))
        return jsonify(releases=musicbrainz.releases(mbid(album_id)))

    @app.post('/api/albums/<album_id>/release')
    def change_release(album_id):
        if request.json.get('confirmed') is not True:
            raise AppError('Confirm the edition change. It replaces the canonical tracks and clears Spotify mappings.')
        with store.catalogue_lock:
            return jsonify(musicbrainz.use_release(mbid(album_id),request.json.get('release_id'),True))

    @app.get('/api/albums/<album_id>/diagnostics')
    def diagnostic_report(album_id):
        album_id = mbid(album_id)
        album = store.album(album_id)
        response = jsonify(format='audioshelf-diagnostics-1', build=build, generated_at=time.time(),
            album=album, global_release_filters=store.release_filters(), events=store.diagnostics(album_id),
            artwork=store.cache_get('artwork:'+album_id), has_custom_cover=bool(store.artwork_override(album_id)))
        response.headers['Content-Disposition'] = 'attachment; filename="audioshelf-'+album_id+'-diagnostics.json"'
        return response

    @app.post('/api/albums/<album_id>/review')
    def review(album_id):
        with store.connect() as db:
            db.execute('UPDATE albums SET canonical_reviewed=1 WHERE id=?',(mbid(album_id),))
        return jsonify(ok=True)

    @app.post('/api/albums/<album_id>/resolve')
    def resolve(album_id):
        with store.catalogue_lock:
            album = musicbrainz.ensure_tracks(mbid(album_id))
            candidates = spotify.candidates(album)
            if candidates:
                positions = {m['position'] for m in candidates[0]['mappings'] if not m['verified']}
                enriched = musicbrainz.enrich_recordings(album, positions)
                if enriched != album:
                    candidates = spotify.candidates(enriched)
            store.diagnostic(album_id, 'spotify_candidates', {'candidates': [
                c if n < 3 else {k: v for k, v in c.items() if k != 'mappings'} for n, c in enumerate(candidates[:30])]})
            if not candidates:
                raise AppError('No Spotify edition found. Paste a Spotify album link in album settings.')
            store.mapping(album_id,candidates[0])
            return jsonify(album=store.album(album_id),candidates=candidates)

    @app.post('/api/albums/<album_id>/mapping')
    def choose_mapping(album_id):
        with store.catalogue_lock:
            album = store.album(mbid(album_id))
            source = spotify.album(request.json.get('spotify_album_id',''))
            choice = candidate(album,source)
            positions = {m['position'] for m in choice['mappings'] if not m['verified']}
            enriched = musicbrainz.enrich_recordings(album, positions)
            if enriched != album:
                choice = candidate(enriched,source)
            store.diagnostic(album_id, 'spotify_assessment', choice)
            store.mapping(album_id,choice,manual=True)
            return jsonify(album=store.album(album_id),candidate=choice)

    @app.get('/api/spotify/track')
    def preview_track():
        track = spotify.api('GET','tracks/'+spotify_id(request.args.get('id',''),'track'),{'market':spotify.market})
        return jsonify(id=track['id'],name=track['name'],artists=track.get('artists',[]),
                       duration_ms=track.get('duration_ms'),album=track.get('album',{}),is_playable=track.get('is_playable',True))

    @app.post('/api/albums/<album_id>/tracks/<int:position>')
    def correct_track(album_id,position):
        if request.json.get('confirmed') is not True:
            raise AppError('Preview and confirm the recording first.')
        with store.catalogue_lock:
            album = store.album(mbid(album_id))
            canonical = next((t for t in album['tracks'] if t['position'] == position),None)
            if canonical is None:
                raise AppError('That canonical track does not exist.',404)
            track = spotify.api('GET','tracks/'+spotify_id(request.json.get('spotify_track_id',''),'track'),{'market':spotify.market})
            if track.get('is_playable') is False or track.get('restrictions'):
                raise AppError('That recording is unavailable for this account.')
            score, _ = track_score(canonical,track,[a['name'] for a in album['artists']])
            with store.connect() as db:
                db.execute("UPDATE tracks SET spotify_id=?,spotify_album_id=?,score=?,method='manual',verified=1 WHERE album_id=? AND position=?",
                    (track['id'],track.get('album',{}).get('id'),score,album_id,position))
            return jsonify(store.album(album_id))

    @app.post('/api/albums/<album_id>/play')
    def play(album_id):
        if not store.setting('preferred_device'):
            raise AppError('Choose your playback device before starting music.', 409)
        body = request.get_json(silent=True) or {}
        album = store.album(mbid(album_id))
        result = spotify.play(album, disc_number=body.get('disc_number'))
        return jsonify(result)

    @app.get('/api/spotify/playback')
    def playback():
        if not spotify.connected:
            return jsonify(active=False)
        state = spotify.api('GET', 'me/player')
        track = state.get('item') or {}
        if not track or state.get('currently_playing_type') not in (None, 'track'):
            return jsonify(active=False)
        identifiers = [track.get('id'), (track.get('linked_from') or {}).get('id')]
        with store.connect() as db:
            matches = [row[0] for row in db.execute(
                'SELECT DISTINCT tracks.album_id FROM tracks JOIN shelf ON shelf.album_id=tracks.album_id '
                'WHERE verified=1 AND spotify_id IN (?,?)', identifiers)]
        previous = store.setting('last_played_album')
        album_id = previous if previous in matches else matches[0] if len(matches) == 1 else None
        album = store.album(album_id) if album_id else None
        return jsonify(active=True, playing=bool(state.get('is_playing')), track=track.get('name', ''),
                       artist=', '.join(a.get('name', '') for a in track.get('artists', [])),
                       album=album['title'] if album else (track.get('album') or {}).get('name', ''),
                       album_id=album_id, device=(state.get('device') or {}).get('name', 'Spotify'),
                       track_ids=[value for value in identifiers if value], progress_ms=state.get('progress_ms'),
                       duration_ms=track.get('duration_ms'))

    @app.get('/api/spotify/devices')
    def devices():
        return jsonify(devices=spotify.devices())

    @app.post('/api/spotify/connect')
    def connect():
        return jsonify(url=spotify.authorization())

    @app.post('/api/spotify/disconnect')
    def disconnect():
        spotify.disconnect()
        return jsonify(ok=True)

    @app.get('/auth/spotify/callback')
    def callback():
        try:
            spotify.callback(request.args.get('code',''),request.args.get('state',''),bool(request.args.get('error')))
            message = 'Spotify is connected. Return to AudioShelf and play an album.'
            ok = True
        except AppError as error:
            message,ok = str(error),False
        return render_template('callback.html',message=message,ok=ok), 200 if ok else 400

    @app.post('/api/backup')
    def backup():
        path = store.backup()
        return send_file(path,as_attachment=True,download_name=path.name)

    @app.get('/api/export')
    def export():
        albums = [store.album(a['id']) for a in store.albums(owned=True)]
        response = jsonify(format='audioshelf-1',albums=albums)
        response.headers['Content-Disposition'] = 'attachment; filename="audioshelf-collection.json"'
        return response

    return app
