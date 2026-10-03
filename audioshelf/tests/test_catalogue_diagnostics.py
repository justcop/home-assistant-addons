import base64

import pytest
import requests

from app.catalogue_rules import BEATLES, BEATLES_CORE, BEATLES_SERIES, MAGICAL_MYSTERY_TOUR
from app.errors import AppError
from app.matching import candidate, track_score, edition_year
from app.storage import Store
from app.themes import THEMES
from conftest import ALBUM, ARTIST, RELEASE, post, spotify_track
from test_release_filters import edition
from test_artwork import png


def test_beatles_core_catalogue_includes_soundtracks_excludes_regional_and_compilation(application, monkeypatch):
    mb = application.extensions['musicbrainz']
    groups = [{'id': identifier, 'title': title, 'primary-type': 'Album', 'first-release-date': '1967',
        'secondary-types': ['Soundtrack'] if title in {'Help!', 'Yellow Submarine', 'Magical Mystery Tour', 'A Hard Day’s Night'} else [],
        'artist-credit': [{'artist': {'id': BEATLES, 'name': 'The Beatles'}}]} for identifier, title in BEATLES_CORE.items()]
    groups += [{**groups[0], 'id': '11111111-1111-4111-8111-111111111111', 'title': 'Meet the Beatles!', 'secondary-types': []},
               {**groups[0], 'id': '22222222-2222-4222-8222-222222222222', 'title': 'Past Masters', 'secondary-types': ['Compilation']}]
    def get(entity, params):
        if entity == 'series/'+BEATLES_SERIES:
            return {'type': 'Release group', 'relations': [{'release-group': {'id': g['id']}} for g in groups if g['title'] != 'Meet the Beatles!']}
        return {'release-groups': groups, 'release-group-count': len(groups)}
    monkeypatch.setattr(mb, 'get', get)
    assert {a['id'] for a in mb.artist_albums(BEATLES)} == set(BEATLES_CORE)
    assert len(mb.artist_albums(BEATLES, manage=True)) == 15
    assert application.extensions['store'].release_filters(MAGICAL_MYSTERY_TOUR)['countries'][0] == 'US'


def test_verified_beatles_snapshot_survives_series_outage(application, monkeypatch):
    mb = application.extensions['musicbrainz']
    monkeypatch.setattr(mb, 'get', lambda *args, **kwargs: (_ for _ in ()).throw(AppError('Unavailable', 502)))
    assert mb.series_members(BEATLES_SERIES) == set(BEATLES_CORE)
    with pytest.raises(AppError):
        mb.series_members(RELEASE)


def test_series_and_individual_overrides_work_for_other_artists_and_keep_shelf(application, client, monkeypatch):
    store = application.extensions['store']; mb = application.extensions['musicbrainz']
    store.shelf(ALBUM, True); before = store.album(ALBUM)['tracks']
    group = {'id': ALBUM, 'title': 'The Album', 'primary-type': 'Album', 'secondary-types': ['Soundtrack'],
             'artist-credit': [{'artist': {'id': ARTIST, 'name': 'The Artist'}}]}
    monkeypatch.setattr(mb, 'get', lambda entity, params: {'type': 'Release group', 'relations': [{'release-group': {'id': ALBUM}}]})
    assert not mb.membership(group)
    assert post(client, '/api/artists/'+ARTIST+'/series', {'series_id': RELEASE}, method='PUT').status_code == 200
    assert mb.membership(group)
    post(client, '/api/albums/'+ALBUM+'/catalogue', {'choice': 'exclude'})
    assert not mb.membership(group)
    post(client, '/api/albums/'+ALBUM+'/catalogue', {'choice': 'include'})
    assert mb.membership(group)
    post(client, '/api/albums/'+ALBUM+'/catalogue', {'choice': 'auto'})
    assert mb.membership(group)
    assert store.album(ALBUM)['tracks'] == before and store.album(ALBUM)['on_shelf']
    restarted = Store(store.directory, store.cache_directory)
    assert restarted.catalogue_series(ARTIST) == RELEASE


