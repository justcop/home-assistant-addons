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
        if path=='me/player':return {'device':PHONE,'shuffle_state':False,'repeat_state':'off',
                                    'is_playing':any(c[1]=='me/player/play' for c in calls),
                                    'item':{'id':'b'*22}}
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
    assert len(response.json['helper_token']) >= 40
    helper_path='/api/helper/playback-handoff/owner/'+response.json['id']
    assert client.get(helper_path).status_code==404
    assert client.get(helper_path,headers={'Authorization':'Bearer invalid'}).status_code==404
    helper_header={'Authorization':'Bearer '+response.json['helper_token']}
    unauthed=application.test_client()
    immediate=unauthed.get(helper_path+'?wait=0',headers=helper_header)
    assert immediate.status_code==200
    assert immediate.json['state']=='waiting'
    assert immediate.json['phase'] in ('checking_devices','waiting_for_device')
    assert 'helper_token' not in immediate.json and 'result' not in immediate.json
    # A valid helper token is verified after cancellation (no 20-second long-poll).
    path='/api/spotify/playback-handoff/'+response.json['id']
    assert client.get(path).json['state']=='waiting'
    # Separate authenticated owner session still cannot inspect another session's job.
    application.extensions['security'].password_hash='configured'
    outsider=application.test_client()
    assert outsider.get(path).status_code==401
    with pytest.raises(AppError):application.extensions['playback_handoff'].status(response.json['id'],'other-owner')
    assert post(client,path,method='DELETE').json['state']=='cancelled'
    assert unauthed.get(helper_path,headers=helper_header).json['state']=='cancelled'



def test_sdk_single_track_handoff_exposes_first_uri_only_to_trusted_helper_without_web_play(application, monkeypatch):
    spotify=application.extensions['spotify']
    album=ready_album(application)
    spotify_commands=[]
    playing=threading.Event()
    def api(method,path,params=None,body=None):
        spotify_commands.append((method,path,body))
        if path=='me/player/devices':
            return {'devices':[PHONE]}
        if path=='me/player':
            return ({'device':PHONE,'is_playing':True,'item':{'id':'a'*22}}
                    if playing.is_set() else {})
        if method!='GET':
            pytest.fail('SDK test must not issue Web API Play, transfer, queue or mode commands')
        return {}
    monkeypatch.setattr(spotify,'api',api)
    manager=PlaybackHandoff(spotify,timeout=2,interval=.01)
    job=manager.start(album,None,PHONE,'owner',lambda:True,sdk_single_track=True)
    uri=None
    deadline=time.monotonic()+1
    while time.monotonic()<deadline:
        helper=manager.helper_status(job['id'],job['helper_token'],wait=0)
        if helper['sdk_test_uri']:
            uri=helper['sdk_test_uri']
            break
        time.sleep(.005)
    assert uri=='spotify:track:'+'a'*22
    owner=manager.status(job['id'],'owner')
    assert 'sdk_test_uri' not in owner and 'sdk_single_track' not in owner
    assert owner['play_accepted'] is False
    assert 'spotify:track:' not in str(manager.snapshot(job))
    assert not any(method=='PUT' for method,_,__ in spotify_commands)
    playing.set()
    done=finished(manager,job)
    assert done['state']=='started' and done['result']['track_count']==1
    assert done['result']['sdk_single_track'] is True
    assert not any(method=='PUT' for method,_,__ in spotify_commands)
    assert manager.helper_status(job['id'],job['helper_token'],wait=0)['state']=='started'


def test_sdk_single_track_requires_phone_and_does_not_expose_uri_before_discovery(application, client, monkeypatch):
    spotify=application.extensions['spotify']
    store=application.extensions['store']
    ready_album(application)
    store.set_setting('preferred_device',SPEAKER)
    rejected=post(client,f'/api/albums/{ALBUM}/playback-handoff',
                  {'sdk_single_track':True})
    assert rejected.status_code==409
    store.set_setting('preferred_device',PHONE)
    monkeypatch.setattr(spotify,'devices',lambda:[SPEAKER])
    resp=post(client,f'/api/albums/{ALBUM}/playback-handoff',
              {'sdk_single_track':True})
    assert resp.status_code==202
    helper_path='/api/helper/playback-handoff/owner/'+resp.json['id']+'?wait=0'
    unauthed=application.test_client()
    assert unauthed.get(helper_path).status_code==404
    helper=unauthed.get(helper_path,headers={'Authorization':'Bearer '+resp.json['helper_token']})
    assert helper.status_code==200 and helper.json['sdk_test_uri'] is None
    assert helper.json['sdk_single_track'] is True
    assert 'sdk_test_uri' not in resp.json
    application.extensions['playback_handoff'].cancel_all()


