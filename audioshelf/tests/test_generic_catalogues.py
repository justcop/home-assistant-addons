import pytest

from app.errors import AppError
from app.storage import Store
from conftest import ALBUM, ARTIST, RELEASE, post
from catalogue_examples import BEATLES, BEATLES_SERIES, MAGICAL_MYSTERY_TOUR

OTHER_ARTIST = '22222222-2222-4222-8222-222222222222'
OTHER_SERIES = '33333333-3333-4333-8333-333333333333'
OTHER_ALBUM = '44444444-4444-4444-8444-444444444444'


def group(artists=(ARTIST,), secondary=()):
    return {'id': ALBUM, 'title': 'The Album', 'primary-type': 'Album',
            'secondary-types': list(secondary), 'artist-credit': [
                {'artist': {'id': identifier, 'name': 'An artist'}} for identifier in artists]}


def test_no_artist_or_album_has_a_built_in_catalogue_exception(application):
    store = application.extensions['store']
    assert store.catalogue_series(BEATLES) is None
    assert store.catalogue_series(ARTIST) is None
    assert store.release_countries(MAGICAL_MYSTERY_TOUR) is None
    assert store.release_filters(MAGICAL_MYSTERY_TOUR) == store.release_filters()


def test_catalogue_discovery_uses_artist_name_and_returns_only_release_group_series(application, client, monkeypatch):
    store = application.extensions['store']; calls = []
    def get(entity, params=None):
        calls.append((entity, params))
        if entity == 'artist/'+ARTIST:
            return {'id': ARTIST, 'name': 'AC/DC'}
        assert entity == 'series'
        return {'series': [
            {'id': RELEASE, 'name': 'AC/DC core catalogue', 'type': 'Release group', 'disambiguation': 'Original albums'},
            {'id': OTHER_SERIES, 'name': 'AC/DC remasters', 'type': 'Release'},
            {'id': OTHER_ALBUM, 'name': 'AC/DC songs', 'type': 'Recording'}]}
    monkeypatch.setattr(application.extensions['musicbrainz'], 'get', get)
    response = client.get('/api/artists/'+ARTIST+'/catalogue-series')
    assert response.status_code == 200
    assert response.json['query'] == 'AC/DC'
    assert [s['id'] for s in response.json['series']] == [RELEASE]
    assert calls[-1][1]['query'] == 'series:"AC\\/DC"'
    assert store.catalogue_series(ARTIST) is None
    assert client.get('/api/artists/'+ARTIST+'/catalogue-series?q=Original%20albums').json['query'] == 'Original albums'
    assert calls[-1][1]['query'] == 'series:"Original albums"'
    assert client.get('/api/artists/not-an-id/catalogue-series').status_code == 400


def test_unrelated_series_cannot_replace_selected_catalogue(application, client, monkeypatch):
    store = application.extensions['store']
    store.set_setting('series:'+ARTIST, RELEASE)
    monkeypatch.setattr(application.extensions['musicbrainz'], 'get', lambda *args: {
        'type': 'Release group', 'name': 'Another artist', 'relations': [{'release-group': {'id': OTHER_ALBUM}}]})
    response = post(client, '/api/artists/'+ARTIST+'/series', {'series_id': OTHER_SERIES}, method='PUT')
    assert response.status_code == 400 and 'none of this artist' in response.json['error']
    assert store.catalogue_series(ARTIST) == RELEASE


def test_explicit_catalogue_and_country_choices_survive_upgrade_and_restart(application):
    store = application.extensions['store']
    store.set_setting('series:'+BEATLES, BEATLES_SERIES)
    store.set_setting('release_countries:'+MAGICAL_MYSTERY_TOUR, ['US'])
    restarted = Store(store.directory, store.cache_directory)
    assert restarted.catalogue_series(BEATLES) == BEATLES_SERIES
    assert restarted.release_countries(MAGICAL_MYSTERY_TOUR) == ['US']


