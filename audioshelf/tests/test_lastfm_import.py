"""Last.fm history is explicit opt-in, account-bound and safe for bulk selection."""
import time
from unittest.mock import Mock

import pytest

from app.errors import AppError
from app.lastfm_import import LastFmImporter, album_key, album_title_variants
from conftest import ALBUM, ARTIST, post


def lastfm_transport(monkeypatch):
    calls = []

    def get(url, params, **kwargs):
        assert url == 'https://ws.audioscrobbler.com/2.0/'
        assert kwargs['allow_redirects'] is False
        calls.append(dict(params))
        if params['method'] == 'user.getinfo':
            return Mock(ok=True, status_code=200,
                        json=lambda: {'user': {'name': params['user']}})
        if params['method'] == 'user.gettopalbums':
            rows = [
                {'name': 'The Album', 'artist': {'name': 'The Artist'},
                 'playcount': '150', 'mbid': ALBUM},
                {'name': 'Unknown Album', 'artist': {'name': 'Unknown Artist'},
                 'playcount': '20', 'mbid': ''},
                {'name': 'The Album', 'artist': {'name': 'The Artist'},
                 'playcount': '42', 'mbid': ALBUM}]
            return Mock(ok=True, status_code=200, json=lambda:
                        {'topalbums': {'album': rows, '@attr': {'totalPages': '1'}}})
        raise AssertionError('Unexpected external method')

    monkeypatch.setattr('app.lastfm_import.requests.get', get)
    return calls


def test_lastfm_opt_in_credentials_public_history_and_exact_selection(application, client, monkeypatch):
    calls = lastfm_transport(monkeypatch)
    status = client.get('/api/lastfm/status').json
    assert status['connected'] is False
    assert post(client, '/api/lastfm/connect', {'username': 'Alice',
             'api_key': 'bad'}).status_code == 400
    assert not calls
    config = post(client, '/api/lastfm/connect', {'username': 'Alice',
                  'api_key': 'A' * 32})
    assert config.status_code == 200
    assert config.json['username'] == 'Alice'
    assert 'api_key' not in str(config.json)
    assert client.get('/api/lastfm/status').json['connected'] is True
    result = client.get('/api/lastfm/albums?period=overall').json
    assert result['total'] == 2  # Last.fm versions/scrobbles collapse by album identity
    assert result['albums'][0]['plays'] == 150
    assert result['albums'][0]['on_shelf'] is False
    assert client.get('/api/lastfm/albums?period=2030').status_code == 400
    assert post(client, '/api/lastfm/import', {'period': 'overall',
                'ids': ['malicious']}).status_code == 409
    assert post(client, '/api/lastfm/import', {'period': 'overall',
                'ids': ['malicious'] * 251}).status_code == 400
    assert all(call['api_key'] == 'a' * 32 for call in calls)


def test_import_adds_existing_group_without_duplicates_and_reports_skips(application, client, monkeypatch):
    lastfm_transport(monkeypatch)
    assert post(client, '/api/lastfm/connect', {'username': 'Alice',
                    'api_key': 'b' * 32}).status_code == 200
    musicbrainz = application.extensions['musicbrainz']
    seen = []

    def ensure_group(group_id):
        seen.append(group_id)
        assert group_id == ALBUM
        return musicbrainz.store.album(group_id)

    monkeypatch.setattr(musicbrainz, 'ensure_group', ensure_group)
    monkeypatch.setattr(musicbrainz, 'ensure_tracks',
                        lambda group_id: musicbrainz.store.album(group_id))
    monkeypatch.setattr(musicbrainz, 'search', lambda query, kind, album_artist=None: [])
    albums = client.get('/api/lastfm/albums').json['albums']
    selected = [a['id'] for a in albums]
    result = post(client, '/api/lastfm/import', {'ids': selected})
    assert result.status_code == 202
    account = result.json['job']
    assert account['total'] == 2

    for _ in range(150):
        state = client.get('/api/lastfm/status').json['job']
        if state['state'] != 'running':
            break
        time.sleep(.01)
    assert state['state'] == 'complete'
    assert state['processed'] == 2
    assert state['added'] == 1
    assert state['failed'] == 1
    assert 'Unknown Album' in str(state['issues'])
    assert seen == [ALBUM]
    own = client.get('/api/shelf').json['albums']
    assert len(own) == 1 and own[0]['id'] == ALBUM
    # Re-opening the chooser marks this album as already collected.
    again = client.get('/api/lastfm/albums').json['albums']
    assert again[0]['on_shelf'] is True
    assert post(client, '/api/lastfm/import', {'ids': [again[0]['id']]}).status_code == 409
    assert post(client, '/api/lastfm/disconnect').status_code == 200
    assert client.get('/api/lastfm/status').json['connected'] is False
    assert client.get('/api/shelf').json['albums'][0]['id'] == ALBUM


