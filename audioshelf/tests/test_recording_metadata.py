import pytest

from app.errors import AppError
from app.matching import candidate, track_assessment, track_score
from app.musicbrainz import release_tracks
from app.storage import Store
from conftest import ALBUM, RELEASE, post, spotify_track

RECORDING = '11111111-1111-4111-8111-111111111111'


def canonical(**changes):
    return {'title': 'Printed alternate title', 'recording_id': RECORDING,
            'recording_title': 'Recording title', 'recording_aliases': ['Other spelling'],
            'duration_ms': 180000, **changes}


@pytest.mark.parametrize('name', ['Recording title', 'Other spelling', 'Other spelling - 2022 Mix'])
def test_any_artist_can_match_the_linked_recording_names(name):
    result = track_assessment(canonical(), spotify_track(name, 'a'*22), ['The Artist'])
    assert result['verified'] and result['reason'] == 'same_recording_title'


@pytest.mark.parametrize('changes,name', [
    ({'recording_id': None}, 'Other spelling'),
    ({'duration_ms': None}, 'Other spelling'),
    ({'duration_ms': 250000}, 'Other spelling'),
    ({'title': 'Printed alternate title - Live'}, 'Other spelling'),
    ({'recording_title': 'Recording title - Live'}, 'Other spelling'),
    ({}, 'Other spelling - Live'),
    ({}, 'Other spelling - Demo'),
    ({}, 'Other spelling - 2022 Remix'),
])
def test_recording_aliases_keep_identity_duration_and_version_safeguards(changes, name):
    assert not track_score(canonical(**changes), spotify_track(name, 'a'*22), ['The Artist'])[1]


def test_no_radiohead_specific_exception_remains():
    track = {'title': 'Pull Pulk Revolving Doors', 'duration_ms': 247000}
    source = {**spotify_track('Pulk/Pull Revolving Doors', 'a'*22, 247000), 'artists': [{'name': 'Radiohead'}]}
    assert not track_score(track, source, ['Radiohead'])[1]
    assert track_score({**track, 'recording_id': RECORDING, 'recording_title': source['name']}, source, ['Radiohead'])[1]


def test_recording_metadata_survives_save_and_restart(application):
    store = application.extensions['store']
    release = {'id': RELEASE, 'media': [{'format': 'CD', 'position': 1, 'tracks': [{
        'position': 1, 'title': 'Printed alternate title', 'length': 180000,
        'recording': {'id': RECORDING, 'title': 'Recording title', 'aliases': [{'name': 'Other spelling'}]}}]}]}
    store.set_tracks(ALBUM, release, release_tracks(release))
    track = Store(store.directory, store.cache_directory).album(ALBUM)['tracks'][0]
    assert track['title'] == 'Printed alternate title'
    assert track['recording_title'] == 'Recording title'
    assert track['recording_aliases'] == ['Other spelling']


def test_real_version_three_migration_preserves_collection_and_manual_mapping(application):
    store = application.extensions['store']
    store.shelf(ALBUM, True)
    with store.connect() as db:
        db.execute("UPDATE tracks SET spotify_id=?,method='manual',verified=1 WHERE position=1", ('a'*22,))
        db.execute('ALTER TABLE tracks DROP COLUMN recording_title')
        db.execute('ALTER TABLE tracks DROP COLUMN recording_aliases')
        db.execute('PRAGMA user_version=3')
    upgraded = Store(store.directory, store.cache_directory)
    album = upgraded.album(ALBUM)
    assert album['on_shelf'] and len(album['tracks']) == 2
    assert album['tracks'][0]['spotify_id'] == 'a'*22
    assert album['tracks'][0]['method'] == 'manual' and album['tracks'][0]['verified']
    assert album['tracks'][0]['recording_title'] == '' and album['tracks'][0]['recording_aliases'] == []
    assert Store(store.directory, store.cache_directory).album(ALBUM) == album


