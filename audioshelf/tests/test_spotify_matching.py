"""Bulk matching must fail closed, retain diagnostic reasons, and stay account-scoped."""
import time

from app.matching import candidate
from app.spotify_matching import SpotifyMatching, current_failures, report_text
from conftest import ALBUM, post


class FakeSpotify:
    connected = True
    market = 'GB'

    def __init__(self, source):
        self.source = source
        self.calls = 0

    def candidates(self, album, broad=False):
        self.calls += 1
        return [candidate(album, self.source)]

    def album(self, identifier):
        assert identifier == self.source['id']
        return self.source


def source_album(first='Opening', second='Closing'):
    return {'id': 's'*22, 'name': 'The Album', 'release_date': '2007-04-18',
            'all_tracks': [
                {'id': 'a'*22, 'name': first, 'duration_ms': 180000,
                 'artists': [{'name': 'The Artist'}], 'is_playable': True},
                {'id': 'b'*22, 'name': second, 'duration_ms': 240000,
                 'artists': [{'name': 'The Artist'}], 'is_playable': True}]}


def completed(manager, store):
    for _ in range(300):
        job = manager.status('owner', store)['job']
        if job and job['state'] != 'running':
            return job
        time.sleep(.01)
    raise AssertionError('Background Spotify matching did not finish')


def test_verified_bulk_match_saves_tracks_but_does_not_review_album(application):
    store = application.extensions['store']
    store.shelf(ALBUM, True)
    matcher, spotify = SpotifyMatching(), FakeSpotify(source_album())
    started = matcher.start('owner', store, spotify, application.extensions['playable_releases'])
    assert started['total'] == 1
    job = completed(matcher, store)
    assert job['state'] == 'complete' and job['matched'] == 1
    assert job['unmatched'] == 0
    album = store.album(ALBUM)
    assert album['spotify_album_id'] == 's'*22
    assert [t['verified'] for t in album['tracks']] == [1, 1]
    assert album['canonical_reviewed'] == 0
    assert current_failures(store) == {}
    assert matcher.ensure_started('owner', store, spotify, application.extensions['playable_releases']) is None


def test_partial_candidates_are_reported_and_never_persisted(application, monkeypatch):
    store = application.extensions['store']
    store.shelf(ALBUM, True)
    playable = application.extensions['playable_releases']
    monkeypatch.setattr(playable, 'enrich_probe', lambda album, assessment: album)
    spotify, matcher = FakeSpotify(source_album(first='Opening - Live')), SpotifyMatching()
    matcher.start('owner', store, spotify, playable)
    job = completed(matcher, store)
    assert job['unmatched'] == 1 and job['matched'] == 0
    album = store.album(ALBUM)
    assert album['spotify_album_id'] is None
    assert all(t['spotify_id'] is None for t in album['tracks'])
    report = report_text(store, job)
    assert 'The Artist — The Album' in report
    assert 'Opening' in report
    assert 'recording_version_mismatch' in report
    assert '1/2 verified' in report
    # Failed unchanged albums are not repeatedly auto-scanned on navigation.
    assert matcher.pending(store, spotify) == []
    assert matcher.pending(store, spotify, force=True) == [ALBUM]


def test_manual_track_choices_are_never_replaced(application):
    store = application.extensions['store']
    store.shelf(ALBUM, True)
    store.mapping(ALBUM, candidate(store.album(ALBUM), source_album(first='Opening - Live')), manual=True)
    before = store.album(ALBUM)
    assert before['tracks'][1]['method'] == 'manual'
    spotify, matcher = FakeSpotify(source_album()), SpotifyMatching()
    matcher.start('owner', store, spotify, application.extensions['playable_releases'])
    job = completed(matcher, store)
    assert job['matched'] == 0 and job['unmatched'] == 1
    assert spotify.calls == 0
    assert store.album(ALBUM)['tracks'] == before['tracks']
    assert 'Manual Spotify track choices' in report_text(store, job)


def test_removed_or_fixed_album_drops_from_failure_report(application, monkeypatch):
    store = application.extensions['store']
    store.shelf(ALBUM, True)
    matcher, spotify = SpotifyMatching(), FakeSpotify(source_album(first='Opening - Live'))
    monkeypatch.setattr(application.extensions['playable_releases'], 'enrich_probe', lambda a, c: a)
    matcher.start('owner', store, spotify, application.extensions['playable_releases'])
    completed(matcher, store)
    assert len(current_failures(store)) == 1
    store.shelf(ALBUM, False)
    assert current_failures(store) == {}
    assert 'No unresolved albums recorded.' in report_text(store, matcher.status('owner', store)['job'])


def test_bulk_routes_restrict_access_and_expose_copyable_report(application, client):
    assert client.get('/api/spotify/matching').status_code == 200
    result = client.get('/api/spotify/matching/report')
    assert result.status_code == 200
    assert 'text' in result.json
    assert post(client, '/api/spotify/matching', {}).status_code == 409