def test_lastfm_exact_match_rejects_ambiguity_and_wrong_artist():
    importer = LastFmImporter()
    one = dict(id=ALBUM, title='The Album',
               artists=[{'id': ARTIST, 'name': 'The Artist'}], on_shelf=False)
    two = dict(one, id='ad8af025-cb51-457b-8d36-c54d018e3f11')
    client = Mock()
    client.search.return_value = [one, two]
    item = {'artist': 'The Artist', 'title': 'The Album', 'mbid': ''}
    with pytest.raises(AppError, match='Several'):
        importer._match_group(client, item)
    client.search.return_value = [dict(one, artists=[{'name': 'Another Artist'}])]
    with pytest.raises(AppError, match='No matching'):
        importer._match_group(client, item)
    client.search.return_value = [one]
    assert importer._match_group(client, item)['id'] == ALBUM


def test_lastfm_import_separated_per_collection(application, tmp_path, monkeypatch):
    from app.storage import Store
    first = application.extensions['store']
    second = Store(tmp_path / 'other', tmp_path / 'other-cache')
    caller = LastFmImporter()
    first.set_setting('lastfm_account', {'username': 'Alice', 'api_key': 'a'*32})
    assert caller.status(first, 'owner')['connected']
    assert caller.status(second, 'another')['connected'] is False
    assert second.setting('lastfm_account') is None
    assert caller.status(first, 'owner')['username'] == 'Alice'
    assert 'api_key' not in str(caller.status(first, 'owner'))



def test_lastfm_edition_and_artist_aliases():
    assert album_title_variants('Revolver (Super Deluxe)', 'The Beatles')[0] == 'Revolver'
    assert album_title_variants('Concrete Love (Deluxe Version)', 'The Courteeners')[0] == 'Concrete Love'
    assert album_title_variants('St. Jude (15th Anniversary Edition)', 'Courteeners')[0] == 'St. Jude'
    assert album_title_variants('Physical Graffiti (disc 1)', 'Led Zeppelin')[0] == 'Physical Graffiti'
    assert album_title_variants('All Things Must Pass CD1', 'George Harrison')[0] == 'All Things Must Pass'
    assert album_title_variants('Tourist History [Promo]', 'Two Door Cinema Club')[0] == 'Tourist History'
    assert album_title_variants('The Beatles (The White Album)', 'The Beatles')[0] == 'The Beatles'
    assert album_title_variants('Led Zeppelin I', 'Led Zeppelin')[0] == 'Led Zeppelin'
    # Do not erase a distinct artistic subtitle or turn a remix into its parent.
    assert album_title_variants('St. Jude Re:Wired', 'Courteeners') == ['St. Jude Re:Wired']
    assert album_title_variants('Let It Be... Naked', 'The Beatles') == ['Let It Be... Naked']


def test_lastfm_looks_up_edition_by_artist_instead_of_title_only():
    from app.musicbrainz import MusicBrainz
    importer = LastFmImporter()
    group = {'id': ALBUM, 'title': 'Revolver',
             'artists': [{'name': 'The Beatles'}], 'on_shelf': False}
    calls = []

    class FakeMusicBrainz:
        def search(self, title, kind, album_artist=None):
            calls.append((title, kind, album_artist))
            return [group] if title == 'Revolver' and album_artist == 'The Beatles' else []

    result = importer._match_group(FakeMusicBrainz(),
                  {'artist': 'The Beatles', 'title': 'Revolver (Super Deluxe)', 'mbid': ''})
    assert result['id'] == ALBUM
    assert calls == [('Revolver', 'album', 'The Beatles')]

    captured = []
    mb = MusicBrainz(Mock())
    mb.get = lambda entity, params: (captured.append((entity, params)) or {'release-groups': []})
    mb.search('Babel', 'album', album_artist='Mumford & Sons')
    assert 'artist:' in captured[0][1]['query']
    assert captured[0][1]['limit'] == 100


def test_lastfm_self_titled_album_remains_ambiguous():
    one = {'id': ALBUM, 'title': 'Weezer',
           'artists': [{'name': 'Weezer'}], 'on_shelf': False}
    two = dict(one, id='ad8af025-cb51-457b-8d36-c54d018e3f11')
    mb = Mock()
    mb.search.return_value = [one, two]
    with pytest.raises(AppError, match='Several'):
        LastFmImporter._match_group(mb, {'artist': 'Weezer', 'title': 'Weezer', 'mbid': ''})
