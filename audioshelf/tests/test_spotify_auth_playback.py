import base64
import hashlib
import json
import time
from urllib.parse import parse_qs,urlsplit
from unittest.mock import Mock

import pytest

from app.errors import AppError
from app.spotify import Spotify,spotify_id
from conftest import ALBUM


def ready_album(application):
    store=application.extensions['store']
    with store.connect() as db:
        db.execute('UPDATE albums SET canonical_reviewed=1')
        db.execute('UPDATE tracks SET spotify_id=?,verified=1 WHERE position=1',('a'*22,))
        db.execute('UPDATE tracks SET spotify_id=?,verified=1 WHERE position=2',('b'*22,))
    return store.album(ALBUM)


def test_pkce_and_single_use_oauth_state(application,monkeypatch):
    spotify=application.extensions['spotify'];store=application.extensions['store']
    query=parse_qs(urlsplit(spotify.authorization()).query)
    assert query['code_challenge_method']==['S256']
    state=query['state'][0]
    with store.connect() as db:row=db.execute('SELECT * FROM oauth_states').fetchone()
    assert row['state']==hashlib.sha256(state.encode()).hexdigest()
    challenge=base64.urlsafe_b64encode(hashlib.sha256(row['verifier'].encode()).digest()).rstrip(b'=').decode()
    assert query['code_challenge']==[challenge]
    calls=[]
    def token(data):calls.append(data);return {'access_token':'private-access','refresh_token':'private-refresh','expires_in':3600}
    monkeypatch.setattr(spotify,'_token_request',token)
    spotify.callback('code',state)
    assert calls[0]['code_verifier']==row['verifier']
    assert spotify.token_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(AppError,match='already used'):spotify.callback('code',state)


def test_expired_oauth_is_consumed_without_exchange(application,monkeypatch):
    spotify=application.extensions['spotify'];store=application.extensions['store']
    query=parse_qs(urlsplit(spotify.authorization()).query)
    with store.connect() as db:db.execute('UPDATE oauth_states SET expires=0')
    exchange=Mock();monkeypatch.setattr(spotify,'_token_request',exchange)
    with pytest.raises(AppError):spotify.callback('code',query['state'][0])
    exchange.assert_not_called()


def test_refresh_preserves_old_refresh_token_and_survives_restart(application,monkeypatch):
    spotify=application.extensions['spotify']
    spotify._save({'access_token':'old','refresh_token':'keep-me','expires_in':0})
    monkeypatch.setattr(spotify,'_token_request',lambda data:{'access_token':'new','expires_in':3600})
    assert spotify.token()=='new'
    assert spotify.tokens['refresh_token']=='keep-me'
    loaded=Spotify(spotify.store,spotify.options,spotify.token_path.parent)
    assert loaded.connected and loaded.token()=='new'


def test_api_401_refreshes_once(application,monkeypatch):
    spotify=application.extensions['spotify'];spotify._save({'access_token':'old','refresh_token':'r'})
    responses=[Mock(status_code=401,ok=False),Mock(status_code=200,ok=True,content=b'{}')]
    responses[1].json.return_value={'ok':True}
    monkeypatch.setattr('app.spotify.requests.request',Mock(side_effect=responses))
    exchange=Mock(return_value={'access_token':'new','expires_in':3600});monkeypatch.setattr(spotify,'_token_request',exchange)
    assert spotify.api('GET','me/player')=={'ok':True}
    exchange.assert_called_once()


def test_playback_sends_only_exact_track_uris_and_turns_off_shuffle_repeat(application,monkeypatch):
    spotify=application.extensions['spotify'];album=ready_album(application);calls=[]
    states=iter([{'device':{'id':'device','name':'Phone'},'shuffle_state':True,'repeat_state':'context'},
                 {'device':{'id':'device'},'shuffle_state':False,'repeat_state':'off'}])
    def api(method,path,params=None,body=None):
        calls.append((method,path,params,body))
        return next(states) if method=='GET' else {}
    monkeypatch.setattr(spotify,'api',api);monkeypatch.setattr('app.spotify.time.sleep',lambda _:None)
    result=spotify.play(album)
    assert result['track_count']==2
    assert result['first_track']=={'id':'a'*22,'title':album['tracks'][0]['title'],'duration_ms':album['tracks'][0]['duration_ms']}
    assert calls[-1][1]=='me/player/play'
    assert calls[-1][3]=={'uris':['spotify:track:'+'a'*22,'spotify:track:'+'b'*22],'position_ms':0}
    assert [c[1] for c in calls]==['me/player','me/player/shuffle','me/player/repeat','me/player','me/player/play']


