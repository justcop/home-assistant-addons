import io
import json
import threading
from urllib.parse import parse_qs, urlsplit

from analytics.artwork import ArtworkWorker
from analytics.db import Database
from analytics.insights import details
from analytics.web import create_app
from test_core import play

URL = 'https://lastfm.freetls.fastly.net/i/u/174s/cover.jpg'
ALBUMS = [dict(artist='The Beatles', album='Abbey Road')]


def response(artist='The Beatles', name='Abbey Road', url=URL):
    return io.BytesIO(json.dumps({'album': dict(artist=artist, name=name,
        image=[{'size': 'large', '#text': url}])}).encode())


def deezer_response(url='https://e-cdns-images.dzcdn.net/images/cover/fixture/250x250.jpg'):
    return io.BytesIO(json.dumps({'data': [{
        'title': 'Abbey Road',
        'artist': {'name': 'The Beatles'},
        'cover_medium': url,
    }]}).encode())


def test_cover_uses_older_scrobbles_and_less_played_albums(tmp_path):
    db = Database(tmp_path / 'history.sqlite3')
    rows = [play(i) for i in range(100, 130)]
    rows[0]['raw']['image'] = [{'size': 'large', '#text': URL}]
    db.apply_window(0, 500, rows)
    with db.connect() as conn:
        assert details(conn, 'artist', 'the beatles', False)['artwork']['url'] == URL
        conn.execute("UPDATE scrobbles SET raw_json='{}'")
    for i, (album, count) in enumerate([('A', 9), ('B', 8), ('C', 7), ('D', 2)]):
        rows = [play(1000 + i * 100 + j, album=album) for j in range(count)]
        if album == 'D':
            rows[0]['raw']['image'] = [{'size': 'large', '#text': URL}]
        db.apply_window(1000+i*100, 1100+i*100, rows)
    with db.connect() as conn:
        assert details(conn, 'artist', 'the beatles', False)['artwork']['album'] == 'D'


def test_background_lookup_is_deduplicated_and_persists_url_only(tmp_path):
    db = Database(tmp_path / 'history.sqlite3')
    started, release = threading.Event(), threading.Event()
    calls = []
    def opener(request, timeout):
        calls.append(parse_qs(urlsplit(request.full_url).query))
        assert timeout == 10
        started.set()
        assert release.wait(3)
        return response()
    worker = ArtworkWorker('fixture-key', opener=opener)
    try:
        assert worker.resolve(db, ALBUMS)['artwork_pending']
        assert started.wait(2)
        for _ in range(8):
            assert worker.resolve(db, ALBUMS)['artwork_pending']
        assert len(calls) == 1
        release.set()
        worker.jobs.join()
        result = worker.resolve(db, ALBUMS)
        assert result['artwork']['url'] == URL
        assert not result['artwork_pending']
        assert calls[0]['method'] == ['album.getInfo']
        assert calls[0]['artist'] == ['The Beatles']
        assert calls[0]['album'] == ['Abbey Road']
        with db.connect() as conn:
            assert conn.execute('SELECT COUNT(*) FROM artwork_urls').fetchone()[0] == 1
        reopened = ArtworkWorker('fixture-key', enabled=False)
        assert reopened.resolve(Database(db.path), ALBUMS) == result
        assert len(calls) == 1
    finally:
        release.set()
        worker.close()


def test_deezer_fallback_is_cached_and_labelled(tmp_path):
    db = Database(tmp_path / 'history.sqlite3')
    calls = []
    def opener(request, timeout):
        calls.append(request.full_url)
        if 'audioscrobbler.com' in request.full_url:
            return response(url='')
        return deezer_response()
    worker = ArtworkWorker('key', opener=opener)
    try:
        assert worker.resolve(db, ALBUMS)['artwork_pending']
        worker.jobs.join()
        result = worker.resolve(db, ALBUMS)
        assert result['artwork']['source'] == 'Deezer'
        assert result['artwork']['url'].startswith('https://e-cdns-images.dzcdn.net/')
        assert any('api.deezer.com/search/album' in url for url in calls)
        assert not result['artwork_pending']
    finally:
        worker.close()


def test_negative_cache_failure_and_untrusted_images(tmp_path):
    db = Database(tmp_path / 'history.sqlite3')
    for opener in [lambda *_args, **_kw: response(artist='Oasis'),
                   lambda *_args, **_kw: response(url='https://evil.example/image.jpg'),
                   lambda *_args, **_kw: io.BytesIO(b'invalid json')]:
        with db.connect() as conn:
            conn.execute('DELETE FROM artwork_urls')
        worker = ArtworkWorker('key', opener=opener)
        try:
            assert worker.resolve(db, ALBUMS)['artwork_pending']
            worker.jobs.join()
            assert worker.resolve(db, ALBUMS) == dict(artwork=None, artwork_pending=False)
            assert worker.jobs.empty()
        finally:
            worker.close()


