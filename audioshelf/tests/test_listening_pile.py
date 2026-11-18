"""Ownership and loan-state invariants for the Listening Room."""
from app.storage import Store
from conftest import ALBUM, post


def test_borrowed_album_is_only_in_listening_pile(application):
    store = application.extensions['store']
    store.pile(ALBUM, True)
    assert not store.albums(owned=True)
    assert store.artists() == []
    records = store.albums(pile=True)
    assert len(records) == 1
    assert records[0]['on_loan'] and not records[0]['on_shelf']
    assert Store(store.directory).album(ALBUM)['on_loan']


def test_owned_record_can_remain_in_both_places(application):
    store = application.extensions['store']
    store.shelf(ALBUM, True)
    store.pile(ALBUM, True)
    album = Store(store.directory).album(ALBUM)
    assert album['on_shelf'] and album['in_listening_pile']
    assert not album['on_loan']
    assert len(store.albums(owned=True)) == len(store.albums(pile=True)) == 1
    store.pile(ALBUM, False)
    assert not store.album(ALBUM)['in_listening_pile']
    assert store.album(ALBUM)['on_shelf']


def test_return_loan_keeps_catalogue_and_tracklist(application):
    store = application.extensions['store']
    store.pile(ALBUM, True)
    store.pile(ALBUM, False)
    assert store.albums(pile=True) == []
    assert store.album(ALBUM)['on_loan'] is False
    assert len(store.album(ALBUM)['tracks']) == 2


def test_pile_routes_do_not_treat_loans_as_owned(client, application):
    store = application.extensions['store']
    assert post(client, f'/api/albums/{ALBUM}/listening-pile').status_code == 409
    assert client.get('/api/listening-pile').json['albums'] == []
    store.pile(ALBUM, True)
    result = client.get('/api/listening-pile').json
    assert len(result['albums']) == 1
    assert result['albums'][0]['on_loan']
    removed = post(client, f'/api/albums/{ALBUM}/listening-pile', method='DELETE')
    assert removed.status_code == 200
    assert not removed.json['on_loan']
    assert client.get('/api/shelf').json['albums'] == []


def test_owned_pile_routes_leave_collection_unchanged(client, application):
    store = application.extensions['store']
    store.shelf(ALBUM, True)
    added = post(client, f'/api/albums/{ALBUM}/listening-pile')
    assert added.status_code == 200
    assert added.json['in_listening_pile'] and not added.json['on_loan']
    assert post(client, f'/api/albums/{ALBUM}/listening-pile').status_code == 200
    assert len(client.get('/api/listening-pile').json['albums']) == 1
    assert post(client, f'/api/albums/{ALBUM}/listening-pile', method='DELETE').status_code == 200
    assert len(client.get('/api/shelf').json['albums']) == 1


def test_borrow_requires_verified_selection(client):
    response = post(client, f'/api/albums/{ALBUM}/borrow')
    assert response.status_code == 400
    assert client.get('/api/listening-pile').json['albums'] == []


def test_borrow_then_keep_does_not_clear_selected_recordings(client, application, monkeypatch):
    store = application.extensions['store']
    musicbrainz = application.extensions['musicbrainz']
    playable = application.extensions['playable_releases']
    monkeypatch.setattr(musicbrainz, 'ensure_group', lambda identifier: store.album(identifier))

    def verified_selection(album_id, release_id, spotify_album_id, **kwargs):
        assert kwargs['reviewed'] is False
        assert kwargs.get('add_to_shelf') is not True
        assert kwargs['split_sides'] is False
        with store.connect() as db:
            db.execute('UPDATE tracks SET spotify_id=?,verified=1 WHERE album_id=?', ('a'*22, album_id))
        return store.album(album_id)

    monkeypatch.setattr(playable, 'select', verified_selection)
    response = post(client, f'/api/albums/{ALBUM}/borrow', {
        'confirmed': True,
        'release_id': '55df510f-2f2b-4d1e-b7d6-0b54c1c9f48e',
        'spotify_album_id': 's'*22,
    })
    assert response.status_code == 200
    assert response.json['on_loan'] and response.json['in_listening_pile']
    assert not response.json['on_shelf']
    assert client.get('/api/shelf').json['albums'] == []

    kept = post(client, f'/api/albums/{ALBUM}/shelf')
    assert kept.status_code == 200
    assert kept.json['on_shelf'] and kept.json['in_listening_pile']
    assert not kept.json['on_loan']
    assert all(track['verified'] for track in kept.json['tracks'])
    assert len(client.get('/api/shelf').json['albums']) == 1
    assert len(client.get('/api/listening-pile').json['albums']) == 1
