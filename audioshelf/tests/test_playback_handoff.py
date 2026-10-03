import threading
import time
from unittest.mock import Mock

import pytest

from app.errors import AppError
from app.playback import PlaybackHandoff
from conftest import ALBUM, post
from test_spotify_auth_playback import ready_album

PHONE = {'id':'phone','name':'Phone','type':'Smartphone'}
SPEAKER = {'id':'speaker','name':'Speaker','type':'Speaker','is_active':True}


def finished(manager, job):
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        state=manager.status(job['id'],'owner')
        if state['state']!='waiting':
            return state
        time.sleep(.005)
    pytest.fail('Background playback did not finish')


def test_waits_for_snapshot_phone_then_dispatches_exact_disc_once(application, monkeypatch):
    spotify=application.extensions['spotify'];album=ready_album(application)
    album['tracks'][1]['disc_number']=2
    available=threading.Event();calls=[]
    def api(method,path,params=None,body=None):
        calls.append((method,path,params,body))
        if path=='me/player/devices':return {'devices':[PHONE] if available.is_set() else [SPEAKER]}
        if path=='me/player':return {'device':PHONE,'shuffle_state':False,'repeat_state':'off'}
        return {}
    monkeypatch.setattr(spotify,'api',api)
    manager=PlaybackHandoff(spotify,interval=.01)
    job=manager.start(album,2,PHONE,'owner',lambda:True)
    time.sleep(.025)
    assert not any(c[0]=='PUT' for c in calls)
    # Changes in global preference and tracklist cannot redirect an accepted request.
    application.extensions['store'].set_setting('preferred_device',SPEAKER)
    album['tracks'][1]['spotify_id']='c'*22
    available.set()
    assert finished(manager,job)['state']=='started'
    queues=[c for c in calls if c[1]=='me/player/play']
    assert len(queues)==1
    assert queues[0][2]=={'device_id':'phone'}
    assert queues[0][3]=={'uris':['spotify:track:'+'b'*22],'position_ms':0}


@pytest.mark.parametrize('stop',['cancel','replace','revoke','expire'])
def test_cancel_replacement_revocation_and_expiry_prevent_dispatch(stop):
    available=threading.Event();allowed=threading.Event();allowed.set()
    spotify=Mock();spotify.devices.side_effect=lambda:[PHONE] if available.is_set() else [SPEAKER]
    manager=PlaybackHandoff(spotify,timeout=.06 if stop=='expire' else 1,interval=.005)
    job=manager.start({'id':'album'},None,PHONE,'owner',allowed.is_set)
    if stop=='cancel':manager.status(job['id'],'owner',cancel=True)
    if stop=='replace':
        manager.start({'id':'new'},None,PHONE,'owner',lambda:False)
        with pytest.raises(AppError):manager.status(job['id'],'owner')
    if stop=='revoke':allowed.clear()
    if stop in ('expire','revoke'):assert finished(manager,job)['state']=='expired'
    available.set();time.sleep(.02)
    spotify.play.assert_not_called()
    manager.cancel_all()


def test_handoff_routes_validate_and_keep_status_private(application,client,monkeypatch):
    spotify=application.extensions['spotify'];store=application.extensions['store']
    ready_album(application);store.set_setting('preferred_device',PHONE)
    monkeypatch.setattr(spotify,'devices',lambda:[SPEAKER])
    assert post(client,f'/api/albums/{ALBUM}/playback-handoff',{'disc_number':3}).status_code==400
    response=post(client,f'/api/albums/{ALBUM}/playback-handoff',{'disc_number':1})
    assert response.status_code==202
    path='/api/spotify/playback-handoff/'+response.json['id']
    assert client.get(path).json['state']=='waiting'
    # Separate authenticated owner session still cannot inspect another session's job.
    application.extensions['security'].password_hash='configured'
    outsider=application.test_client()
    assert outsider.get(path).status_code==401
    with pytest.raises(AppError):application.extensions['playback_handoff'].status(response.json['id'],'other-owner')
    assert post(client,path,method='DELETE').json['state']=='cancelled'


def test_worker_stops_on_spotify_errors_without_repeating_queue():
    spotify=Mock();spotify.devices.return_value=[PHONE]
    spotify.play.side_effect=AppError('Spotify rate limit, retry later.',429)
    manager=PlaybackHandoff(spotify,interval=.005)
    job=manager.start({},None,PHONE,'owner',lambda:True)
    result=finished(manager,job)
    assert result['state']=='failed' and 'rate limit' in result['error']
    assert spotify.play.call_count==1


def test_cancellation_during_device_activation_prevents_queue(application,monkeypatch):
    spotify=application.extensions['spotify'];album=ready_album(application)
    activating=threading.Event();release=threading.Event();queues=[]
    def api(method,path,params=None,body=None):
        if path=='me/player/devices':return {'devices':[PHONE]}
        if path=='me/player' and method=='GET':
            return {'device':PHONE if release.is_set() else SPEAKER,'shuffle_state':False,'repeat_state':'off'}
        if path=='me/player' and method=='PUT':
            activating.set();release.wait(2)
        if path=='me/player/play':queues.append(body)
        return {}
    monkeypatch.setattr(spotify,'api',api)
    monkeypatch.setattr('app.spotify.time.sleep',lambda _:None)
    manager=PlaybackHandoff(spotify,interval=.005)
    job=manager.start(album,None,PHONE,'owner',lambda:True)
    assert activating.wait(1)
    assert manager.status(job['id'],'owner',cancel=True)['state']=='cancelled'
    worker=manager.worker
    release.set()
    worker.join(1)
    assert not queues


def test_revoking_real_session_cancels_server_job(application,monkeypatch):
    from app.server import create_app
    store=application.extensions['store'];ready_album(application)
    app=create_app({'data_directory':str(store.directory), 'private_directory':str(application.extensions['spotify'].token_path.parent), 'web_password':'owner-password'})
    spotify=app.extensions['spotify'];manager=app.extensions['playback_handoff']
    manager.interval=.005
    store.set_setting('preferred_device',PHONE)
    monkeypatch.setattr(spotify,'devices',lambda:[SPEAKER])
    client=app.test_client();security=app.extensions['security']
    token=security.create_session()
    with client.session_transaction() as session:session['sid']=token
    response=post(client,f'/api/albums/{ALBUM}/playback-handoff')
    assert response.status_code==202
    security.logout(token)
    deadline=time.monotonic()+1
    while manager.job['state']=='waiting' and time.monotonic()<deadline:time.sleep(.005)
    assert manager.job['state']=='expired'
    assert client.get('/api/spotify/playback-handoff/'+response.json['id']).status_code==401
