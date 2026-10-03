"""Account boundaries are tested through requests, including identical album IDs."""
import base64
import hashlib
import io
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from app.server import create_app
from app.security import totp
from conftest import ALBUM, ARTIST, RELEASE, post

OWNER = 'existing-owner-password'
ALICE = 'alice-password-long-enough'
BOB = 'bob-password-long-enough'


def signin(client, username='owner', password=OWNER, **extra):
    return post(client, '/api/login', {'username':username, 'password':password, **extra})


def seed(context, title):
    context.store.catalogue([{'id':ALBUM, 'title':title, 'first-release-date':'2007-04-18',
        'primary-type':'Album', 'artist-credit':[{'artist':{'id':ARTIST,'name':'The Artist','sort-name':'Artist'}}]}])
    context.store.set_tracks(ALBUM, {'id':RELEASE,'label':'GB CD'}, [
        {'title':title+' track','disc_number':1,'track_number':1,'duration_ms':180000}], reviewed=True)
    context.store.shelf(ALBUM, True)


@pytest.fixture
def users(tmp_path):
    options={'data_directory':str(tmp_path/'collection'), 'cache_directory':str(tmp_path/'cache'),
        'private_directory':str(tmp_path/'private'), 'web_password':OWNER, 'allow_support_access':True,
        'spotify_client_id':'test', 'spotify_redirect_uri':'https://audioshelf.example/auth/spotify/callback'}
    app=create_app(options); app.config['TESTING']=True
    owner=app.test_client(); assert signin(owner).status_code==200
    accounts=app.extensions['accounts']
    identifiers={}
    for name,password in [('alice',ALICE),('bob',BOB)]:
        result=post(owner,'/api/accounts',{'password':OWNER,'username':name,'new_password':password})
        assert result.status_code==201
        identifiers[name]=result.json['id']
    alice=app.test_client(); bob=app.test_client()
    assert signin(alice,'alice',ALICE).status_code==200
    assert signin(bob,'bob',BOB).status_code==200
    return app,owner,alice,bob,identifiers,options


def test_existing_collection_tokens_and_security_stay_with_owner(users):
    app,owner,alice,_,_,options=users
    context=app.extensions['accounts'].owner
    seed(context,'Legacy library')
    context.spotify._save({'access_token':'old-access','refresh_token':'old-refresh','expires_in':3600})
    context.security.set('totp','JBSWY3DPEHPK3PXP')
    restarted=create_app(options)
    assert restarted.extensions['store'].album(ALBUM)['title']=='Legacy library'
    assert restarted.extensions['spotify'].tokens['refresh_token']=='old-refresh'
    assert restarted.extensions['security'].get('totp')=='JBSWY3DPEHPK3PXP'
    assert restarted.extensions['accounts'].owner.store.directory==context.store.directory
    assert alice.get('/api/shelf').json['albums']==[]
    assert alice.get('/api/status').json['spotify_connected'] is False


def test_account_creation_and_management_require_admin_and_fresh_password(users):
    app,owner,alice,_,identifiers,_=users
    for client in (app.test_client(),alice):
        assert post(client,'/api/accounts',{'username':'eve','new_password':ALICE}).status_code in (401,403)
        assert client.get('/api/accounts').status_code in (401,403)
        assert post(client,'/api/accounts/'+identifiers['bob'],{'disabled':True},method='PUT').status_code in (401,403)
    assert post(owner,'/api/accounts',{'username':'eve','new_password':ALICE}).status_code==401
    assert post(owner,'/api/accounts',{'password':OWNER,'username':'ALICE','new_password':ALICE}).status_code==409
    listing=owner.get('/api/accounts').json['accounts']
    assert all(set(row)=={'id','username','disabled'} for row in listing)
    assert post(owner,'/api/accounts/owner',{'password':OWNER,'disabled':True},method='PUT').status_code==400


