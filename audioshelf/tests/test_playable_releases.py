"""Only completely verified Spotify mappings are presented for shelf editions."""
import copy

import pytest

from app.errors import AppError
from conftest import ALBUM, RELEASE, spotify_track, post

GOOD='11111111-1111-4111-8111-111111111111'
PARTIAL='22222222-2222-4222-8222-222222222222'
OTHER='33333333-3333-4333-8333-333333333333'
SPOTIFY='s'*22


def release(identifier, second='Closing', country='GB'):
    return {'id':identifier,'title':'The Album','date':'2007-04-23',
        'country':country,'status':'Official','release-group':{'id':ALBUM},
        'media':[{'format':'CD','position':1,'track-count':2,'tracks':[
            {'position':1,'title':'Opening','length':180000,'recording':{'id':'recording-1'}},
            {'position':2,'title':second,'length':240000,'recording':{'id':'recording-2'}}]}]}


@pytest.fixture
def editions(application,monkeypatch):
    mb=application.extensions['musicbrainz']
    spotify=application.extensions['spotify']
    store=application.extensions['store']
    spotify.tokens={'refresh_token':'fixture'}
    good=release(GOOD)
    partial=release(PARTIAL,'Unreleased bonus track')
    other=release(OTHER)
    all_releases=[good,partial,other]
    def get(entity,params=None):
        if entity=='release-group/'+ALBUM:
            return {'id':ALBUM,'title':'The Album','first-release-date':'2007-04-18',
                'primary-type':'Album','artist-credit':[{'artist':{
                    'id':'f181961b-20f7-459e-89de-920ef03c7ed0',
                    'name':'The Artist','sort-name':'Artist, The'}}]}
        if entity=='release':
            return {'releases':all_releases, 'release-count':len(all_releases)}
        if entity.startswith('release/'):
            return next(r for r in all_releases if r['id']==entity.split('/')[1])
        raise AssertionError(entity)
    monkeypatch.setattr(mb,'get',get)
    monkeypatch.setattr(spotify,'candidates',lambda album:[{'id':SPOTIFY}])
    monkeypatch.setattr(spotify,'album',lambda identifier:{
        'id':identifier,'name':'The Album (2024 Remaster)','release_date':'2024',
        'all_tracks':[spotify_track('Opening - 2024 Remaster','a'*22),
                      spotify_track('Closing - 2024 Remaster','b'*22,240000),
                      spotify_track('Bonus demo','c'*22)]})
    return store,all_releases


def test_picker_hides_partially_matched_musicbrainz_edition_and_does_not_mutate(application,client,editions):
    store,_=editions
    before=store.album(ALBUM)
    result=client.get('/api/albums/'+ALBUM+'/releases?playable=1&offset=0')
    assert result.status_code==200,result.json
    assert [r['id'] for r in result.json['releases']]==[GOOD,OTHER]
    assert all(r['matched_tracks']==2 and r['spotify_album_id']==SPOTIFY for r in result.json['releases'])
    assert result.json['checked']==3 and result.json['next_offset'] is None
    assert store.album(ALBUM)==before,'Preview must never mutate existing canonical tracks or mappings'


def test_shelf_add_rejects_incomplete_mapping_before_mutation(application,client,editions):
    store,_=editions
    before=store.album(ALBUM)
    response=post(client,'/api/albums/'+ALBUM+'/shelf',
                  {'release_id':PARTIAL,'spotify_album_id':SPOTIFY})
    assert response.status_code==400
    assert 'verified Spotify match' in response.json['error']
    assert store.album(ALBUM)==before,'Failed preflight must not add to shelf or erase mappings'


def test_shelf_add_commits_whole_album_mapping_and_requires_review(application,client,editions):
    store,_=editions
    result=post(client,'/api/albums/'+ALBUM+'/shelf',
                {'release_id':GOOD,'spotify_album_id':SPOTIFY})
    assert result.status_code==200,result.json
    album=result.json
    assert album['on_shelf'] and album['playable']
    assert album['canonical_reviewed']==0
    assert album['release_id']==GOOD and album['spotify_album_id']==SPOTIFY
    assert [t['spotify_id'] for t in album['tracks']]==['a'*22,'b'*22]
    assert all(t['verified'] for t in album['tracks'])
    assert store.album(ALBUM)['playable']
    assert post(client,'/api/albums/'+ALBUM+'/review').status_code==200
    assert store.album(ALBUM)['canonical_reviewed']==1


def test_change_tracklist_is_atomic_and_only_accepts_full_match(application,client,editions):
    store,_=editions
    before=copy.deepcopy(store.album(ALBUM))
    bad=post(client,'/api/albums/'+ALBUM+'/release',
             {'confirmed':True,'release_id':PARTIAL,'spotify_album_id':SPOTIFY})
    assert bad.status_code==400
    assert store.album(ALBUM)==before
    good=post(client,'/api/albums/'+ALBUM+'/release',
             {'confirmed':True,'release_id':GOOD,'spotify_album_id':SPOTIFY})
    assert good.status_code==200,good.json
    assert good.json['playable'] and good.json['canonical_reviewed']==1
    assert all(t['verified'] for t in good.json['tracks'])


def test_unconnected_spotify_has_clear_feedback_without_mutation(application,client):
    result=client.get('/api/albums/'+ALBUM+'/releases?playable=1')
    assert result.status_code==409
    assert 'Connect Spotify' in result.json['error']


def test_invalid_cursor_is_rejected(application,client,editions):
    for cursor in ('-1','9999','banana'):
        result=client.get('/api/albums/'+ALBUM+'/releases?playable=1&offset='+cursor)
        assert result.status_code==400


def test_later_batched_editions_can_be_discovered(application,client,editions,monkeypatch):
    store,items=editions
    mb=application.extensions['musicbrainz']
    many=[release(str(__import__('uuid').uuid5(__import__('uuid').NAMESPACE_DNS,str(i))), 'Not on Spotify')
          for i in range(4)]
    final=release(GOOD)
    monkeypatch.setattr(mb,'releases',lambda album_id:many+[final])
    monkeypatch.setattr(mb,'get',lambda entity,params=None:next(
        r for r in many+[final] if entity=='release/'+r['id']))
    first=client.get('/api/albums/'+ALBUM+'/releases?playable=1&offset=0')
    assert first.status_code==200
    assert first.json['releases']==[]
    assert first.json['checked']==4 and first.json['next_offset']==4
    second=client.get('/api/albums/'+ALBUM+'/releases?playable=1&offset=4')
    assert second.status_code==200
    assert [r['id'] for r in second.json['releases']]==[GOOD]
