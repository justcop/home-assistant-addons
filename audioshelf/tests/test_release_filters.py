import json
import sqlite3

import pytest

from app.errors import AppError
from app.musicbrainz import release_rank
from app.release_filters import DEFAULT_FILTERS, matches_filters
from app.storage import Store
from conftest import ALBUM, RELEASE, post


def edition(country='GB', formats=('CD',), identifier=RELEASE, **extra):
    return {'id': identifier, 'title': 'The Album', 'country': country, 'date': '2007-04-23',
            'status': 'Official', 'release-group': {'id': ALBUM},
            'media': [{'format': f, 'position': n+1, 'tracks': [
                {'title': 'Opening', 'position': 1, 'length': 180000}]} for n, f in enumerate(formats)], **extra}


@pytest.mark.parametrize('country,formats,expected', [
    ('GB', ('CD',), True), ('GB', ('12" Vinyl',), True),
    ('GB', ('7" Vinyl',), True), ('GB', ('Digital Media',), True),
    ('GB', ('Enhanced CD',), True), ('GB', ('8cm CD',), True),
    ('GB', ('CD', 'DVD-Video'), True), ('GB', ('CD', 'CD'), True),
    ('GB', ('Cassette',), False), ('GB', ('CD', 'Cassette'), False),
    ('GB', ('DVD-Video',), False), ('GB', ('SACD',), False),
    ('GB', (None,), False), ('GB', (), False),
    ('US', ('CD',), True), ('XW', ('Digital Media',), True),
    ('XE', ('CD',), True), (None, ('CD',), True),
])
def test_default_countries_and_all_audio_media(country, formats, expected):
    assert matches_filters(edition(country, formats), DEFAULT_FILTERS) is expected


def test_multiple_release_events_include_gb_but_missing_area_does_not():
    release = edition('US', **{'release-events': [
        {'area': None}, {'area': {'iso-3166-1-codes': ['US']}},
        {'area': {'iso-3166-1-codes': ['GB']}}]})
    strict = {**DEFAULT_FILTERS, 'countries': ['GB'], 'strict_countries': True}
    assert matches_filters(release, strict)
    release['release-events'].pop()
    assert not matches_filters(release, strict)


def test_preferences_persist_preserve_mappings_and_are_backed_up(application, client):
    store = application.extensions['store']
    assert client.get('/api/settings').json['release_filters'] == DEFAULT_FILTERS
    with store.connect() as db:
        db.execute("UPDATE tracks SET spotify_id=?,method='manual',verified=1 WHERE position=1", ('a'*22,))
    before = store.album(ALBUM)
    response = post(client, '/api/settings', {'release_filters': {
        'countries': ['us', 'GB', 'us'], 'formats': ['cassette', 'cd', 'cd']}}, method='PUT')
    expected = {'countries': ['US', 'GB'], 'formats': ['cassette', 'cd'], 'strict_countries': False}
    assert response.status_code == 200
    assert response.json['release_filters'] == expected
    assert client.get('/api/status').json['release_filters'] == expected
    assert Store(store.directory, store.cache_directory).release_filters() == expected
    after = store.album(ALBUM)
    assert after['tracks'] == before['tracks'] and after['release_id'] == before['release_id']
    with sqlite3.connect(store.backup()) as db:
        assert json.loads(db.execute("SELECT value FROM settings WHERE key='release_filters'").fetchone()[0]) == expected


@pytest.mark.parametrize('value', [None, [], {}, {'countries': 'GB', 'formats': ['cd']},
    {'countries': ['United Kingdom'], 'formats': ['cd']}, {'countries': [None], 'formats': ['cd']},
    {'countries': ['GB'], 'formats': []}, {'countries': ['GB'], 'formats': 'cd'},
    {'countries': ['GB'], 'formats': ['unknown']}, {'countries': ['GB'], 'formats': [{}]}])
def test_invalid_settings_leave_preferences_unchanged(client, value):
    response = post(client, '/api/settings', {'release_filters': value}, method='PUT')
    assert response.status_code == 400
    assert client.get('/api/settings').json['release_filters'] == DEFAULT_FILTERS


