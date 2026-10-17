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



def test_report_distinguishes_out_of_order_track_from_arbitrary_fallback():
    from app.spotify_matching import failure
    from conftest import spotify_track
    titles=['Out of the Blue', 'Second', 'Third', 'Fourth', 'Fifth', 'Sixth']
    canonical=[{'position':i,'title':title,'duration_ms':180000}
               for i,title in enumerate(titles,1)]
    tracks=[spotify_track(title,str(i)*22,180000) for i,title in enumerate(titles,1)]
    source={'id':'x'*22,'name':'The Album','release_date':'1970',
            'all_tracks':tracks[1:]+tracks[:1]}
    album={'id':ALBUM,'title':'The Album','release_date':'1970',
           'artists':[{'name':'The Artist'}], 'tracks':canonical, 'on_shelf':True}
    best=candidate(album,source)
    assert best['verified']==5
    issue=failure(album,'Incomplete ordered album',best)
    assert issue['tracks'][0]['title']=='Out of the Blue'
    assert issue['tracks'][0]['reason']=='matching_track_out_of_order'
    assert issue['tracks'][0]['spotify_title']=='Out of the Blue'
    assert issue['tracks'][0]['spotify_position']==6
    unrelated=[spotify_track('Time to Relax','t'*22)]
    source['all_tracks']=unrelated
    issue=failure(album,'Incomplete ordered album',candidate(album,source))
    assert issue['tracks'][0]['reason']=='no_compatible_track_in_this_spotify_edition'
    assert not issue['tracks'][0]['spotify_title']


def test_spotify_album_title_only_search_recovers_split_artist_credits(application,monkeypatch):
    from conftest import spotify_track
    spotify=application.extensions['spotify']
    tracks=[{'position':i,'title':t,'duration_ms':180000} for i,t in
            enumerate(['These Days','The Crossing'],1)]
    album={'id':ALBUM,'title':'Heat, Dust & Dreams','release_date':'1993',
           'artists':[{'name':'Johnny Clegg and Savuka'}], 'tracks':tracks}
    joint=[spotify_track(t,str(i)*22) for i,t in enumerate(['These Days','The Crossing'],1)]
    for track in joint:
        track['artists']=[{'name':'Johnny Clegg'},{'name':'Savuka'}]
    source={'id':'h'*22,'name':'Heat Dust & Dreams',
            'release_date':'1993','all_tracks':joint}
    wrong={'id':'z'*22,'name':'Shadow Man'}
    calls=[]
    def api(method,path,params=None,body=None):
        assert path=='search'
        calls.append(params['q'])
        items=[{'id':'h'*22,'name':'Heat Dust & Dreams'}] if params['q'].startswith('album:"Heat, Dust & Dreams"') and 'artist:' not in params['q'] else [wrong]
        return {'albums':{'items':items,'next':None}}
    monkeypatch.setattr(spotify,'api',api)
    monkeypatch.setattr(spotify,'album',lambda identifier: source if identifier=='h'*22
                        else {'id':'z'*22,'name':'Shadow Man','release_date':'1993',
                              'all_tracks':[spotify_track('Unrelated','u'*22)]})
    choices=spotify.candidates(album,broad=True)
    assert choices[0]['id']=='h'*22
    assert choices[0]['verified']==2
    assert any(q.startswith('album:"Heat, Dust & Dreams"') and 'artist:' not in q for q in calls)
