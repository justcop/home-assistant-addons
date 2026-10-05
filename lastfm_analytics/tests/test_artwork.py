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
        assert timeout == 5
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
