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


def fake_original_releases(application, monkeypatch):
    from conftest import RELEASE
    musicbrainz=application.extensions['musicbrainz']
    release={'id': RELEASE, 'title':'The Album', 'status':'Official',
        'date':'2007-04-18', 'country':'GB', 'release-group':{'id':ALBUM},
        'media':[{'position':1,'format':'CD','track-count':2,'tracks':[
            {'position':1,'title':'Opening','length':180000,
             'recording':{'title':'Opening','id':'rec-1','length':180000}},
            {'position':2,'title':'Closing','length':240000,
             'recording':{'title':'Closing','id':'rec-2','length':240000}}]}]}
    monkeypatch.setattr(musicbrainz,'releases',lambda album_id:[release])
    monkeypatch.setattr(musicbrainz,'get',lambda entity,params=None:release)
    return release


def completed(manager, store):
    for _ in range(300):
        job = manager.status('owner', store)['job']
        if job and job['state'] != 'running':
            return job
        time.sleep(.01)
    raise AssertionError('Background Spotify matching did not finish')


def test_verified_bulk_match_saves_tracks_but_does_not_review_album(application, monkeypatch):
    store = application.extensions['store']
    store.shelf(ALBUM, True)
    matcher, spotify = SpotifyMatching(), FakeSpotify(source_album())
    fake_original_releases(application,monkeypatch)
    application.extensions['playable_releases'].spotify=spotify
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
    fake_original_releases(application,monkeypatch)
    playable.spotify=spotify
    matcher.start('owner', store, spotify, playable)
    job = completed(matcher, store)
    assert job['unmatched'] == 1 and job['matched'] == 0
    album = store.album(ALBUM)
    assert album['spotify_album_id'] is None
    assert album['tracks']==[], 'An unverified imported tracklist must not remain canonical'
    assert album['release_id'] is None
    report = report_text(store, job)
    assert 'The Artist — The Album' in report
    assert 'Opening' in report
    assert 'recording_version_mismatch' in report
    assert '1/2 verified' in report
    # Failed unchanged albums are not repeatedly auto-scanned on navigation.
    assert matcher.pending(store, spotify) == []
    assert matcher.pending(store, spotify, force=True) == [ALBUM]



def test_invalid_manual_track_choices_are_replaced_with_complete_edition(application, monkeypatch):
    store=application.extensions['store']
    store.shelf(ALBUM, True)
    # One manual Spotify choice in an otherwise incomplete release cannot
    # block a complete official Spotify-compatible MusicBrainz release.
    store.mapping(ALBUM,candidate(store.album(ALBUM),source_album(first='Opening - Live')),manual=True)
    before=store.album(ALBUM)
    assert before['tracks'][1]['method']=='manual'
    fake_original_releases(application,monkeypatch)
    spotify, matcher=FakeSpotify(source_album()),SpotifyMatching()
    playable=application.extensions['playable_releases']
    playable.spotify=spotify
    matcher.start('owner',store,spotify,playable)
    job=completed(matcher,store)
    assert job['matched']==1 and job['unmatched']==0
    selected=store.album(ALBUM)
    assert selected['on_shelf'] and selected['playable']
    assert selected['release_id']==before['release_id']
    assert selected['canonical_reviewed']==0
    assert [t['spotify_id'] for t in selected['tracks']]==['a'*22,'b'*22]
    assert all(t['method']=='automatic' for t in selected['tracks'])
    assert not current_failures(store)


def test_reviewed_manual_edition_without_spotify_match_is_wiped(application,monkeypatch):
    store=application.extensions['store']
    store.shelf(ALBUM,True)
    with store.connect() as db:
        db.execute('UPDATE albums SET canonical_reviewed=1 WHERE id=?',(ALBUM,))
    store.mapping(ALBUM,candidate(store.album(ALBUM),source_album(first='Opening - Live')),manual=True)
    before=store.album(ALBUM)
    assert before['canonical_reviewed'] and any(t['method']=='manual' for t in before['tracks'])
    fake_original_releases(application,monkeypatch)
    spotify=FakeSpotify(source_album(first='Opening - Live'))
    playable=application.extensions['playable_releases']
    monkeypatch.setattr(playable,'enrich_probe',lambda probe, assessment:probe)
    playable.spotify=spotify
    matcher=SpotifyMatching()
    matcher.start('owner',store,spotify,playable)
    job=completed(matcher,store)
    assert job['matched']==0 and job['unmatched']==1
    cleared=store.album(ALBUM)
    assert cleared['on_shelf']
    assert not cleared['playable'] and cleared['tracks']==[]
    assert cleared['release_id'] is None
    assert cleared['spotify_album_id'] is None
    assert cleared['canonical_reviewed']==0
    assert current_failures(store)[ALBUM]['previous_manual_selection_cleared'] is True
    assert matcher.pending(store,spotify)==[]
    assert matcher.pending(store,spotify,force=True)==[ALBUM]