def test_sdk_full_album_exposes_exact_canonical_sequence_without_web_play(application, monkeypatch):
    spotify=application.extensions['spotify']
    album=ready_album(application)
    started=threading.Event()
    calls=[]
    def api(method,path,params=None,body=None):
        calls.append((method,path))
        if path=='me/player/devices':return {'devices':[PHONE]}
        if path=='me/player':
            return {'device':PHONE,'is_playing':True,'item':{'id':'a'*22}} if started.is_set() else {}
        if method!='GET':pytest.fail('SDK album must not invoke Web API playback or queue')
        return {}
    monkeypatch.setattr(spotify,'api',api)
    manager=PlaybackHandoff(spotify,interval=.01,timeout=2)
    job=manager.start(album,None,PHONE,'owner',lambda:True,sdk_full_album=True)
    deadline=time.monotonic()+1
    helper={}
    while time.monotonic()<deadline:
        helper=manager.helper_status(job['id'],job['helper_token'],wait=0)
        if helper.get('sdk_track_uris'):break
        time.sleep(.005)
    expected=['spotify:track:'+t['spotify_id'] for t in album['tracks']]
    assert helper['sdk_track_uris']==expected
    assert helper['sdk_test_uri']==expected[0]
    assert 'sdk_track_uris' not in manager.status(job['id'],'owner')
    assert 'sdk_track_uris' not in str(manager.snapshot(job))
    assert not any(m=='PUT' for m,_ in calls)
    started.set()
    done=finished(manager,job)
    assert done['state']=='started'
    assert done['result']['sdk_full_album'] is True
    assert done['result']['track_count']==len(expected)
    assert not any(m=='PUT' for m,_ in calls)


def test_worker_stops_on_spotify_errors_without_repeating_queue():
    spotify=Mock();spotify.devices.return_value=[PHONE]
    spotify.play.side_effect=AppError('Spotify rate limit, retry later.',429)
    manager=PlaybackHandoff(spotify,interval=.005)
    job=manager.start({},None,PHONE,'owner',lambda:True)
    result=finished(manager,job)
    assert result['state']=='failed' and 'rate limit' in result['error']
    assert spotify.play.call_count==1


def test_cancellation_just_before_direct_play_prevents_queue(application, monkeypatch):
    spotify=application.extensions['spotify'];album=ready_album(application)
    activating=threading.Event();release=threading.Event();queues=[]
    def api(method,path,params=None,body=None):
        if path=='me/player/devices':return {'devices':[PHONE]}
        if path=='me/player/play':queues.append(body)
        if path=='me/player' and method=='PUT':
            pytest.fail('A direct phone handoff must not depend on a paused transfer')
        return {}
    monkeypatch.setattr(spotify,'api',api)
    manager=PlaybackHandoff(spotify,interval=.005)
    original_progress=manager.progress
    def progress(job,phase,*args,**kwargs):
        if phase=='preparing_explicit_play':
            activating.set()
            assert release.wait(2)
        return original_progress(job,phase,*args,**kwargs)
    manager.progress=progress
    job=manager.start(album,None,PHONE,'owner',lambda:True)
    assert activating.wait(1)
    assert manager.status(job['id'],'owner',cancel=True)['state']=='cancelled'
    release.set()
    manager.worker.join(1)
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