@pytest.mark.parametrize('username,password',[('../alice',ALICE),('',ALICE),('a'*33,ALICE),('new','short'),('with space',ALICE)])
def test_invalid_account_inputs_do_not_create_accounts(users,username,password):
    app,owner,_,_,_,_=users
    assert post(owner,'/api/accounts',{'password':OWNER,'username':username,'new_password':password}).status_code==400
    assert len(app.extensions['accounts'].listing())==3


def test_credentials_and_signed_sessions_cannot_cross_accounts(users):
    app,_,alice,bob,identifiers,_=users
    assert signin(app.test_client(),'alice',BOB).status_code==401
    assert signin(app.test_client(),'unknown',ALICE).status_code==401
    assert signin(app.test_client(),'ALICE',ALICE).status_code==200
    with alice.session_transaction() as session:
        token=session['sid']; session['account']=identifiers['bob']
    assert app.extensions['accounts'].context(identifiers['bob']).security.identity(token) is None
    assert alice.get('/api/shelf').status_code==401
    assert bob.get('/api/shelf').status_code==200


def test_library_preferences_mappings_diagnostics_and_exports_are_isolated(users):
    app,owner,alice,bob,ids,_=users
    contexts=[app.extensions['accounts'].owner,app.extensions['accounts'].context(ids['alice']),app.extensions['accounts'].context(ids['bob'])]
    for n,(client,context) in enumerate(zip((owner,alice,bob),contexts)):
        seed(context,f'Library {n}')
        context.store.set_setting('preferred_device',{'id':str(n),'name':str(n),'type':'Computer'})
        context.store.set_setting('last_played_album',f'last-{n}')
        context.store.diagnostic(ALBUM,'own event',{'account':n})
        with context.store.connect() as db:
            db.execute('UPDATE tracks SET spotify_id=?,verified=1 WHERE album_id=?', (str(n)*22,ALBUM))
        assert client.get('/api/shelf').json['albums'][0]['title']==f'Library {n}'
        assert client.get('/api/export').json['albums'][0]['tracks'][0]['spotify_id']==str(n)*22
        assert client.get('/api/status').json['preferred_device']['id']==str(n)
        report=client.get(f'/api/albums/{ALBUM}/diagnostics').json
        assert f'Library {n}' in json.dumps(report)
    assert post(alice,'/api/settings',{'theme':'midnight'},method='PUT').status_code==200
    assert owner.get('/api/settings').json['theme']=='record-store'
    assert bob.get('/api/settings').json['theme']=='record-store'
    assert post(alice,f'/api/albums/{ALBUM}/shelf',method='DELETE').status_code==200
    assert alice.get('/api/shelf').json['albums']==[]
    assert len(owner.get('/api/shelf').json['albums'])==len(bob.get('/api/shelf').json['albums'])==1


def test_backups_and_replaceable_cache_are_account_scoped(users,tmp_path):
    app,owner,alice,_,ids,_=users
    contexts=[app.extensions['accounts'].owner,app.extensions['accounts'].context(ids['alice'])]
    for name,context,client in zip(('owner','alice'),contexts,(owner,alice)):
        seed(context,name)
        with context.store.cache_connect() as db:
            db.execute('INSERT INTO cache VALUES (?,?,?)', ('same-key',json.dumps(name),time.time()+600))
        assert context.store.cache_get('same-key')==name
        response=post(client,'/api/backup'); assert response.status_code==200
        path=tmp_path/(name+'.db'); path.write_bytes(response.data)
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT title FROM albums').fetchone()[0]==name
            names={r[0] for r in db.execute('SELECT name FROM sqlite_master')}
            assert 'accounts' not in names and 'trusted' not in names
    assert contexts[0].store.cache_directory not in contexts[1].store.directory.parents
    assert contexts[1].store.directory not in contexts[1].store.cache_directory.parents


