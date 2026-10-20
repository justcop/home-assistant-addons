"""Playback diagnostics must inspect the *actual* queued Spotify IDs without changing music."""
import time
from unittest.mock import Mock

from app.errors import AppError
from app.playback import PlaybackHandoff
from app.playback_audit import catalogue_entry, inspect_dispatched_tracks, tag
from test_playback_handoff import PHONE, finished
from test_spotify_auth_playback import ready_album


def test_catalogue_probe_reports_playability_relinking_and_restrictions_without_raw_ids():
    track={'spotify_id':'a'*22,'title':'First Song'}
    available=catalogue_entry(track,1,item={'id':'b'*22,'name':'First Song',
        'is_playable':True,'linked_from':{'id':'a'*22}})
    assert available['status']=='found'
    assert available['playable']=='yes' and available['relinked']
    assert available['saved_tag']==tag('a'*22)
    assert available['returned_tag']==tag('b'*22)
    assert 'a'*22 not in str(available) and 'b'*22 not in str(available)
    restricted=catalogue_entry(track,1,item={'id':'a'*22,'name':'First Song',
        'is_playable':False,'restrictions':{'reason':'market'}})
    assert restricted['playable']=='no'
    assert restricted['restriction']=='market' and not restricted['relinked']
    unknown=catalogue_entry(track,1,item={'id':'a'*22,'name':'First Song'})
    assert unknown['playable']=='unknown'
    missing=catalogue_entry(track,1,error=AppError('private data',404))
    assert missing['status']=='not_found' and 'private data' not in str(missing)


def test_post_play_catalogue_audit_samples_actual_canonical_queue_without_control_commands():
    albums=[{'title':title,'spotify_id':letter*22}
            for letter,title in zip('abcd',['One','Two','Three','Four'])]
    expected=['spotify:track:'+row['spotify_id'] for row in albums]
    spotify=Mock()
    spotify.market='GB'
    spotify.play_tracks.return_value=(albums, expected)
    queries=[]
    def api(method,path,params=None):
        queries.append((method,path,params))
        letter=path.split('/')[-1][0]
        return {'id':letter*22,'name':next(row['title'] for row in albums
            if row['spotify_id']==letter*22),'is_playable':True}
    spotify.api.side_effect=api
    spotify.devices.return_value=[{'id':'phone','is_active':False}]
    snapshots=[]
    inspect_dispatched_tracks(spotify,{},None,'phone',snapshots.append)
    result=snapshots[-1]
    assert result['state']=='complete' and result['track_count']==4
    assert result['queue_tag']==tag('|'.join(expected))
    assert [s['position'] for s in result['samples']]==[1,2,4]
    assert all(s['playable']=='yes' for s in result['samples'])
    assert result['phone_visible_after_play']=='yes'
    assert result['phone_active_after_play']=='no'
    assert [q[1] for q in queries]==['tracks/'+letter*22 for letter in 'abd']
    assert all(q[0]=='GET' and q[2]=={'market':'GB'} for q in queries)
    assert 'spotify:track:' not in str(result)
    spotify.play_tracks.assert_called_once_with({},None)


def test_catalogue_api_failure_cannot_interrupt_accepted_play(application,monkeypatch):
    spotify=application.extensions['spotify']
    album=ready_album(application)
    sent=[];requested=[]
    def api(method,path,params=None,body=None):
        requested.append((method,path))
        if path=='me/player/devices':
            return {'devices':[PHONE]}
        if path=='me/player/play':
            sent.append(body)
            return {}
        if path=='me/player':
            return {'device':PHONE,'is_playing':bool(sent),'item':{'id':'a'*22},
                'shuffle_state':False,'repeat_state':'off'}
        if path.startswith('tracks/'):
            raise AppError('untrusted private data',404)
        return {}
    monkeypatch.setattr(spotify,'api',api)
    manager=PlaybackHandoff(spotify,interval=.01)
    job=manager.start(album,None,PHONE,'owner',lambda:True)
    assert finished(manager,job)['state']=='started'
    deadline=time.monotonic()+1
    audit=None
    while time.monotonic()<deadline:
        audit=manager.helper_status(job['id'],job['helper_token'],wait=0)['track_audit']
        if audit and audit.get('state')=='complete':break
        time.sleep(.01)
    assert audit['state']=='complete'
    assert [s['status'] for s in audit['samples']]==['not_found','not_found']
    assert len(sent)==1
    assert 'untrusted private data' not in str(audit)
    assert audit['phone_visible_after_play']=='yes'
    assert manager.status(job['id'],'owner')['state']=='started'