def test_settings_mutations_require_application_header(client):
    assert client.put('/api/settings', json={'release_filters': DEFAULT_FILTERS}).status_code == 403
    assert client.put('/api/settings', json=[], headers={'X-AudioShelf-Request': '1'}).status_code == 400


def test_browse_and_automatic_selection_skip_early_cassette(application, client, monkeypatch):
    mb = application.extensions['musicbrainz']; store = application.extensions['store']
    cassette = edition(formats=('Cassette',), identifier='11111111-1111-4111-8111-111111111111', date='2007')
    us = edition('US', identifier='22222222-2222-4222-8222-222222222222', date='2007-04-18')
    cd = edition()
    def get(entity, params=None):
        if entity == 'release':
            return {'releases': [cassette, us, cd], 'release-count': 3}
        assert entity == 'release/'+RELEASE
        return cd
    monkeypatch.setattr(mb, 'get', get)
    assert [r['id'] for r in client.get('/api/albums/'+ALBUM+'/releases').json['releases']] == [RELEASE, us['id']]
    with store.connect() as db:
        db.execute('DELETE FROM tracks')
    assert mb.ensure_tracks(ALBUM)['release_id'] == RELEASE


def test_no_matching_editions_has_settings_guidance_without_fallback(application, monkeypatch):
    mb = application.extensions['musicbrainz']; store = application.extensions['store']
    monkeypatch.setattr(mb, 'get', lambda *args, **kwargs: {'releases': [edition('XW')], 'release-count': 1})
    store.set_release_filters({**DEFAULT_FILTERS, 'countries': ['GB'], 'strict_countries': True})
    with store.connect() as db:
        db.execute('DELETE FROM tracks')
    with pytest.raises(AppError, match='No MusicBrainz editions match.*Settings'):
        mb.ensure_tracks(ALBUM)
    assert store.album(ALBUM)['tracks'] == []


def test_direct_selection_cannot_bypass_filters_or_clear_mappings(application, client, monkeypatch):
    store = application.extensions['store']; before = store.album(ALBUM)
    monkeypatch.setattr(application.extensions['musicbrainz'], 'get', lambda *args, **kwargs: edition(formats=('Cassette',)))
    response = post(client, '/api/albums/'+ALBUM+'/release', {'release_id': RELEASE, 'confirmed': True})
    assert response.status_code == 400
    assert 'release filters' in response.json['error']
    assert store.album(ALBUM) == before
    post(client, '/api/settings', {'release_filters': {'countries': [], 'formats': ['cassette']}}, method='PUT')
    assert post(client, '/api/albums/'+ALBUM+'/release', {'release_id': RELEASE, 'confirmed': True}).status_code == 200


def test_filter_change_applies_to_cached_raw_pages(application, client, monkeypatch):
    mb = application.extensions['musicbrainz']
    monkeypatch.setattr(mb, 'get', lambda *args, **kwargs: {'releases': [edition('XW')], 'release-count': 1})
    application.extensions['store'].set_release_filters({**DEFAULT_FILTERS, 'countries': ['GB'], 'strict_countries': True})
    assert mb.releases(ALBUM) == []
    post(client, '/api/settings', {'release_filters': {'countries': ['XW'], 'formats': ['cd']}}, method='PUT')
    assert len(mb.releases(ALBUM)) == 1
    post(client, '/api/settings', {'release_filters': {'countries': [], 'formats': ['cd']}}, method='PUT')
    assert len(mb.releases(ALBUM)) == 1


def test_upgrade_from_version_two_keeps_collection_and_sets_defaults(application):
    store = application.extensions['store']; before = store.album(ALBUM)
    with store.connect() as db:
        db.execute('DROP TABLE settings')
        db.execute('PRAGMA user_version=2')
    reloaded = Store(store.directory, store.cache_directory)
    assert reloaded.release_filters() == DEFAULT_FILTERS
    assert reloaded.album(ALBUM) == before
    with reloaded.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 5