def test_wrong_track_or_wrong_device_never_falsely_confirm_playback(application, monkeypatch):
    spotify=application.extensions['spotify']; album=ready_album(application)
    phase={'track': 'wrong', 'device': 'phone', 'playing': True}
    def api(method, path, params=None, body=None):
        if path=='me/player/devices':return {'devices':[PHONE]}
        if path=='me/player':
            return {'device':{'id':phase['device']},'item':{'id':phase['track']},
                    'is_playing':phase['playing'],'shuffle_state':False,'repeat_state':'off'}
        if path=='me/player/play':phase.update(device='speaker',track='wrong')
        return {}
    monkeypatch.setattr(spotify,'api',api)
    manager=PlaybackHandoff(spotify,interval=.01)
    job=manager.start(album,None,PHONE,'owner',lambda:True)
    def await_phase(expected):
        deadline=time.monotonic()+1
        while time.monotonic()<deadline:
            status=manager.helper_status(job['id'],job['helper_token'],wait=0)
            if status['phase']==expected:
                assert status['state']=='waiting'
                return
            time.sleep(.005)
        pytest.fail('Did not receive helper phase: '+expected)
    await_phase('wrong_device')
    assert manager.status(job['id'],'owner')['state']=='waiting'
    phase['device']='phone'
    await_phase('wrong_track')
    assert manager.status(job['id'],'owner')['state']=='waiting'
    phase['track']='a'*22
    phase['playing']=False
    await_phase('paused')
    assert manager.status(job['id'],'owner')['state']=='waiting'
    phase['playing']=True
    assert finished(manager,job)['state']=='started'


def test_helper_token_revoked_on_job_replacement_and_cannot_access_other_jobs():
    spotify=Mock();spotify.devices.return_value=[]
    manager=PlaybackHandoff(spotify,interval=.01)
    first=manager.start({'id':'first'},None,PHONE,'owner',lambda:True)
    token=first['helper_token']
    assert manager.helper_status(first['id'],token,wait=0)['state']=='waiting'
    second=manager.start({'id':'second'},None,PHONE,'owner',lambda:True)
    with pytest.raises(AppError):manager.helper_status(first['id'],token,wait=0)
    with pytest.raises(AppError):manager.helper_status(second['id'],token,wait=0)
    assert manager.helper_status(second['id'],second['helper_token'],wait=0)['state']=='waiting'
    manager.cancel_all()


def test_helper_feedback_remains_readable_while_spotify_play_is_blocked():
    spotify=Mock();spotify.devices.return_value=[PHONE]
    sending=threading.Event();release=threading.Event();received=threading.Event();responses=[]
    result={'device_id':'phone','first_track':{'id':'track'}}
    def play(album,disc,preferred_device,guard,dispatch,resolved_device=None,progress=None):
        def send():
            sending.set()
            assert release.wait(2)
        dispatch(send)
        return result
    spotify.play.side_effect=play
    spotify.api.return_value={'device':PHONE,'item':{'id':'track'},'is_playing':True}
    manager=PlaybackHandoff(spotify,interval=.01)
    job=manager.start({},None,PHONE,'owner',lambda:True)
    def read():
        responses.append(manager.helper_status(job['id'],job['helper_token'],wait=0))
        received.set()
    try:
        assert sending.wait(1)
        reader=threading.Thread(target=read,daemon=True);reader.start()
        assert received.wait(.5), 'Helper feedback blocked behind a Spotify network request'
        assert responses[0]['state']=='waiting'
        assert responses[0]['phase']=='sending_play'
        assert responses[0]['device_checks']==1
    finally:
        release.set()
    assert finished(manager,job)['state']=='started'
    confirmed=manager.helper_status(job['id'],job['helper_token'],wait=0)
    assert confirmed['phase']=='confirmed'
    assert confirmed['confirmation_checks']==1


def test_play_acceptance_is_published_before_device_and_track_confirmation():
    spotify=Mock()
    def devices():
        time.sleep(.02)
        return [PHONE]
    spotify.devices.side_effect=devices
    in_flight=threading.Event(); release=threading.Event()
    def play(album,disc,preferred_device,guard,dispatch,resolved_device=None,progress=None):
        def send():
            in_flight.set()
            assert release.wait(2)
            return {}  # The Spotify PUT /me/player/play succeeded.
        dispatch(send)
        return {'device_id':'phone','first_track':{'id':'expected'}}
    spotify.play.side_effect=play
    spotify.api.return_value={'device':PHONE,'item':{'id':'old'},
                             'is_playing':False}
    manager=PlaybackHandoff(spotify,interval=.01)
    job=manager.start({},None,PHONE,'owner',lambda:True)
    try:
        assert in_flight.wait(1)
        pending=manager.helper_status(job['id'],job['helper_token'],wait=0)
        assert pending['state']=='waiting'
        assert pending['phase']=='sending_play'
        assert pending['play_accepted'] is False
        assert pending['device_seen_ms'] is not None
        assert pending['last_device_probe_ms'] >= 15
    finally:
        release.set()
    deadline=time.monotonic()+1
    while time.monotonic()<deadline:
        accepted=manager.helper_status(job['id'],job['helper_token'],wait=0)
        if accepted['play_accepted']:
            break
        time.sleep(.005)
    assert accepted['play_accepted'] is True
    assert accepted['state']=='waiting', 'Play acceptance is not playback confirmation'
    assert accepted['play_accepted_ms'] >= accepted['device_seen_ms']
    assert manager.status(job['id'],'owner')['state']=='waiting'
    manager.cancel_all()