def test_non_album_series_cannot_be_selected(application, client, monkeypatch):
    monkeypatch.setattr(application.extensions['musicbrainz'], 'get', lambda *args: {'type': 'Recording', 'relations': []})
    assert post(client, '/api/artists/'+ARTIST+'/series', {'series_id': RELEASE}, method='PUT').status_code == 400
    assert application.extensions['store'].catalogue_series(ARTIST) is None


@pytest.mark.parametrize('printed,source,duration', [
    ('Pull Pulk Revolving Doors', 'Pulk/Pull Revolving Doors', 247000),
    ('The Morning Bell Amnesiac', 'Morning Bell/Amnesiac', 194000),
    ('Dollars & Cents', 'Dollars and Cents', 291000),
])
def test_amnesiac_printed_title_variants_verify_with_artist_and_duration(printed, source, duration):
    canonical = {'title': printed, 'duration_ms': duration, 'recording_id': 'linked-recording', 'recording_title': source}
    spotify = {'name': source, 'duration_ms': duration, 'artists': [{'name': 'Radiohead'}]}
    assert track_score(canonical, spotify, ['Radiohead'])[1]
    spotify['duration_ms'] += 60000
    assert track_score(canonical, spotify, ['Radiohead']) == (0, False)


def test_amnesiac_alias_cannot_accept_kid_a_morning_bell_or_another_artist():
    canonical = {'title': 'The Morning Bell Amnesiac', 'duration_ms': 194000,
                 'recording_id': 'linked-recording', 'recording_title': 'Morning Bell/Amnesiac'}
    spotify = {'name': 'Morning Bell', 'duration_ms': 275000, 'artists': [{'name': 'Radiohead'}]}
    assert track_score(canonical, spotify, ['Radiohead']) == (0, False)
    spotify.update(name='Morning Bell/Amnesiac', duration_ms=194000, artists=[{'name': 'Tribute Band'}])
    assert track_score(canonical, spotify, ['Radiohead']) == (0, False)
    assert not track_score({k: v for k, v in canonical.items() if k not in {'recording_id', 'recording_title'}}, spotify, ['Tribute Band'])[1]


def test_dated_remix_is_rejected_while_stereo_mix_is_accepted(application):
    track = application.extensions['store'].album(ALBUM)['tracks'][0]
    assert track_score(track, spotify_track('Opening - 2022 Remix', 'a'*22), ['The Artist']) == (0, False)
    assert track_score(track, spotify_track('Opening - Stereo Mix', 'a'*22), ['The Artist'])[1]
    assert edition_year({'name': 'Album - Remix 2025'}) == 0


def test_diagnostics_record_matching_decisions_and_errors_without_credentials(application, client, monkeypatch):
    store = application.extensions['store']; spotify = application.extensions['spotify']
    spotify.tokens = {'access_token': 'secret-access-token', 'refresh_token': 'secret-refresh-token'}
    source = {'id': 's'*22, 'name': 'The Album', 'all_tracks': [spotify_track('Opening', 'a'*22)]}
    result = candidate(store.album(ALBUM), source)
    monkeypatch.setattr(spotify, 'candidates', lambda album: [result])
    assert post(client, '/api/albums/'+ALBUM+'/resolve').status_code == 200
    monkeypatch.setattr(application.extensions['musicbrainz'], 'get', lambda *args, **kwargs: (_ for _ in ()).throw(AppError('MusicBrainz unavailable', 502)))
    assert client.get('/api/albums/'+ALBUM+'/releases?offset=0').status_code == 502
    response = client.get('/api/albums/'+ALBUM+'/diagnostics')
    assert response.status_code == 200 and 'attachment;' in response.headers['Content-Disposition']
    assert response.json['format'] == 'audioshelf-diagnostics-1'
    events = response.json['events']
    choice = next(e['details']['candidates'][0] for e in events if e['event'] == 'spotify_candidates')
    assert choice['mappings'][0]['assessment']['reason'] == 'exact_title'
    assert choice['mappings'][1]['assessment']['reason'] == 'no_ordered_match'
    assert any(e['event'] == 'request_error' for e in events)
    assert 'secret-access-token' not in response.text and 'secret-refresh-token' not in response.text
    assert 'private_directory' not in response.text