@pytest.mark.parametrize('manual_album', [False, True])
def test_existing_shelf_enriches_unmatched_tracks_and_preserves_manual_corrections(application, client, monkeypatch, manual_album):
    store = application.extensions['store']; mb = application.extensions['musicbrainz']; spotify = application.extensions['spotify']
    with store.connect() as db:
        db.execute("UPDATE tracks SET spotify_id=?,method='manual',verified=1 WHERE position=1", ('m'*22,))
        db.execute('UPDATE tracks SET recording_id=? WHERE position=2', (RECORDING,))
    before = store.album(ALBUM)
    source = {'id': 's'*22, 'name': 'The Album', 'all_tracks': [
        spotify_track('Opening', 'a'*22), spotify_track('Other spelling', 'b'*22, 240000)]}
    calls = []
    def get(entity, params):
        calls.append(entity)
        assert entity == 'recording/'+RECORDING and params == {'inc': 'aliases+isrcs'}
        return {'id': RECORDING, 'title': 'Closing', 'aliases': [{'name': 'Other spelling'}], 'isrcs': ['GBTEST']}
    monkeypatch.setattr(mb, 'get', get)
    monkeypatch.setattr(spotify, 'album', lambda identifier: source)
    monkeypatch.setattr(spotify, 'candidates', lambda album: [candidate(album, source)])
    endpoint = '/mapping' if manual_album else '/resolve'
    response = post(client, '/api/albums/'+ALBUM+endpoint, {'spotify_album_id': 's'*22})
    assert response.status_code == 200
    album = store.album(ALBUM)
    assert album['playable']
    assert album['tracks'][1]['spotify_id'] == 'b'*22
    assert album['tracks'][1]['recording_aliases'] == ['Other spelling']
    assert [t['title'] for t in album['tracks']] == [t['title'] for t in before['tracks']]
    assert album['release_id'] == before['release_id'] and album['canonical_reviewed'] == before['canonical_reviewed']
    if not manual_album:
        assert album['tracks'][0]['spotify_id'] == 'm'*22
    assert calls == ['recording/'+RECORDING]
    # Persisted recording names avoid another lookup during the next match.
    assert post(client, '/api/albums/'+ALBUM+endpoint, {'spotify_album_id': 's'*22}).status_code == 200
    assert calls == ['recording/'+RECORDING]


def test_metadata_outage_keeps_saved_names_and_mappings(application, monkeypatch):
    store = application.extensions['store']; mb = application.extensions['musicbrainz']
    with store.connect() as db:
        db.execute('UPDATE tracks SET recording_id=?', (RECORDING,))
    before = store.album(ALBUM)
    calls = []
    def unavailable(*args):
        calls.append(args)
        raise AppError('MusicBrainz unavailable', 502)
    monkeypatch.setattr(mb, 'get', unavailable)
    assert mb.enrich_recordings(before, {1, 2}) == before
    assert store.album(ALBUM) == before and len(calls) == 1
    assert store.diagnostics(ALBUM)[0]['event'] == 'recording_metadata_unavailable'


def test_wrong_recording_response_cannot_supply_aliases(application, monkeypatch):
    store = application.extensions['store']; mb = application.extensions['musicbrainz']
    with store.connect() as db:
        db.execute('UPDATE tracks SET recording_id=? WHERE position=1', (RECORDING,))
    before = store.album(ALBUM)
    monkeypatch.setattr(mb, 'get', lambda *args: {'id': RELEASE, 'title': 'Unrelated title'})
    assert mb.enrich_recordings(before, {1}) == before


def test_verified_manual_track_does_not_trigger_recording_lookup(application, monkeypatch):
    store = application.extensions['store']; mb = application.extensions['musicbrainz']
    with store.connect() as db:
        db.execute("UPDATE tracks SET recording_id=?,method='manual',verified=1,spotify_id=? WHERE position=1", (RECORDING, 'm'*22))
    monkeypatch.setattr(mb, 'get', lambda *args: pytest.fail('Manual correction must be preserved'))
    before = store.album(ALBUM)
    assert mb.enrich_recordings(before, {1}) == before