def test_ambiguous_play_transport_failure_never_claims_accepted_or_requeues():
    spotify=Mock()
    spotify.devices.return_value=[PHONE]
    commands=[]
    def play(album,disc,preferred_device,guard,dispatch,resolved_device=None,progress=None):
        def send():
            commands.append('sent')
            raise AppError('Spotify transport failure',502)
        dispatch(send)
    spotify.play.side_effect=play
    manager=PlaybackHandoff(spotify,interval=.01)
    job=manager.start({},None,PHONE,'owner',lambda:True)
    result=finished(manager,job)
    helper=manager.helper_status(job['id'],job['helper_token'],wait=0)
    assert result['state']=='unconfirmed'
    assert helper['play_accepted'] is False
    assert len(commands)==1
    assert spotify.play.call_count==1


def test_cold_start_polling_is_fast_only_during_initial_phone_discovery():
    manager=PlaybackHandoff(Mock(),interval=2)
    assert manager.device_poll_interval(0)==.5
    assert manager.device_poll_interval(4.999)==.5
    assert manager.device_poll_interval(5)==1
    assert manager.device_poll_interval(14.999)==1
    assert manager.device_poll_interval(15)==2
    manager.interval=.01
    assert manager.device_poll_interval(0)==.01
    assert manager.device_poll_interval(7)==.01
    assert manager.device_poll_interval(60)==.01


def test_owner_snapshot_exposes_safe_progress_but_not_playback_capability():
    spotify=Mock()
    spotify.devices.return_value=[]
    manager=PlaybackHandoff(spotify,interval=.01)
    job=manager.start({'id':'test'},None,PHONE,'owner',lambda:True)
    try:
        snapshot=manager.status(job['id'],'owner')
        assert snapshot['state']=='waiting'
        assert snapshot['phase'] in ('checking_devices','waiting_for_device')
        assert snapshot['play_accepted'] is False
        assert 'helper_token' not in snapshot
        assert 'helper_token_hash' not in snapshot
        assert 'payload' not in snapshot
    finally:
        manager.cancel_all()

def test_spotify_player_confirmation_polling_is_fast_after_play_then_tapers():
    manager=PlaybackHandoff(Mock(),interval=2)
    assert manager.confirmation_poll_interval(0)==.5
    assert manager.confirmation_poll_interval(9.999)==.5
    assert manager.confirmation_poll_interval(10)==1
    assert manager.confirmation_poll_interval(19.999)==1
    assert manager.confirmation_poll_interval(20)==2
    manager.interval=.01
    assert manager.confirmation_poll_interval(0)==.01
    assert manager.confirmation_poll_interval(15)==.01
    assert manager.confirmation_poll_interval(60)==.01




def test_verification_observation_distinguishes_old_track_missing_item_and_linked_track():
    from unittest.mock import Mock
    manager=PlaybackHandoff(Mock())
    wanted='a'*22
    device='private-phone'
    observation=manager.verification_snapshot({
        'device':{'id':device,'name':'Private Phone','extra':'secret'},
        'item':{'id':'b'*22,'name':'Previous song','type':'track','uri':'spotify:track:secret'},
        'is_playing':True, 'timestamp':int(time.time()*1000)-12000,
        'access_token':'do-not-leak'
    },device,wanted,'New song')
    assert observation['requested']=='New song'
    assert observation['observed']=='Previous song'
    assert observation['device_matches'] and not observation['track_matches']
    assert observation['is_playing'] is True
    assert observation['player_age_ms']>=11000
    assert observation['requested_tag'] != observation['observed_tag']
    assert 'private-phone' not in str(observation) and 'do-not-leak' not in str(observation)
    assert 'spotify:track:' not in str(observation)

    missing=manager.verification_snapshot({'device':{'id':device}},device,wanted,'New song')
    assert not missing['item_present'] and missing['device_matches']
    assert missing['is_playing'] is None

    relinked=manager.verification_snapshot({
        'device':{'id':device},'is_playing':True,
        'item':{'id':'b'*22,'linked_from':{'id':wanted},'name':'New song'}
    },device,wanted,'New song')
    assert relinked['track_matches'] and relinked['linked_tag']==observation['requested_tag']