def test_diagnostics_are_bounded_and_keep_only_latest_large_assessment(application):
    store = application.extensions['store']
    for n in range(210):
        store.diagnostic(ALBUM, 'request_error', {'number': n})
    for n in range(3):
        store.diagnostic(ALBUM, 'spotify_candidates', {'number': n})
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM diagnostics').fetchone()[0] <= 200
        assert db.execute("SELECT count(*) FROM diagnostics WHERE event='spotify_candidates'").fetchone()[0] == 1


def test_cover_selection_preserves_tracks_and_custom_cover_when_download_fails(application, client, monkeypatch):
    store = application.extensions['store']; artwork = application.extensions['artwork']
    before = store.album(ALBUM)['tracks']
    def get(entity, params):
        assert entity == 'release/'+RELEASE
        result = edition()
        if 'media' not in params.get('inc', '').split('+'):
            result.pop('media')
        return result
    monkeypatch.setattr(application.extensions['musicbrainz'], 'get', get)
    monkeypatch.setattr(artwork, 'download', lambda url: (png(), 'image/png'))
    assert post(client, '/api/albums/'+ALBUM+'/artwork-release', {'release_id': RELEASE}).status_code == 200
    assert store.setting('artwork_release:'+ALBUM) == RELEASE
    assert store.album(ALBUM)['tracks'] == before
    artwork.upload(ALBUM, base64.b64encode(png()).decode())
    custom = store.artwork_override(ALBUM)
    monkeypatch.setattr(artwork, 'download', lambda url: (_ for _ in ()).throw(requests.HTTPError('404')))
    assert post(client, '/api/albums/'+ALBUM+'/artwork-release', {'release_id': RELEASE}).status_code == 502
    assert store.artwork_override(ALBUM) == custom


def test_legacy_cassette_cover_uses_allowed_edition_without_changing_tracklist(application, monkeypatch):
    store = application.extensions['store']; artwork = application.extensions['artwork']; calls = []
    with store.connect() as db:
        db.execute("UPDATE albums SET release_label='GB · 2001 · Cassette' WHERE id=?", (ALBUM,))
    before = store.album(ALBUM)['tracks']
    allowed = '11111111-1111-4111-8111-111111111111'
    monkeypatch.setattr(application.extensions['musicbrainz'], 'release_page', lambda album_id: {'releases': [{'id': allowed}]})
    monkeypatch.setattr(artwork, 'download', lambda url: (calls.append(url) or png(), 'image/png'))
    assert artwork.get(ALBUM)[2] == 'cover-art-archive'
    assert '/release/'+allowed+'/' in calls[0]
    assert store.album(ALBUM)['tracks'] == before


@pytest.mark.parametrize('theme', [theme['id'] for theme in THEMES])
def test_all_ten_themes_persist_without_changing_album(application, client, theme):
    store = application.extensions['store']; before = store.album(ALBUM)
    assert post(client, '/api/settings', {'theme': theme}, method='PUT').status_code == 200
    assert client.get('/api/status').json['theme'] == theme
    assert Store(store.directory, store.cache_directory).setting('theme') == theme
    assert store.album(ALBUM) == before
    assert len(client.get('/api/settings').json['themes']) == 10


def test_invalid_theme_does_not_partially_save_preferences(application, client):
    before = application.extensions['store'].release_filters()
    assert post(client, '/api/settings', {'theme': 'invalid', 'release_filters': {'countries': ['US'], 'formats': ['cd']}}, method='PUT').status_code == 400
    assert application.extensions['store'].release_filters() == before