def test_unreviewed_tracklist_cannot_play(application,monkeypatch):
    album=ready_album(application);album['canonical_reviewed']=False
    api=Mock();monkeypatch.setattr(application.extensions['spotify'],'api',api)
    with pytest.raises(AppError,match='Review the MusicBrainz'):application.extensions['spotify'].play(album)
    api.assert_not_called()


def test_partial_mapping_cannot_play(application,monkeypatch):
    album=ready_album(application);album['playable']=False
    api=Mock();monkeypatch.setattr(application.extensions['spotify'],'api',api)
    with pytest.raises(AppError,match='Every canonical'):application.extensions['spotify'].play(album)
    api.assert_not_called()


def test_no_active_device_has_actionable_error(application,monkeypatch):
    spotify=application.extensions['spotify']
    monkeypatch.setattr(spotify,'api',lambda *args:{})
    with pytest.raises(AppError,match='Open Spotify'):spotify.play(ready_album(application))


def test_shuffle_not_acknowledged_does_not_start_album(application,monkeypatch):
    spotify=application.extensions['spotify'];calls=[]
    def api(method,path,params=None,body=None):
        calls.append(path);return {'device':{'id':'d'},'shuffle_state':True,'repeat_state':'off'}
    monkeypatch.setattr(spotify,'api',api);monkeypatch.setattr('app.spotify.time.sleep',lambda _:None)
    with pytest.raises(AppError,match='Shuffle'):spotify.play(ready_album(application))
    assert 'me/player/play' not in calls


def test_album_tracks_pagination_is_complete(application,monkeypatch):
    spotify=application.extensions['spotify'];calls=[]
    def api(method,path,params=None,body=None):
        calls.append(path)
        if path.endswith('/tracks'):return {'items':[{'id':'b'}],'next':None}
        return {'id':'a'*22,'name':'Album','tracks':{'items':[{'id':'a'}],'next':'https://api.spotify.com/v1/albums/x/tracks'}}
    monkeypatch.setattr(spotify,'api',api)
    assert [t['id'] for t in spotify.album('a'*22)['all_tracks']]==['a','b']
    assert len(calls)==2


@pytest.mark.parametrize('value',['a'*22,'spotify:album:'+'a'*22,'https://open.spotify.com/album/'+'a'*22+'?si=hello',
                                  'https://open.spotify.com/intl-de/album/'+'a'*22])
def test_spotify_album_link_formats(value):assert spotify_id(value,'album')=='a'*22


def test_untrusted_spotify_urls_not_fetched():
    with pytest.raises(AppError):spotify_id('https://evil.example/album/'+'a'*22,'album')


def test_candidate_search_reaches_new_remaster_on_next_page(application,monkeypatch):
    spotify=application.extensions['spotify'];album=application.extensions['store'].album(ALBUM);offsets=[]
    from conftest import spotify_track
    def api(method,path,params=None,body=None):
        offsets.append(params.get('offset',0))
        if params.get('offset',0)==0:return {'albums':{'items':[{'id':'o'*22}],'next':'next'}}
        return {'albums':{'items':[{'id':'n'*22}],'next':None}}
    def source(identifier):
        new=identifier[0]=='n';suffix=' - 2022 Remaster' if new else ''
        return {'id':identifier,'name':'The Album'+suffix,'release_date':'2022' if new else '2007',
            'all_tracks':[spotify_track('Opening'+suffix,'a'*22),spotify_track('Closing'+suffix,'b'*22,240000)]}
    monkeypatch.setattr(spotify,'api',api);monkeypatch.setattr(spotify,'album',source)
    assert spotify.candidates(album)[0]['id']=='n'*22
    assert offsets==[0,10]


def test_fallback_candidate_search_paginates_when_structured_search_is_empty(application, monkeypatch):
    spotify = application.extensions['spotify']; album = application.extensions['store'].album(ALBUM)
    calls = []
    from conftest import spotify_track
    def api(method, path, params=None, body=None):
        calls.append((params['q'], params['offset']))
        if params['q'].startswith('album:'):
            return {'albums': {'items': [], 'next': None}}
        offset = params['offset']
        return {'albums': {'items': [{'id': ('n' if offset == 20 else 'o')*22}], 'next': 'next' if offset < 20 else None}}
    def source(identifier):
        suffix = ' - 2022 Mix' if identifier[0] == 'n' else ''
        return {'id': identifier, 'name': 'The Album'+suffix, 'release_date': '2007', 'all_tracks': [
            spotify_track('Opening'+suffix, 'a'*22), spotify_track('Closing'+suffix, 'b'*22, 240000)]}
    monkeypatch.setattr(spotify, 'api', api); monkeypatch.setattr(spotify, 'album', source)
    assert spotify.candidates(album)[0]['id'] == 'n'*22
    assert [offset for _, offset in calls] == [0, 0, 10, 20]


