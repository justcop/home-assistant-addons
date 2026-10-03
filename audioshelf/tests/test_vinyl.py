from app.storage import Store
from conftest import ALBUM, ARTIST, post


def test_interface_persists_and_invalid_choice_is_atomic(application, client):
    store = application.extensions['store']
    assert client.get('/api/settings').json['interface'] == 'vinyl'
    original = store.album(ALBUM)
    assert post(client, '/api/settings', {'interface': 'classic'}, method='PUT').json['interface'] == 'classic'
    assert client.get('/api/status').json['interface'] == 'classic'
    assert Store(store.directory, store.cache_directory).setting('interface') == 'classic'
    assert post(client, '/api/settings', {'interface': 'unknown', 'theme': 'midnight'}, method='PUT').status_code == 400
    assert store.setting('theme', 'record-store') == 'record-store'
    assert store.album(ALBUM) == original


def test_playback_tracks_spotify_pause_and_relinked_library_track(application, client, monkeypatch):
    spotify = application.extensions['spotify']
    store = application.extensions['store']
    assert client.get('/api/spotify/playback').json == {'active': False}
    spotify._save({'access_token': 'test', 'refresh_token': 'test', 'expires_in': 3600})
    store.shelf(ALBUM, True)
    with store.connect() as db:
        db.execute('UPDATE tracks SET spotify_id=?,verified=1 WHERE album_id=? AND position=1', ('a'*22, ALBUM))
    state = {'is_playing': True, 'item': {'id': 'relinked', 'linked_from': {'id': 'a'*22}, 'name': 'Opening',
             'artists': [{'name': 'The Artist'}], 'album': {'name': 'A Different Spotify Edition'}}, 'device': {'name': 'Kitchen'}}
    monkeypatch.setattr(spotify, 'api', lambda *args: state)
    result = client.get('/api/spotify/playback').json
    assert result['album_id'] == ALBUM and result['album'] == 'The Album'
    assert result['playing'] and result['device'] == 'Kitchen'
    state['is_playing'] = False
    assert client.get('/api/spotify/playback').json['playing'] is False
    state.clear()
    assert client.get('/api/spotify/playback').json == {'active': False}


def test_playback_does_not_misidentify_shared_or_uncollected_tracks(application, client, monkeypatch):
    store, spotify = application.extensions['store'], application.extensions['spotify']
    other = '11111111-1111-4111-8111-111111111111'
    store.catalogue([{'id': other, 'title': 'Other album', 'artist-credit': [{'artist': {'id': ARTIST, 'name': 'The Artist'}}]}])
    store.set_tracks(other, {'id': other}, [{'title': 'Opening', 'disc_number': 1, 'track_number': 1}])
    for album_id in (ALBUM, other):
        store.shelf(album_id, True)
    with store.connect() as db:
        db.execute('UPDATE tracks SET spotify_id=?,verified=1 WHERE position=1', ('a'*22,))
    spotify._save({'access_token': 'test', 'refresh_token': 'test', 'expires_in': 3600})
    state = {'is_playing': True, 'item': {'id': 'a'*22, 'name': 'Opening', 'album': {'name': 'Spotify album'}}}
    monkeypatch.setattr(spotify, 'api', lambda *args: state)
    assert client.get('/api/spotify/playback').json['album_id'] is None
    store.set_setting('last_played_album', other)
    assert client.get('/api/spotify/playback').json['album_id'] == other
    for album_id in (ALBUM, other):
        store.shelf(album_id, False)
    assert client.get('/api/spotify/playback').json['album_id'] is None
    state['currently_playing_type'] = 'episode'
    assert client.get('/api/spotify/playback').json == {'active': False}
