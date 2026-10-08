"""Spotify Now Playing respects album canonicalisation, account settings and playback control."""
from unittest.mock import Mock

import pytest

from app.now_playing import canonical_title, resolve_album, spotify_artwork
from conftest import ALBUM, ARTIST


def spotify_track(album_name='The Album (Deluxe Edition)', album_id='x'*22):
    return {'id': 'a'*22, 'name': 'Opening', 'artists': [{'name':'The Artist'}],
            'album': {'id':album_id, 'name':album_name, 'artists':[{'name':'The Artist'}],
                      'images':[{'url':'https://i.scdn.co/image/cover320', 'width':320},
                                {'url':'https://i.scdn.co/image/cover640', 'width':640}]}}


@pytest.mark.parametrize(('source','expected'),[
    ('Revolver (2022 Remaster)','Revolver'),
    ('The Album (Deluxe Edition)','The Album'),
    ('The Album - 2019 Remaster','The Album'),
    ('Another Country','Another Country'),
])
def test_strip_edition_without_changing_canonical_album(source,expected):
    assert canonical_title(source)==expected


def test_spotify_album_images_are_selected_and_untrusted_urls_excluded():
    assert spotify_artwork([{'url':'http://i.scdn.co/image/bad','width':300},
                            {'url':'https://attacker.example/img','width':320},
                            {'url':'https://i.scdn.co/image/640','width':640},
                            {'url':'https://i.scdn.co/image/320','width':320}])=='https://i.scdn.co/image/320'
    assert spotify_artwork([{'url':'javascript:alert(1)','width':300}]) is None


def test_verified_studio_track_opens_its_own_shelf(application):
    store=application.extensions['store']
    store.shelf(ALBUM,True)
    with store.connect() as db:
        db.execute("UPDATE tracks SET spotify_id=?, verified=1 WHERE album_id=? AND position=1",('a'*22,ALBUM))
    mb=Mock()
    assert resolve_album(store,mb,spotify_track())=={'view':'album','album_id':ALBUM,'on_shelf':True}
    mb.search.assert_not_called()


def test_external_studio_album_opens_record_store_and_reuses_canonical_group(application):
    store=application.extensions['store']
    other='33fa21bb-8f16-4666-ad4d-8e0b1646e5e0'
    store.catalogue([{'id':other,'title':'Brand New Sound','first-release-date':'2024-01-01',
                      'artist-credit':[{'artist':{'id':ARTIST,'name':'The Artist'}}]}])
    mb=Mock(search=Mock(return_value=[store.album(other)]))
    target=resolve_album(store,mb,spotify_track('Brand New Sound (Deluxe Edition)'))
    assert target=={'view':'album','album_id':other,'on_shelf':False}
    mb.search.assert_called_once_with('Brand New Sound','album')


def test_live_album_does_not_redirect_to_original_studio_album(application):
    store=application.extensions['store']
    store.shelf(ALBUM,True)
    with store.connect() as db:
        db.execute("UPDATE tracks SET spotify_id=?, verified=1 WHERE album_id=? AND position=1",('a'*22,ALBUM))
    mb=Mock()
    target=resolve_album(store,mb,spotify_track('Live at the City Hall'))
    assert target=={'view':'store-search','query':'Live at the City Hall'}
    mb.search.assert_not_called()


def test_same_title_different_artist_cannot_open_a_wrong_album(application):
    store=application.extensions['store']
    mb=Mock(search=Mock(return_value=[store.album(ALBUM)]))
    track=spotify_track()
    track['album']['artists']=[{'name':'Another Band'}]
    assert resolve_album(store,mb,track)=={'view':'store-search','query':'The Album'}


def test_settings_default_and_skip_control_authorisation(client,application,monkeypatch):
    spotify=application.extensions['spotify']
    spotify._save({'access_token':'test','refresh_token':'test','expires_in':3600})
    requests=[]
    monkeypatch.setattr(spotify,'api',lambda method,path,params=None,body=None: requests.append((method,path)) or {})
    headers={'X-AudioShelf-Request':'1'}
    assert client.get('/api/status').json['show_skip_controls'] is False
    assert client.get('/api/settings').json['show_skip_controls'] is False
    assert client.post('/api/spotify/control',json={'action':'next'},headers=headers).status_code==403
    assert client.put('/api/settings',json={'show_skip_controls':1},headers=headers).status_code==400
    assert client.put('/api/settings',json={'show_skip_controls':True},headers=headers).json['show_skip_controls'] is True
    for action,method,path in [('pause','PUT','me/player/pause'),('resume','PUT','me/player/play'),
                               ('next','POST','me/player/next'),('previous','POST','me/player/previous')]:
        assert client.post('/api/spotify/control',json={'action':action},headers=headers).status_code==200
        assert requests[-1]==(method,path)
    assert client.post('/api/spotify/control',json={'action':'delete'},headers=headers).status_code==400
    assert client.put('/api/settings',json={'show_skip_controls':False},headers=headers).json['show_skip_controls'] is False
    assert client.post('/api/spotify/control',json={'action':'pause'},headers=headers).status_code==200
    assert client.post('/api/spotify/control',json={'action':'next'},headers=headers).status_code==403


def test_now_playing_artwork_and_album_resolution_for_uncollected_record(client,application,monkeypatch):
    spotify=application.extensions['spotify']
    spotify._save({'access_token':'test','refresh_token':'test','expires_in':3600})
    track=spotify_track('The Album (Deluxe Edition)')
    state={'item':track,'currently_playing_type':'track','is_playing':True,'progress_ms':12000,
           'device':{'name':'Phone'}}
    monkeypatch.setattr(spotify,'api',lambda method,path,params=None,body=None: state)
    info=client.get('/api/spotify/playback').json
    assert info['active'] is True and info['album_id'] is None
    assert info['spotify_album_id']=='x'*22
    assert info['artwork_url']=='https://i.scdn.co/image/cover320'
    monkeypatch.setattr(application.extensions['musicbrainz'],'search',
                        lambda title,kind:[application.extensions['store'].album(ALBUM)])
    resolved=client.get('/api/spotify/album-destination')
    assert resolved.json=={'view':'album','album_id':ALBUM,'on_shelf':False}