def test_cover_overrides_and_http_cache_cannot_cross_accounts(users):
    app,owner,alice,_,ids,_=users
    for client,context,color in [(owner,app.extensions['accounts'].owner,'red'),(alice,app.extensions['accounts'].context(ids['alice']),'blue')]:
        seed(context,color)
        buffer=io.BytesIO(); Image.new('RGB',(20,20),color).save(buffer,format='PNG')
        assert post(client,f'/api/albums/{ALBUM}/artwork',{'image':base64.b64encode(buffer.getvalue()).decode()}).status_code==200
    first=owner.get(f'/api/albums/{ALBUM}/artwork?size=128&account=owner')
    second=alice.get(f'/api/albums/{ALBUM}/artwork?size=128&account='+ids['alice'])
    assert first.data!=second.data
    assert 'Cookie' in first.headers['Vary'] and 'private' in first.headers['Cache-Control']
    assert alice.get(f'/api/albums/{ALBUM}/artwork?account=owner').status_code==403
    assert post(alice,f'/api/albums/{ALBUM}/artwork',method='DELETE').status_code==200
    assert app.extensions['store'].artwork_override(ALBUM)


def test_spotify_callback_routes_to_initiating_account_after_browser_switch(users,monkeypatch):
    app,owner,alice,_,ids,_=users
    ctx=app.extensions['accounts'].context(ids['alice']); calls=[]
    monkeypatch.setattr(ctx.spotify,'_token_request',lambda body:(calls.append(body) or {'access_token':'alice-access','refresh_token':'alice-refresh','expires_in':3600}))
    result=post(alice,'/api/spotify/connect'); query=parse_qs(urlsplit(result.json['url']).query)
    assert query['show_dialog']==['true']
    assert signin(alice).status_code==200
    response=alice.get('/auth/spotify/callback?code=auth-code&state='+query['state'][0])
    assert response.status_code==200 and b'alice' in response.data
    assert ctx.spotify.tokens['refresh_token']=='alice-refresh'
    assert app.extensions['spotify'].connected is False
    assert owner.get('/api/status').json['spotify_connected'] is False
    assert calls[0]['code_verifier']
    assert alice.get('/auth/spotify/callback?code=replay&state='+query['state'][0]).status_code==400
    assert len(calls)==1


@pytest.mark.parametrize('failure',['expired','disabled','unknown','denied'])
def test_invalid_oauth_never_connects_an_account(users,monkeypatch,failure):
    app,owner,alice,_,ids,_=users
    ctx=app.extensions['accounts'].context(ids['alice'])
    monkeypatch.setattr(ctx.spotify,'_token_request',lambda _:pytest.fail('Invalid callback exchanged a token'))
    query=parse_qs(urlsplit(post(alice,'/api/spotify/connect').json['url']).query); state=query['state'][0]
    if failure=='expired':
        with app.extensions['accounts'].connect() as db: db.execute('UPDATE oauth SET expires=0')
    if failure=='disabled':
        assert post(owner,'/api/accounts/'+ids['alice'],{'password':OWNER,'disabled':True},method='PUT').status_code==200
    if failure=='unknown': state='unknown'
    error='&error=access_denied' if failure=='denied' else ''
    assert app.test_client().get('/auth/spotify/callback?code=code&state='+state+error).status_code==400
    assert ctx.spotify.connected is False and app.extensions['spotify'].connected is False


def test_spotify_devices_playback_and_disconnect_use_only_current_account(users,monkeypatch):
    app,owner,alice,bob,ids,_=users
    for name,client,context in [('owner',owner,app.extensions['accounts'].owner),('alice',alice,app.extensions['accounts'].context(ids['alice'])),('bob',bob,app.extensions['accounts'].context(ids['bob']))]:
        context.spotify._save({'access_token':name,'refresh_token':name,'expires_in':3600})
        monkeypatch.setattr(context.spotify,'api',lambda method,path,params=None,body=None,n=name:
            {'devices':[{'id':n,'name':n,'type':'Computer','is_active':True,'is_restricted':False}]} if path=='me/player/devices' else
            {'item':{'id':n,'name':n,'artists':[]},'is_playing':True,'device':{'name':n}})
        assert client.get('/api/spotify/devices').json['devices'][0]['id']==name
        assert client.get('/api/spotify/playback').json['track']==name
    assert post(alice,'/api/spotify/disconnect').status_code==200
    assert owner.get('/api/status').json['spotify_connected'] and bob.get('/api/status').json['spotify_connected']
    assert alice.get('/api/status').json['spotify_connected'] is False


