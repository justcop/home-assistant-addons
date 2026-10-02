import hashlib
import json
import logging
import os
import secrets
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException

from .errors import AppError
from .matching import candidate, track_score
from .musicbrainz import MusicBrainz, mbid
from .spotify import Spotify, atomic_private_json, spotify_id
from .storage import Store

LOG = logging.getLogger('audioshelf')


def load_options():
    path = Path(os.environ.get('AUDIOSHELF_OPTIONS','/data/options.json'))
    options = json.loads(path.read_text()) if path.exists() else {}
    for key in ('data_directory','spotify_client_id','spotify_redirect_uri','spotify_market','web_password'):
        value = os.environ.get('AUDIOSHELF_'+key.upper())
        if value is not None:
            options[key] = value
    options.setdefault('data_directory','/share/audioshelf')
    return options


def create_app(options=None):
    options = options if options is not None else load_options()
    store = Store(options.get('data_directory','/share/audioshelf'))
    private_dir = Path(options.get('private_directory') or os.environ.get('AUDIOSHELF_PRIVATE_DIRECTORY','/data/audioshelf-private'))
    private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret_path = private_dir/'session.json'
    if not secret_path.exists():
        atomic_private_json(secret_path, {'key':secrets.token_hex(32)})
    app = Flask(__name__)
    app.config.update(SECRET_KEY=json.loads(secret_path.read_text())['key'], MAX_CONTENT_LENGTH=64*1024,
                      SESSION_COOKIE_NAME='audioshelf_session', SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', PERMANENT_SESSION_LIFETIME=timedelta(days=30))
    musicbrainz, spotify = MusicBrainz(store), Spotify(store, options, private_dir)
    app.extensions.update(store=store, musicbrainz=musicbrainz, spotify=spotify)
    build_path = Path(__file__).resolve().parents[1]/'build.json'
    build = json.loads(build_path.read_text()) if build_path.exists() else {'version':'0.1.0','channel':'local','revision':'local'}
    build['version'] = os.environ.get('AUDIOSHELF_VERSION',build['version'])
    password = options.get('web_password','')
    fingerprint = hashlib.sha256((app.config['SECRET_KEY']+password).encode()).hexdigest()
    login_attempts = {}

    def ingress():
        # The ingress header alone cannot bypass the standalone password.
        return request.remote_addr == '172.30.32.2' and bool(request.headers.get('X-Ingress-Path'))

    def authenticated():
        return not password or ingress() or secrets.compare_digest(session.get('auth',''), fingerprint)

    @app.before_request
    def protect():
        if request.method in {'POST','PUT','PATCH','DELETE'}:
            if request.headers.get('X-AudioShelf-Request') != '1' or not request.is_json:
                raise AppError('Please perform this action from AudioShelf.', 403)
        if request.path.startswith('/api/') and request.path not in {'/api/status','/api/login'} and not authenticated():
            raise AppError('Enter your AudioShelf password.', 401)

    @app.after_request
    def headers(response):
        if request.path.startswith(('/api/','/auth/')) or request.path == '/':
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' https://coverartarchive.org https://*.archive.org https://*.scdn.co data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'"
        return response

    @app.errorhandler(AppError)
    def expected(error):
        return jsonify(error=str(error)), error.status

    @app.errorhandler(KeyError)
    def missing(error):
        return jsonify(error='That item could not be found.'), 404

    @app.errorhandler(Exception)
    def unexpected(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        LOG.exception('AudioShelf request failed')
        return jsonify(error='AudioShelf could not complete this request. Check the add-on log.'), 500

    @app.get('/')
    def index():
        prefix = request.headers.get('X-Ingress-Path','') if ingress() else ''
        if not prefix.startswith('/') or any(c in prefix for c in ['<','>','"',"'",'\\']) or prefix.startswith('//'):
            prefix = ''
        return render_template('index.html', base=prefix.rstrip('/')+'/')

    @app.get('/health')
    def health():
        with store.connect() as db:
            db.execute('SELECT 1').fetchone()
        return jsonify(ok=True, version=build['version'])

    @app.get('/sw.js')
    def service_worker():
        response = app.send_static_file('sw.js')
        response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.get('/api/status')
    def status():
        result = {'build':build,'authenticated':authenticated(),'password_required':bool(password) and not ingress()}
        if authenticated():
            result.update(spotify_configured=spotify.configured, spotify_connected=spotify.connected,
                          spotify_redirect_uri=spotify.redirect_uri, data_directory=str(store.directory), market=spotify.market)
        return jsonify(result)

    @app.post('/api/login')
    def login():
        address = request.remote_addr
        now = time.monotonic()
        for key in list(login_attempts):
            login_attempts[key] = [t for t in login_attempts[key] if now-t<300]
            if not login_attempts[key]:
                del login_attempts[key]
        attempts = login_attempts.setdefault(address, [])
        if len(attempts) >= 10:
            raise AppError('Too many password attempts. Try again in five minutes.',429)
        if password and not secrets.compare_digest(str(request.json.get('password','')).encode(),password.encode()):
            attempts.append(now)
            raise AppError('Incorrect AudioShelf password.',401)
        session['auth'] = fingerprint
        session.permanent = True
        login_attempts.pop(address,None)
        return jsonify(ok=True)

    @app.post('/api/logout')
    def logout():
        session.clear()
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

    @app.get('/api/albums/<album_id>')
    def album(album_id):
        album_id = mbid(album_id)
        with store.catalogue_lock:
            try:
                store.album(album_id)
            except KeyError:
                musicbrainz.ensure_group(album_id)
            return jsonify(musicbrainz.ensure_tracks(album_id))

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
        store.shelf(mbid(album_id),False)
        return jsonify(ok=True)

    @app.get('/api/albums/<album_id>/releases')
    def releases(album_id):
        return jsonify(releases=musicbrainz.releases(mbid(album_id)))

    @app.post('/api/albums/<album_id>/release')
    def change_release(album_id):
        if request.json.get('confirmed') is not True:
            raise AppError('Confirm the edition change. It replaces the canonical tracks and clears Spotify mappings.')
        with store.catalogue_lock:
            return jsonify(musicbrainz.use_release(mbid(album_id),request.json.get('release_id'),True))

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
        return jsonify(spotify.play(store.album(mbid(album_id))))

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