def test_detail_endpoint_cached_fallback_and_scoped_lists(tmp_path):
    app = create_app(tmp_path, config={'username': 'user', 'api_key': 'fixture'},
                     development=True, start_worker=False)
    db = app.extensions['database']
    db.apply_window(0, 200, [play(100), play(101, title='Something'),
                            play(102, artist='Oasis', album='Definitely Maybe')])
    worker = app.extensions['artwork_worker']
    worker.enabled = True
    worker.opener = lambda *_args, **_kw: response()
    client = app.test_client()
    query = '?entity=artist&id=the+beatles&period=all'
    try:
        initial = client.get('/api/detail' + query).json
        assert initial['name'] == 'The Beatles'
        assert initial['listening_albums'] == ['Abbey Road']
        worker.jobs.join()
        assert client.get('/api/artwork' + query).json['artwork']['url'] == URL
        assert client.get('/api/detail' + query).json['artwork']['url'] == URL
        albums = client.get('/api/rankings' + query + '&kind=album').json['rows']
        songs = client.get('/api/rankings' + query + '&kind=song').json['rows']
        history = client.get('/api/history' + query).json
        assert [r['name'] for r in albums] == ['Abbey Road']
        assert {r['name'] for r in songs} == {'Come Together', 'Something'}
        assert history['total'] == 2
        assert {r['artist'] for r in history['rows']} == {'The Beatles'}
        assert client.get('/api/artwork' + query + '&source=vinyl').json['artwork'] is None
    finally:
        worker.close()
        app.extensions['view_cache'].close()


def test_artist_photo_logo_cache_and_identity(tmp_path):
    photo = 'https://r2.theaudiodb.com/images/media/artist/thumb/beatles.jpg'
    logo = 'https://r2.theaudiodb.com/images/media/artist/logo/beatles.png'
    calls = []
    def opener(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(json.dumps({'artists': [dict(strArtist='The Beatles',
            strArtistThumb=photo, strArtistLogo=logo)]}).encode())
    db = Database(tmp_path / 'artist.sqlite3')
    worker = ArtworkWorker('', opener=opener)
    try:
        assert worker.resolve_artist(db, 'The Beatles')['artwork_pending']
        worker.jobs.join()
        result = worker.resolve_artist(db, 'The Beatles')
        assert result['artist_photo'] == photo
        assert result['artist_logo'] == logo
        assert not result['artwork_pending']
        assert len(calls) == 1
        assert worker.fetch_artist('Oasis') is None
        worker.opener = lambda *_a, **_k: io.BytesIO(json.dumps({'artists': [dict(
            strArtist='The Beatles', strArtistThumb='https://evil.example/photo',
            strArtistLogo=logo)]}).encode())
        assert json.loads(worker.fetch_artist('The Beatles')) == {'logo': logo}
        reopened = ArtworkWorker('', enabled=False)
        assert reopened.resolve_artist(Database(db.path), 'The Beatles') == result
    finally:
        worker.close()


def test_audiodb_cdn_and_edition_album_fallback(tmp_path):
    url = 'https://r2.theaudiodb.com/images/media/album/thumb/abbey.jpg'
    calls = []
    def opener(request, timeout):
        calls.append(request.full_url)
        if 'audioscrobbler.com' in request.full_url:
            return response(url='')
        return io.BytesIO(json.dumps({'album': [dict(strArtist='The Beatles',
            strAlbum='Abbey Road', strAlbumThumb=url)]}).encode())
    worker = ArtworkWorker('key', opener=opener)
    assert worker.fetch('The Beatles', 'Abbey Road (2009 Remaster)') == url
    assert parse_qs(urlsplit(calls[-1]).query)['a'] == ['Abbey Road']
    assert worker.fetch_audiodb_album('Oasis', 'Abbey Road') is None
    assert worker.fetch_audiodb_album('The Beatles', 'Abbey Road (Live)') is None
    from analytics.artwork import artwork_source, audiodb_image
    assert artwork_source(url) == 'TheAudioDB'
    assert audiodb_image(url.replace('https:', 'http:'), 'album') == url
    assert audiodb_image(url.replace('r2.theaudiodb.com', 'r2.theaudiodb.com.evil.test'), 'album') is None
    assert audiodb_image(url, 'artist') is None


def test_old_failed_cache_is_expired_once_but_urls_survive(tmp_path):
    import sqlite3
    import time
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE artwork_urls (artist_key TEXT,album_key TEXT,url TEXT,expires INTEGER,PRIMARY KEY(artist_key,album_key))')
        conn.executemany('INSERT INTO artwork_urls VALUES (?,?,?,?)', [
            ('the beatles', 'abbey road', None, time.time()+86400),
            ('oasis', 'definitely maybe', URL, time.time()+86400)])
    db = Database(path)
    with db.connect() as conn:
        assert conn.execute('SELECT artist_key FROM artwork_urls').fetchall()[0][0] == 'oasis'
        conn.execute('INSERT INTO artwork_urls VALUES (?,?,?,?)', ('radiohead', 'ok computer', None, 9999999999))
    Database(path)
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM artwork_urls').fetchone()[0] == 2


def test_provider_outage_is_short_lived(tmp_path):
    import time
    db = Database(tmp_path / 'errors.sqlite3')
    worker = ArtworkWorker('', opener=lambda *_a, **_k: io.BytesIO(b'<html>Site Unavailable</html>'))
    try:
        worker.resolve(db, ALBUMS)
        worker.jobs.join()
        with db.connect() as conn:
            expires = conn.execute('SELECT expires FROM artwork_urls').fetchone()[0]
        assert 290 < expires-time.time() <= 300
    finally:
        worker.close()