def test_ingress_can_switch_users_and_signout_does_not_return_to_owner(users):
    app,_,_,_,ids,_=users
    client=app.test_client(); client.environ_base.update(REMOTE_ADDR='172.30.32.2',HTTP_X_INGRESS_PATH='/ha')
    assert client.get('/api/status').json['account']['username']=='owner'
    assert post(client,'/api/logout').status_code==200
    assert client.get('/api/status').json['authenticated'] is False
    assert signin(client,'alice',ALICE).status_code==200
    assert client.get('/api/status').json['account']['id']==ids['alice']
    assert client.get('/api/security').json['ingress'] is False
    assert post(client,'/api/security/revoke-sessions').status_code==401
    assert client.get('/api/accounts').status_code==403
    assert post(client,'/api/logout').status_code==200
    assert client.get('/api/shelf').status_code==401


def test_disabled_account_reenable_preserves_library_but_revokes_sessions(users):
    app,owner,alice,bob,ids,_=users
    seed(app.extensions['accounts'].context(ids['alice']),'Kept collection')
    with alice.session_transaction() as s: old_token=s['sid']
    assert post(owner,'/api/accounts/'+ids['alice'],{'password':OWNER,'disabled':True},method='PUT').status_code==200
    assert alice.get('/api/shelf').status_code==401
    assert signin(app.test_client(),'alice',ALICE).status_code==401
    assert bob.get('/api/shelf').status_code==200
    assert post(owner,'/api/accounts/'+ids['alice'],{'password':OWNER,'disabled':False},method='PUT').status_code==200
    assert app.extensions['accounts'].context(ids['alice']).security.identity(old_token) is None
    assert signin(alice,'alice',ALICE).status_code==200
    assert alice.get('/api/shelf').json['albums'][0]['title']=='Kept collection'


def test_password_change_and_admin_reset_revoke_only_target_account_and_persist(users):
    app,owner,alice,bob,ids,options=users
    new='new-alice-password-long-enough'
    assert post(alice,'/api/security/password',{'password':ALICE,'new_password':new}).status_code==200
    assert alice.get('/api/status').json['account']['username']=='alice'
    assert signin(app.test_client(),'alice',ALICE).status_code==401
    assert signin(app.test_client(),'alice',new).status_code==200
    assert post(owner,'/api/accounts/'+ids['alice'],{'password':OWNER,'new_password':ALICE},method='PUT').status_code==200
    assert alice.get('/api/shelf').status_code==401
    assert bob.get('/api/shelf').status_code==200 and owner.get('/api/shelf').status_code==200
    restarted=create_app(options)
    assert signin(restarted.test_client(),'alice',ALICE).status_code==200
    assert ALICE.encode() not in app.extensions['accounts'].path.read_bytes()