def test_disc_playback_excludes_other_discs_and_allows_unmapped_other_disc(application,monkeypatch):
    spotify=application.extensions['spotify'];album=ready_album(application)
    album['tracks'][0]['disc_number']=1
    album['tracks'][1].update(disc_number=2,verified=False,spotify_id=None)
    album['playable']=False
    calls=[]
    def api(method,path,params=None,body=None):
        calls.append((path,body))
        return {'device':{'id':'device'},'shuffle_state':False,'repeat_state':'off'}
    monkeypatch.setattr(spotify,'api',api)
    assert spotify.play(album,disc_number=1)['track_count']==1
    assert calls[-1]==('me/player/play',{'uris':['spotify:track:'+'a'*22],'position_ms':0})
    calls.clear()
    with pytest.raises(AppError,match='verified'):spotify.play(album,disc_number=2)
    assert calls==[]


@pytest.mark.parametrize('disc',[0,-1,True,'1',1.5,3])
def test_invalid_disc_never_starts_playback(application,monkeypatch,disc):
    spotify=application.extensions['spotify'];api=Mock()
    monkeypatch.setattr(spotify,'api',api)
    with pytest.raises(AppError):spotify.play(ready_album(application),disc_number=disc)
    api.assert_not_called()


def test_preferred_device_transfer_preserves_exact_disc_queue(application,monkeypatch):
    spotify=application.extensions['spotify'];store=application.extensions['store']
    album=ready_album(application);album['tracks'][1]['disc_number']=2
    store.set_setting('preferred_device',{'id':'old-id','name':'Phone','type':'Smartphone'})
    states=iter([{'device':{'id':'speaker'},'shuffle_state':True,'repeat_state':'context'},
                 {'device':{'id':'new-id'},'shuffle_state':False,'repeat_state':'off'}])
    calls=[]
    def api(method,path,params=None,body=None):
        calls.append((method,path,params,body))
        if path=='me/player/devices':return {'devices':[{'id':'new-id','name':'Phone','type':'Smartphone','is_restricted':False}]}
        if method=='GET':return next(states)
        return {}
    monkeypatch.setattr(spotify,'api',api);monkeypatch.setattr('app.spotify.time.sleep',lambda _:None)
    result=spotify.play(album,disc_number=2)
    assert result['device']=='Phone'
    assert ('PUT','me/player',None,{'device_ids':['new-id'],'play':False}) in calls
    assert calls[-1]==('PUT','me/player/play',{'device_id':'new-id'},{'uris':['spotify:track:'+'b'*22],'position_ms':0})


@pytest.mark.parametrize('devices', [[],[{'id':'phone','name':'Phone','type':'Smartphone','is_restricted':True}],
    [{'id':'a','name':'Phone','type':'Smartphone'},{'id':'b','name':'Phone','type':'Smartphone'}]])
def test_unavailable_or_ambiguous_preference_never_plays_elsewhere(application,monkeypatch,devices):
    spotify=application.extensions['spotify'];application.extensions['store'].set_setting('preferred_device',{'id':'phone','name':'Phone','type':'Smartphone'})
    calls=[]
    def api(method,path,params=None,body=None):
        calls.append(method)
        return {'devices':devices} if path.endswith('devices') else {'device':{'id':'speaker'}}
    monkeypatch.setattr(spotify,'api',api)
    with pytest.raises(AppError) as failure:spotify.play(ready_album(application))
    assert failure.value.status==409
    assert 'PUT' not in calls


def test_preferred_device_transfer_timeout_never_starts_tracks(application,monkeypatch):
    spotify=application.extensions['spotify'];application.extensions['store'].set_setting('preferred_device',{'id':'phone','name':'Phone','type':'Smartphone'})
    calls=[]
    def api(method,path,params=None,body=None):
        calls.append(path)
        return {'devices':[{'id':'phone','name':'Phone','type':'Smartphone'}]} if path.endswith('devices') else {'device':{'id':'speaker'}}
    monkeypatch.setattr(spotify,'api',api);monkeypatch.setattr('app.spotify.time.sleep',lambda _:None)
    with pytest.raises(AppError,match='activated'):spotify.play(ready_album(application))
    assert 'me/player/play' not in calls