def test_reviewed_canonical_edition_remains_reviewed_when_fully_matched(application,monkeypatch):
    from conftest import RELEASE
    store=application.extensions['store']
    store.shelf(ALBUM,True)
    with store.connect() as db:
        db.execute('UPDATE albums SET canonical_reviewed=1 WHERE id=?',(ALBUM,))
    store.mapping(ALBUM,candidate(store.album(ALBUM),source_album(first='Opening - Live')),manual=True)
    fake_original_releases(application,monkeypatch)
    spotify=FakeSpotify(source_album())
    playable=application.extensions['playable_releases']
    playable.spotify=spotify
    matcher=SpotifyMatching()
    matcher.start('owner',store,spotify,playable)
    job=completed(matcher,store)
    assert job['matched']==1
    reviewed=store.album(ALBUM)
    assert reviewed['on_shelf'] and reviewed['playable']
    assert reviewed['canonical_reviewed']==1
    assert reviewed['release_id']==RELEASE


def test_reviewed_replacement_edition_requires_new_confirmation(application,monkeypatch):
    from conftest import RELEASE
    store=application.extensions['store']
    store.shelf(ALBUM,True)
    with store.connect() as db:
        db.execute('UPDATE albums SET canonical_reviewed=1 WHERE id=?',(ALBUM,))
    store.mapping(ALBUM,candidate(store.album(ALBUM),source_album(first='Opening - Live')),manual=True)
    mb=application.extensions['musicbrainz']
    alternative='77777777-7777-4777-8777-777777777777'
    base=fake_original_releases(application,monkeypatch)
    new={**base,'id':alternative,'date':'2007-04-19'}
    old={**base,'media':[dict(base['media'][0],
        **{'track-count':3,'tracks':base['media'][0]['tracks']+[
            {'position':3,'title':'Unreleased Demo','length':180000,
             'recording':{'title':'Unreleased Demo','id':'rec-3'}}]})]}
    monkeypatch.setattr(mb,'releases',lambda album_id:[old,new])
    monkeypatch.setattr(mb,'get',lambda entity,params=None:
                        old if entity=='release/'+old['id'] else new)
    spotify=FakeSpotify(source_album())
    playable=application.extensions['playable_releases']
    playable.spotify=spotify
    matcher=SpotifyMatching()
    matcher.start('owner',store,spotify,playable)
    job=completed(matcher,store)
    assert job['matched']==1
    selected=store.album(ALBUM)
    assert selected['playable'] and selected['on_shelf']
    assert selected['release_id']==alternative
    assert selected['canonical_reviewed']==0


def test_removed_or_fixed_album_drops_from_failure_report(application, monkeypatch):
    store = application.extensions['store']
    store.shelf(ALBUM, True)
    matcher, spotify = SpotifyMatching(), FakeSpotify(source_album(first='Opening - Live'))
    fake_original_releases(application,monkeypatch)
    application.extensions['playable_releases'].spotify=spotify
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



def test_background_verifies_alternate_release_after_unplayable_first_release(application, monkeypatch):
    from conftest import RELEASE
    store=application.extensions['store']
    store.shelf(ALBUM,True)
    assert store.clear_unverified_edition(ALBUM)
    spotify=FakeSpotify(source_album())
    playable=application.extensions['playable_releases']
    playable.spotify=spotify
    mb=application.extensions['musicbrainz']
    bonus='11111111-1111-4111-8111-111111111111'
    def release(identifier,bonus_track=False):
        tracks=[{'position':1,'title':'Opening','length':180000,
                 'recording':{'title':'Opening','id':'rec-1'}},
                {'position':2,'title':'Closing','length':240000,
                 'recording':{'title':'Closing','id':'rec-2'}}]
        if bonus_track:
            tracks.append({'position':3,'title':'Unreleased Demo','length':240000,
                           'recording':{'title':'Unreleased Demo','id':'rec-3'}})
        return {'id':identifier, 'title':'The Album','status':'Official',
                'date':'2007-04-18' if bonus_track else '2007-04-19',
                'country':'GB','release-group':{'id':ALBUM},
                'media':[{'position':1,'format':'CD','track-count':len(tracks),'tracks':tracks}]}
    variants=[release(bonus,True),release(RELEASE)]
    monkeypatch.setattr(mb,'releases',lambda album_id:variants)
    monkeypatch.setattr(mb,'get',lambda entity,params=None:
                        next(release for release in variants if entity=='release/'+release['id']))
    matcher=SpotifyMatching()
    matcher.start('owner',store,spotify,playable)
    job=completed(matcher,store)
    assert job['matched']==1
    saved=store.album(ALBUM)
    assert saved['release_id']==RELEASE
    assert len(saved['tracks'])==2
    assert saved['playable'] and not saved['canonical_reviewed']


def test_unresolved_album_stays_on_shelf_without_repopulating_unverified_tracks(application,client,monkeypatch):
    store=application.extensions['store']
    store.shelf(ALBUM,True)
    assert store.clear_unverified_edition(ALBUM)
    mb=application.extensions['musicbrainz']
    monkeypatch.setattr(mb,'ensure_tracks',lambda _id: (_ for _ in ()).throw(
        AssertionError('Must not repopulate unresolved shelf editions')))
    saved=client.get('/api/albums/'+ALBUM)
    assert saved.status_code==200
    assert saved.json['on_shelf'] is True
    assert saved.json['tracks']==[]
    assert saved.json['release_id'] is None
