from app.listening_links import shelf_matches
from conftest import ALBUM, ARTIST


def test_only_owned_records_and_song_album_links(application):
    store = application.extensions['store']
    assert shelf_matches(store, 'album', 'The Artist', 'The Album', []) == []
    store.shelf(ALBUM, True)
    assert shelf_matches(store, 'artist', 'THE ARTIST', '', []) == [dict(label='The Artist', path='#shelf/'+ARTIST)]
    assert shelf_matches(store, 'album', 'The Artist', 'The Album (2011 Remastered)', []) == [dict(label='The Album', path='#album/'+ALBUM)]
    assert shelf_matches(store, 'song', 'The Artist', 'Opening', ['The Album'])[0]['path'] == '#album/'+ALBUM
    assert shelf_matches(store, 'song', 'Another Artist', 'Opening', []) == []
    assert shelf_matches(store, 'song', 'The Artist', 'Opening (Live)', []) == []


def test_authenticated_endpoint_and_no_cors_by_default(client):
    result = client.get('/api/listening-links?kind=artist&artist=The+Artist', headers={'Origin':'https://analytics.example'})
    assert result.status_code == 200
    assert result.json['matches'] == []
    assert 'Access-Control-Allow-Origin' not in result.headers
    assert client.get('/api/listening-links?kind=unknown').status_code == 400


def test_cors_limited_to_configured_origin(tmp_path):
    from app.server import create_app
    app = create_app(dict(data_directory=str(tmp_path/'shelf'), private_directory=str(tmp_path/'private'),
        listening_analytics_origin='https://analytics.example'))
    client = app.test_client()
    url = '/api/listening-links?kind=artist&artist=The+Artist'
    result = client.get(url, headers={'Origin':'https://analytics.example'})
    assert result.status_code == 401
    assert result.headers['Access-Control-Allow-Origin'] == 'https://analytics.example'
    assert result.headers['Access-Control-Allow-Credentials'] == 'true'
    assert 'Origin' in result.headers['Vary']
    assert 'Access-Control-Allow-Origin' not in client.get(url, headers={'Origin':'https://evil.example'}).headers
    assert 'Access-Control-Allow-Origin' not in client.get('/api/shelf', headers={'Origin':'https://analytics.example'}).headers