def test_helper_feedback_includes_bounded_track_mismatch_without_private_payloads():
    spotify=Mock()
    spotify.devices.return_value=[PHONE]
    state={'device':PHONE,'item':{'id':'b'*22,'name':'Previous song','type':'track'},
           'is_playing':True, 'shuffle_state':False, 'repeat_state':'off'}
    spotify.play.side_effect=lambda album,disc,preferred_device,guard,dispatch,resolved_device=None,progress=None: (
        dispatch(lambda:{}), {'device_id':'phone','first_track':{'id':'a'*22,'title':'New song'}})[1]
    spotify.api.side_effect=lambda method,path:dict(state)
    manager=PlaybackHandoff(spotify,timeout=1,interval=.01)
    job=manager.start({},None,PHONE,'owner',lambda:True)
    try:
        deadline=time.monotonic()+1
        while time.monotonic()<deadline:
            feedback=manager.helper_status(job['id'],job['helper_token'],wait=0)
            if feedback['phase']=='wrong_track' and feedback['verification']:
                break
            time.sleep(.005)
        assert feedback['phase']=='wrong_track'
        assert feedback['verification']['requested']=='New song'
        assert feedback['verification']['observed']=='Previous song'
        assert not feedback['verification']['track_matches']
        assert 'payload' not in feedback and 'helper_token' not in feedback
    finally:
        manager.cancel_all()



def test_cancel_reason_identifies_explicit_user_action_but_not_untrusted_text(application, client, monkeypatch):
    spotify=application.extensions['spotify']
    store=application.extensions['store']
    ready_album(application)
    store.set_setting('preferred_device',PHONE)
    monkeypatch.setattr(spotify,'devices',lambda:[SPEAKER])
    path=f'/api/albums/{ALBUM}/playback-handoff'
    request=post(client,path)
    assert request.status_code==202
    job=request.json['id']
    bearer=request.json['helper_token']
    url=f'/api/spotify/playback-handoff/{job}'
    cancelled=post(client,url,{'reason':'user_cancelled'},method='DELETE')
    assert cancelled.status_code==200
    assert cancelled.json['state']=='cancelled'
    assert cancelled.json['cancel_reason']=='user_cancelled'
    helper=application.test_client().get(
        f'/api/helper/playback-handoff/owner/{job}?wait=0',
        headers={'Authorization':'Bearer '+bearer})
    assert helper.status_code==200
    assert helper.json['state']=='cancelled'
    assert helper.json['cancel_reason']=='user_cancelled'
    assert 'payload' not in helper.json

    following=post(client,path)
    url=f"/api/spotify/playback-handoff/{following.json['id']}"
    spoof=post(client,url,{'reason':'private-token-should-never-appear'},method='DELETE')
    assert spoof.json['cancel_reason']=='cancelled'
    assert 'private-token' not in str(spoof.json)


def test_new_job_replaces_prior_job_with_safe_reason():
    spotify=Mock()
    spotify.devices.return_value=[]
    manager=PlaybackHandoff(spotify,interval=.005)
    first=manager.start({'id':'one'},None,PHONE,'owner',lambda:True)
    original_job=manager.job
    same_token=first['helper_token']
    another=manager.start({'id':'two'},None,PHONE,'owner',lambda:True)
    assert original_job['state']=='cancelled'
    assert original_job['cancel_reason']=='new_playback'
    assert manager.job['id']==another['id']
    with pytest.raises(AppError):
        manager.helper_status(first['id'],same_token,wait=0)
    manager.cancel_all()