def test_series_snapshot_survives_restart_and_deleted_replaceable_cache(application, monkeypatch):
    store = application.extensions['store']; mb = application.extensions['musicbrainz']
    monkeypatch.setattr(mb, 'get', lambda *args: {'type': 'Release group', 'name': 'Original studio catalogue',
        'relations': [{'release-group': {'id': ALBUM}}]})
    assert mb.series_members(RELEASE) == {ALBUM}
    snapshot = Store(store.directory, store.cache_directory).setting('series_snapshot:'+RELEASE)
    assert snapshot['name'] == 'Original studio catalogue' and snapshot['members'] == [ALBUM]
    with store.cache_connect() as db:
        db.execute('DELETE FROM cache')
    monkeypatch.setattr(mb, 'get', lambda *args: (_ for _ in ()).throw(AppError('Unavailable', 502)))
    assert mb.series_members(RELEASE) == {ALBUM}


def test_old_cached_series_members_gain_a_persistent_snapshot(application, monkeypatch):
    store = application.extensions['store']
    store.cache_put('series-members:'+RELEASE, [ALBUM])
    monkeypatch.setattr(application.extensions['musicbrainz'], 'get', lambda *args: pytest.fail('Cache should be reused'))
    assert application.extensions['musicbrainz'].series_members(RELEASE) == {ALBUM}
    assert store.setting('series_snapshot:'+RELEASE)['members'] == [ALBUM]


@pytest.mark.parametrize('secondary,expected', [([], True), (['Soundtrack'], True),
    (['Compilation'], False), (['Live'], False), (['Soundtrack', 'Live'], False)])
def test_curated_rules_handle_soundtracks_for_every_artist(application, monkeypatch, secondary, expected):
    store = application.extensions['store']; mb = application.extensions['musicbrainz']
    store.set_setting('series:'+ARTIST, RELEASE)
    monkeypatch.setattr(mb, 'series_members', lambda series: {ALBUM})
    assert mb.membership(group(secondary=secondary), ARTIST) is expected
    store.set_setting('catalogue:'+ALBUM, 'include' if not expected else 'exclude')
    assert mb.membership(group(secondary=secondary), ARTIST) is not expected


def test_collaborative_catalogue_rules_do_not_depend_on_credit_order(application, monkeypatch):
    store = application.extensions['store']; mb = application.extensions['musicbrainz']
    store.set_setting('series:'+ARTIST, RELEASE)
    store.set_setting('series:'+OTHER_ARTIST, OTHER_SERIES)
    monkeypatch.setattr(mb, 'series_members', lambda series: {ALBUM} if series == OTHER_SERIES else {OTHER_ALBUM})
    for credits in [(ARTIST, OTHER_ARTIST), (OTHER_ARTIST, ARTIST)]:
        album = group(credits)
        assert not mb.membership(album, ARTIST)
        assert mb.membership(album, OTHER_ARTIST)
        assert mb.membership(album)


def test_clearing_series_restores_generic_rules_and_keeps_shelf(application, client):
    store = application.extensions['store']; mb = application.extensions['musicbrainz']
    store.shelf(ALBUM, True)
    store.set_setting('series:'+ARTIST, RELEASE)
    before = store.album(ALBUM)
    assert post(client, '/api/artists/'+ARTIST+'/series', {'series_id': None}, method='PUT').status_code == 200
    assert store.catalogue_series(ARTIST) is None
    assert mb.membership(group()) and not mb.membership(group(secondary=['Soundtrack']))
    assert store.album(ALBUM) == before


@pytest.mark.parametrize('artist_name,album_title', [('An unfamiliar artist', 'Regional LP'),
    ('A film composer', 'Original score'), ('A new band', 'Second album')])
def test_catalogue_decisions_depend_on_membership_and_types_not_names(application,monkeypatch,artist_name,album_title):
    store=application.extensions['store'];mb=application.extensions['musicbrainz']
    store.set_setting('series:'+ARTIST, RELEASE)
    monkeypatch.setattr(mb,'series_members',lambda series:{ALBUM})
    album=group(secondary=['Soundtrack'])
    album['title']=album_title
    album['artist-credit'][0]['artist']['name']=artist_name
    assert mb.membership(album,ARTIST)
    assert not mb.membership({**album,'id':OTHER_ALBUM},ARTIST)
    assert not mb.membership({**album,'secondary-types':['Compilation']},ARTIST)