def test_country_and_format_priorities_with_original_reissue_safeguard():
    vinyl = edition(formats=('12" Vinyl',), identifier='gb-vinyl')
    cd = edition(identifier='gb-cd')
    digital = edition(formats=('Digital Media',), identifier='gb-digital')
    us = edition('US', ('12" Vinyl',), identifier='us-vinyl')
    worldwide = edition('XW', ('12" Vinyl',), identifier='worldwide')
    other = edition('AU', ('12" Vinyl',), identifier='other')
    reissue = edition(formats=('12" Vinyl',), identifier='gb-reissue', date='2025')
    releases = [other, worldwide, us, digital, cd, reissue, vinyl]
    assert [r['id'] for r in sorted(releases, key=lambda r: release_rank(r, '2007', DEFAULT_FILTERS))] == [
        'gb-vinyl', 'gb-cd', 'gb-digital', 'gb-reissue', 'us-vinyl', 'worldwide', 'other']
    assert all(matches_filters(r, DEFAULT_FILTERS) for r in releases)
    reversed_formats = {**DEFAULT_FILTERS, 'formats': ['cd', 'vinyl', 'digital']}
    assert sorted([vinyl, cd], key=lambda r: release_rank(r, '2007', reversed_formats))[0]['id'] == 'gb-cd'


def test_strict_country_setting_is_optional_and_validated(client):
    for invalid in ['yes', 1, None]:
        value = {**DEFAULT_FILTERS, 'strict_countries': invalid}
        assert post(client, '/api/settings', {'release_filters': value}, method='PUT').status_code == 400
    value = {**DEFAULT_FILTERS, 'countries': [], 'strict_countries': True}
    assert post(client, '/api/settings', {'release_filters': value}, method='PUT').status_code == 400


def test_paginated_picker_filters_each_page_and_keeps_raw_cursor(application, client, monkeypatch):
    seen = []
    def get(entity, params):
        seen.append(params['offset'])
        if not params['offset']:
            return {'releases': [edition(formats=('Cassette',))], 'release-count': 2}
        return {'releases': [edition()], 'release-count': 2}
    monkeypatch.setattr(application.extensions['musicbrainz'], 'get', get)
    first = client.get('/api/albums/'+ALBUM+'/releases?offset=0').json
    assert first['releases'] == [] and first['next_offset'] == 1
    second = client.get('/api/albums/'+ALBUM+'/releases?offset=1').json
    assert second['releases'][0]['id'] == RELEASE and second['next_offset'] is None
    assert seen == [0, 1]
    assert client.get('/api/albums/'+ALBUM+'/releases?offset=invalid').status_code == 400


def test_album_country_preference_does_not_change_other_albums(application, client):
    store = application.extensions['store']; before = store.album(ALBUM)['tracks']
    assert post(client, '/api/albums/'+ALBUM+'/release-countries', {'countries': ['US', 'GB']}, method='PUT').status_code == 200
    assert store.release_filters(ALBUM)['countries'] == ['US', 'GB']
    assert store.release_filters()['countries'][0] == 'GB'
    assert store.album(ALBUM)['tracks'] == before
    post(client, '/api/albums/'+ALBUM+'/release-countries', {'countries': None}, method='PUT')
    assert store.release_filters(ALBUM) == store.release_filters()


@pytest.mark.parametrize('preferred_country', ['JP', 'AU', 'US'])
def test_any_country_can_be_preferred_without_hidden_country_penalties(preferred_country):
    preferred = edition(preferred_country, identifier='preferred')
    gb = edition('GB', identifier='gb')
    filters = {**DEFAULT_FILTERS, 'countries': [preferred_country, 'GB']}
    assert min([gb, preferred], key=lambda r: release_rank(r, '2007', filters))['id'] == 'preferred'


def test_no_country_preference_uses_date_and_format_equally_for_all_countries():
    japanese = edition('JP', identifier='jp', date='2007-04-18')
    australian = edition('AU', identifier='au', date='2007-04-23')
    filters = {**DEFAULT_FILTERS, 'countries': []}
    assert min([australian, japanese], key=lambda r: release_rank(r, '2007', filters))['id'] == 'jp'
    for release in (japanese, australian):
        assert release_rank(release, '2007') == release_rank(release, '2007', DEFAULT_FILTERS)