def test_handoff_reuses_first_verified_device_without_second_discovery(application, monkeypatch):
    spotify=application.extensions['spotify'];album=ready_album(application)
    first=[PHONE];probes=[];queues=[]
    def api(method,path,params=None,body=None):
        if path=='me/player/devices':
            probes.append('device-list')
            if len(probes)>1:
                assert queues, 'Only the read-only post-Play audit may probe again; never block initial Play'
            return {'devices':first if len(probes)==1 else []}
        if path=='me/player' and method=='GET':
            return {'device':PHONE,'shuffle_state':False,'repeat_state':'off',
                    'is_playing':bool(queues),'item':{'id':'a'*22}}
        if path=='me/player/play':
            queues.append((params,body))
        return {}
    monkeypatch.setattr(spotify,'api',api)
    manager=PlaybackHandoff(spotify,interval=.01)
    job=manager.start(album,None,PHONE,'owner',lambda:True)
    assert finished(manager,job)['state']=='started'
    assert probes and len(probes)<=2, 'No duplicate pre-Play discovery is permitted'
    assert len(queues)==1
    assert queues[0][0]=={'device_id':'phone'}
    assert queues[0][1]['uris']==['spotify:track:'+'a'*22, 'spotify:track:'+'b'*22]


def test_handoff_direct_play_skips_stale_transfer_and_corrects_modes(application,monkeypatch):
    spotify=application.extensions['spotify'];album=ready_album(application)
    calls=[];state={'playing':False,'shuffle':True,'repeat':'context'}
    def api(method,path,params=None,body=None):
        calls.append((method,path,params,body))
        if path=='me/player/devices':return {'devices':[PHONE]}
        if path=='me/player' and method=='PUT':
            pytest.fail('Paused Spotify transfer should not block a known phone')
        if path=='me/player' and method=='GET':
            assert state['playing'], 'A stale current-player read must not precede Play'
            return {'device':PHONE,'is_playing':True,'item':{'id':'a'*22},
                    'shuffle_state':state['shuffle'],'repeat_state':state['repeat']}
        if path=='me/player/play':
            assert params=={'device_id':'phone'}
            assert body=={'uris':['spotify:track:'+'a'*22,'spotify:track:'+'b'*22],
                          'position_ms':0}
            state['playing']=True
        if path=='me/player/shuffle':
            assert params=={'device_id':'phone','state':'false'}
            state['shuffle']=False
        if path=='me/player/repeat':
            assert params=={'device_id':'phone','state':'off'}
            state['repeat']='off'
        return {}
    monkeypatch.setattr(spotify,'api',api)
    manager=PlaybackHandoff(spotify,timeout=2,interval=.01)
    job=manager.start(album,None,PHONE,'owner',lambda:True)
    assert finished(manager,job)['state']=='started'
    assert [c[1] for c in calls].count('me/player/play')==1
    assert [c[1] for c in calls].count('me/player/shuffle')==1
    assert [c[1] for c in calls].count('me/player/repeat')==1
    assert state['shuffle'] is False and state['repeat']=='off'
    assert [c[1] for c in calls].index('me/player/play') < [c[1] for c in calls].index('me/player/shuffle')


def test_handoff_mode_retry_does_not_repeat_accepted_play(application,monkeypatch):
    spotify=application.extensions['spotify'];album=ready_album(application)
    calls=[];state={'shuffle':True,'repeat':'context','shuffle_attempts':0}
    def api(method,path,params=None,body=None):
        calls.append((method,path,params,body))
        if path=='me/player/devices':return {'devices':[PHONE]}
        if path=='me/player' and method=='GET':
            return {'device':PHONE,'is_playing':True,'item':{'id':'a'*22},
                    'shuffle_state':state['shuffle'],'repeat_state':state['repeat']}
        if path=='me/player/shuffle':
            state['shuffle_attempts']+=1
            if state['shuffle_attempts']==1:
                raise AppError('Phone warming up',409)
            state['shuffle']=False
        if path=='me/player/repeat':state['repeat']='off'
        return {}
    monkeypatch.setattr(spotify,'api',api)
    manager=PlaybackHandoff(spotify,timeout=3,interval=.01)
    job=manager.start(album,None,PHONE,'owner',lambda:True)
    assert finished(manager,job)['state']=='started'
    assert state['shuffle_attempts']==2
    assert [c[1] for c in calls].count('me/player/play')==1