def test_totp_and_trusted_browsers_belong_to_each_account(users):
    app,owner,alice,bob,ids,_=users
    result=post(alice,'/api/security/totp/start',{'password':ALICE})
    assert 'AudioShelf%3Aalice' in result.json['uri']
    secret=result.json['secret']
    result=post(alice,'/api/security/totp/confirm',{'password':ALICE,'setup_code':totp(secret,int(time.time()//30))})
    codes=result.json['recovery_codes']
    assert signin(alice,'alice',ALICE,code=codes[0],remember=True).status_code==200
    trust=alice.get_cookie('audioshelf_trusted').value
    assert app.extensions['accounts'].context(ids['bob']).security.trusted_valid(trust) is False
    assert app.extensions['security'].get('totp') is None
    assert bob.get('/api/security').json['two_factor'] is False
    assert signin(app.test_client(),'alice',ALICE).status_code==401
    assert post(owner,'/api/accounts/'+ids['alice'],{'password':OWNER,'new_password':ALICE,'reset_two_factor':True},method='PUT').status_code==200
    assert signin(app.test_client(),'alice',ALICE).status_code==200
    assert signin(owner).status_code==200


def test_old_tab_account_header_cannot_mutate_newly_selected_library(users):
    app,_,alice,_,ids,_=users
    assert signin(alice,'bob',BOB).status_code==200
    response=alice.put('/api/settings',json={'theme':'midnight'},headers={'X-AudioShelf-Request':'1','X-AudioShelf-Account':ids['alice']})
    assert response.status_code==409
    assert alice.get('/api/settings').json['theme']=='record-store'


def test_concurrent_requests_do_not_share_a_current_account(users):
    app,owner,alice,bob,ids,_=users
    for client,context,name in [(owner,app.extensions['accounts'].owner,'owner'),(alice,app.extensions['accounts'].context(ids['alice']),'alice'),(bob,app.extensions['accounts'].context(ids['bob']),'bob')]:
        seed(context,name)
    def read(name,password):
        client=app.test_client(); assert signin(client,name,password).status_code==200
        for _ in range(8):
            assert client.get('/api/shelf').json['albums'][0]['title']==name
            assert client.get('/api/status').json['account']['username']==name
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(read,name,password) for name,password in [('owner',OWNER),('alice',ALICE),('bob',BOB)]]
        for future in futures: future.result()


def test_login_rate_limit_is_shared_across_usernames(users):
    app,_,_,_,_,_=users
    client=app.test_client()
    for n in range(10):
        assert signin(client,['alice','bob','unknown'][n%3],'wrong-password').status_code==401
    assert signin(client,'owner',OWNER).status_code==429


def test_only_trusted_ingress_can_return_to_owner_after_signout(users):
    app,_,_,_,_,_=users
    client=app.test_client()
    assert post(client,'/api/login/ingress').status_code==403
    client.environ_base.update(REMOTE_ADDR='172.30.32.2',HTTP_X_INGRESS_PATH='/ha')
    assert post(client,'/api/logout').status_code==200
    assert client.get('/api/status').json['authenticated'] is False
    assert post(client,'/api/login/ingress').status_code==200
    status=client.get('/api/status').json
    assert status['account']['username']=='owner' and status['account']['admin']
    assert client.get('/api/security').json['ingress'] is True
    assert post(client,'/api/accounts',{'username':'ingress-created','new_password':ALICE}).status_code==201


def test_background_playback_is_account_and_session_scoped(users,monkeypatch):
    import threading
    from unittest.mock import Mock
    app,owner,alice,bob,ids,_=users
    ready=threading.Event();contexts=[];jobs=[]
    try:
        for name,client in [('alice',alice),('bob',bob)]:
            ctx=app.extensions['accounts'].context(ids[name]);contexts.append(ctx)
            seed(ctx,name)
            ctx.store.set_setting('preferred_device',{'id':name,'name':name,'type':'Computer'})
            with ctx.store.connect() as db: db.execute('UPDATE tracks SET verified=1,spotify_id=?', ('a'*22,))
            monkeypatch.setattr(ctx.spotify,'devices',lambda n=name:[{'id':n,'name':n,'type':'Computer'}] if ready.is_set() else [])
            monkeypatch.setattr(ctx.spotify,'play',Mock(return_value={'account':name}))
            ctx.handoff.interval=.005
            result=post(client,f'/api/albums/{ALBUM}/playback-handoff')
            assert result.status_code==202
            jobs.append(result.json['id'])
        alice_path='/api/spotify/playback-handoff/'+jobs[0]
        assert alice.get(alice_path).json['state']=='waiting'
        assert bob.get(alice_path).status_code==404 and owner.get(alice_path).status_code==404
        assert post(bob,alice_path,method='DELETE').status_code==404
        ready.set()
        deadline=time.monotonic()+1
        while any(ctx.handoff.job['state']=='waiting' for ctx in contexts) and time.monotonic()<deadline:
            time.sleep(.005)
        for name,ctx in zip(('alice','bob'),contexts):
            assert ctx.handoff.job['state']=='started'
            assert ctx.handoff.job['result']=={'account':name}
            assert ctx.spotify.play.call_count==1
    finally:
        for ctx in contexts: ctx.handoff.cancel_all()


@pytest.mark.parametrize('action',['logout','switch','disable','reset'])
def test_pending_background_playback_stops_when_account_access_changes(users,monkeypatch,action):
    from unittest.mock import Mock
    app,owner,alice,bob,ids,_=users
    ctx=app.extensions['accounts'].context(ids['alice']);seed(ctx,'alice')
    ctx.store.set_setting('preferred_device',{'id':'alice','name':'alice','type':'Computer'})
    with ctx.store.connect() as db: db.execute('UPDATE tracks SET verified=1,spotify_id=?', ('a'*22,))
    monkeypatch.setattr(ctx.spotify,'devices',lambda:[])
    monkeypatch.setattr(ctx.spotify,'play',Mock())
    result=post(alice,f'/api/albums/{ALBUM}/playback-handoff');assert result.status_code==202
    if action=='logout': assert post(alice,'/api/logout').status_code==200
    if action=='switch': assert signin(alice,'bob',BOB).status_code==200
    if action=='disable': assert post(owner,'/api/accounts/'+ids['alice'],{'password':OWNER,'disabled':True},method='PUT').status_code==200
    if action=='reset': assert post(owner,'/api/accounts/'+ids['alice'],{'password':OWNER,'new_password':ALICE},method='PUT').status_code==200
    assert ctx.handoff.job['state']=='cancelled'
    ctx.spotify.play.assert_not_called()
    assert bob.get('/api/status').json['account']['username']=='bob'


@pytest.mark.parametrize('role',['view','control'])
def test_support_access_cannot_create_or_manage_accounts(users,role):
    app,owner,_,_,ids,_=users
    grant=post(owner,'/api/security/support',{'password':OWNER,'role':role}).json
    support=app.test_client(); assert signin(support,'owner',grant['password']).status_code==200
    assert support.get('/api/status').json['account']['admin'] is False
    assert support.get('/api/accounts').status_code==403
    assert post(support,'/api/accounts',{'username':'outsider','new_password':ALICE}).status_code==403
    assert post(support,'/api/accounts/'+ids['alice'],{'disabled':True},method='PUT').status_code==403


def test_new_login_or_disabled_old_session_cannot_cancel_owner_playback(users,monkeypatch):
    from unittest.mock import Mock
    app,owner,alice,_,ids,_=users
    cancel=Mock();monkeypatch.setattr(app.extensions['accounts'].owner.handoff,'cancel_all',cancel)
    assert signin(app.test_client(),'bob',BOB).status_code==200
    cancel.assert_not_called()
    assert post(owner,'/api/accounts/'+ids['alice'],{'password':OWNER,'disabled':True},method='PUT').status_code==200
    assert signin(alice,'bob',BOB).status_code==200
    cancel.assert_not_called()


def test_playback_authorization_does_not_wait_on_account_management_lock(users,monkeypatch):
    import threading
    app,_,alice,_,ids,_=users
    accounts=app.extensions['accounts'];ctx=accounts.context(ids['alice']);seed(ctx,'alice')
    ctx.store.set_setting('preferred_device',{'id':'alice','name':'alice','type':'Computer'})
    with ctx.store.connect() as db: db.execute('UPDATE tracks SET verified=1,spotify_id=?', ('a'*22,))
    ready=threading.Event();sent=threading.Event()
    monkeypatch.setattr(ctx.spotify,'devices',lambda:[{'id':'alice','name':'alice','type':'Computer'}] if ready.is_set() else [])
    monkeypatch.setattr(ctx.spotify,'play',lambda *args,**kwargs:(sent.set() or {'ok':True}))
    ctx.handoff.interval=.005
    assert post(alice,f'/api/albums/{ALBUM}/playback-handoff').status_code==202
    try:
        with accounts.lock:
            ready.set()
            assert sent.wait(1), 'Background authorization was blocked by account administration'
    finally:
        ctx.handoff.cancel_all()
