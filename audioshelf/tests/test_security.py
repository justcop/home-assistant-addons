import json
import time

import pytest

from app.server import create_app
from app.security import totp
from conftest import post, ALBUM

PASSWORD = 'test-owner-password-with-enough-length'


@pytest.fixture
def secured(tmp_path):
    options = {'data_directory':str(tmp_path/'shelf'), 'private_directory':str(tmp_path/'private'),
               'web_password':PASSWORD, 'allow_support_access':True}
    app = create_app(options); app.config['TESTING'] = True
    return app, app.test_client(), options


def login(client, password=PASSWORD, **extra):
    return post(client, '/api/login', {'password':password, **extra})


def owner(client):
    assert login(client).status_code == 200


def provision(app, client):
    owner(client)
    result = post(client, '/api/security/totp/start', {'password':PASSWORD})
    assert result.status_code == 200
    assert result.json['qr'].startswith('data:image/png;base64,')
    secret = result.json['secret']
    confirmation = post(client, '/api/security/totp/confirm', {
        'password':PASSWORD, 'setup_code':totp(secret, int(time.time()//30))})
    assert confirmation.status_code == 200
    return secret, confirmation.json['recovery_codes']


def test_blank_password_locks_standalone_but_ingress_still_works(tmp_path):
    app = create_app({'data_directory':str(tmp_path/'s'), 'private_directory':str(tmp_path/'p')})
    client = app.test_client()
    assert client.get('/api/status').json['authenticated'] is False
    assert client.get('/api/shelf').status_code == 401
    assert login(client, '').status_code == 403
    assert client.get('/api/shelf', headers={'X-Ingress-Path':'/fake'}).status_code == 401
    assert client.get('/api/shelf', headers={'X-Ingress-Path':'/ha'}, environ_base={'REMOTE_ADDR':'172.30.32.2'}).status_code == 200


def test_standard_sessions_are_secure_expire_and_logout_revokes(secured):
    app, client, _ = secured
    response = login(client)
    cookie = response.headers.get('Set-Cookie')
    assert 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=Lax' in cookie
    with client.session_transaction() as session: token = session['sid']
    assert app.extensions['security'].identity(token)
    assert post(client, '/api/logout').status_code == 200
    assert app.extensions['security'].identity(token) is None
    owner(client)
    with app.extensions['security'].connect() as db:db.execute('UPDATE sessions SET expires=0')
    assert client.get('/api/shelf').status_code == 401


def test_password_change_invalidates_existing_sessions(secured):
    app, client, options = secured
    owner(client)
    with client.session_transaction() as session: token = session['sid']
    changed = create_app({**options, 'web_password':'a-different-owner-password'})
    assert changed.extensions['security'].identity(token) is None


def test_login_throttle_survives_restart_and_ignores_forwarded_ip(secured):
    app, client, options = secured
    for n in range(10):
        assert client.post('/api/login',json={'password':'bad'},headers={
            'X-AudioShelf-Request':'1','X-Forwarded-For':f'10.0.0.{n}'}).status_code == 401
    restarted = create_app(options).test_client()
    assert login(restarted).status_code == 429


def test_totp_requires_factor_rejects_replay_and_recovery_is_single_use(secured):
    app, client, _ = secured
    secret, codes = provision(app, client)
    stranger = app.test_client()
    assert login(stranger).status_code == 401
    assert login(stranger, code=totp(secret,int(time.time()//30))).status_code == 401
    assert login(stranger, code=codes[0]).status_code == 200
    assert login(app.test_client(), code=codes[0]).status_code == 401
    assert len(app.extensions['security'].get('recovery')) == 9
    data = app.extensions['security'].path.read_bytes()
    assert all(code.encode() not in data for code in codes)


def test_totp_matches_rfc_vector():
    assert totp('GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ',1) == '287082'


def test_trusted_browser_still_needs_password_and_is_revocable(secured):
    app, client, _ = secured
    _, codes = provision(app, client)
    assert login(client, code=codes[0], remember=True).status_code == 200
    assert client.get_cookie('audioshelf_trusted').secure
    assert login(client, password='bad').status_code == 401
    assert login(client).status_code == 200
    assert post(client,'/api/security/revoke-sessions',{'password':PASSWORD,'code':codes[1]}).status_code == 200
    assert login(client).status_code == 401


def test_security_changes_require_fresh_owner_factor_even_on_trusted_browser(secured):
    app, client, _ = secured
    _, codes = provision(app, client)
    login(client, code=codes[0], remember=True)
    assert post(client,'/api/security/totp/disable',{'password':PASSWORD}).status_code == 401
    assert post(client,'/api/security/totp/disable',{'password':PASSWORD,'code':codes[1]}).status_code == 200
    assert login(client).status_code == 200


def grant(client, role='view'):
    response=post(client,'/api/security/support',{'password':PASSWORD,'role':role,'hours':1})
    assert response.status_code == 200
    return response.json


@pytest.mark.parametrize('role',['view','control'])
def test_support_permissions_expiry_and_revocation_are_server_enforced(secured,role):
    app, client, _ = secured
    owner(client); access=grant(client,role)
    overview=client.get('/api/security')
    assert overview.status_code==200 and overview.json['support_enabled']
    assert 'password' not in overview.json['grants'][0]
    support=app.test_client()
    assert login(support,access['password']).status_code == 200
    assert support.get('/api/shelf').status_code == 200
    assert support.get('/api/security').status_code == 403
    assert support.get('/api/export').status_code == 403
    assert support.get(f'/api/albums/{ALBUM}/diagnostics').status_code == 403
    for path in ('/api/spotify/connect','/api/spotify/disconnect','/api/backup','/api/security/support'):
        assert post(support,path).status_code == 403
    theme=post(support,'/api/settings',{'theme':'midnight'},method='PUT')
    assert theme.status_code == (403 if role=='view' else 200)
    assert post(client,f"/api/security/support/{access['id']}/revoke",{'password':PASSWORD}).status_code == 200
    assert support.get('/api/shelf').status_code == 401
    assert login(support,access['password']).status_code == 401
    access=grant(client,role)
    assert login(support,access['password']).status_code == 200
    with app.extensions['security'].connect() as db:db.execute('UPDATE grants SET expires=0')
    assert support.get('/api/shelf').status_code == 401


def test_support_master_toggle_revokes_existing_and_prevents_resurrection(secured):
    app, client, options=secured
    owner(client);access=grant(client)
    off=create_app({**options,'allow_support_access':False})
    assert login(off.test_client(),access['password']).status_code == 401
    on=create_app(options)
    assert login(on.test_client(),access['password']).status_code == 401


def test_support_disabled_by_default_and_secrets_not_in_collection(secured):
    app,client,options=secured
    owner(client);access=grant(client)
    assert access['password'].encode() not in app.extensions['security'].path.read_bytes()
    assert 'security' not in client.get('/api/export').json
    disabled=create_app({**options,'allow_support_access':False}).test_client()
    owner(disabled)
    assert post(disabled,'/api/security/support',{'password':PASSWORD}).status_code == 403


def test_setup_bruteforce_and_expiry_are_limited(secured):
    app,client,_=secured;owner(client)
    assert post(client,'/api/security/totp/start',{'password':PASSWORD}).status_code == 200
    for n in range(10):
        assert post(client,'/api/security/totp/confirm',{'password':PASSWORD,'setup_code':'wrong'}).status_code == 400
    assert post(client,'/api/security/totp/confirm',{'password':PASSWORD,'setup_code':'wrong'}).status_code == 429
    assert not app.extensions['security'].get('totp')


def test_cross_origin_mutation_is_rejected(secured):
    _,client,_=secured
    response=client.post('/api/login',json={'password':PASSWORD},headers={'X-AudioShelf-Request':'1','Origin':'https://evil.example'})
    assert response.status_code == 403


def test_home_assistant_can_recover_totp_without_factor_but_spoofed_header_cannot(secured):
    app, client, _=secured
    _, codes=provision(app,client)
    login(client,code=codes[0],remember=True)
    recovery=app.test_client()
    assert recovery.post('/api/security/totp/disable',json={},headers={
        'X-AudioShelf-Request':'1','X-Ingress-Path':'/ha'}).status_code==401
    response=recovery.post('/api/security/totp/disable',json={},headers={
        'X-AudioShelf-Request':'1','X-Ingress-Path':'/ha','Origin':'https://home-assistant.example'},
        environ_base={'REMOTE_ADDR':'172.30.32.2'})
    assert response.status_code==200
    assert not app.extensions['security'].get('totp')
    assert client.get('/api/shelf').status_code==401
    assert not app.extensions['security'].trusted_valid(client.get_cookie('audioshelf_trusted').value)
    assert login(recovery).status_code==200


def test_sessions_and_factor_setup_survive_restarts_and_setup_expires(secured):
    app,client,options=secured;owner(client)
    with client.session_transaction() as session:token=session['sid']
    restarted=create_app(options)
    assert restarted.extensions['security'].identity(token)
    pending=app.extensions['security'].start_totp()
    app.extensions['security'].set('pending_totp',{'secret':pending['secret'],'expires':0})
    response=post(client,'/api/security/totp/confirm',{'password':PASSWORD,'setup_code':'123456'})
    assert response.status_code==400 and 'expired' in response.json['error']


def test_trusted_browser_credential_is_revoked_on_logout(secured):
    app,client,_=secured
    _,codes=provision(app,client)
    login(client,code=codes[0],remember=True)
    token=client.get_cookie('audioshelf_trusted').value
    assert app.extensions['security'].trusted_valid(token)
    post(client,'/api/logout')
    assert not app.extensions['security'].trusted_valid(token)


def test_ha_option_recovers_lost_factor_and_preserves_password_spotify_and_collection(secured):
    app,client,options=secured
    owner(client);access=grant(client)
    _,codes=provision(app,client)
    login(client,code=codes[0],remember=True)
    with client.session_transaction() as session:old_session=session['sid']
    old_trust=client.get_cookie('audioshelf_trusted').value
    app.extensions['store'].set_setting('sentinel','kept')
    app.extensions['spotify']._save({'access_token':'fixture-only','refresh_token':'fixture-only','expires_in':3600})
    app.extensions['security'].throttle('an-address')
    recovered=create_app({**options,'two_factor_reset_request':'reset-1'})
    security=recovered.extensions['security']
    assert not security.get('totp') and security.get('recovery')==[] and security.get('pending_totp') is None
    assert security.identity(old_session) is None and not security.trusted_valid(old_trust)
    assert login(recovered.test_client(),access['password']).status_code==401
    assert login(recovered.test_client(),'').status_code==401
    assert login(recovered.test_client()).status_code==200
    assert recovered.extensions['store'].setting('sentinel')=='kept'
    assert recovered.extensions['spotify'].tokens['access_token']=='fixture-only'
    assert any('reset from Home Assistant' in event['event'] for event in security.overview()['events'])


def test_ha_reset_is_once_per_changed_value_even_after_clearing_and_reenrolling(secured):
    _,_,options=secured
    app=create_app({**options,'two_factor_reset_request':'reset-1'})
    client=app.test_client();_,codes=provision(app,client)
    for value in ('reset-1','', ' reset-1 '):
        app=create_app({**options,'two_factor_reset_request':value})
        assert app.extensions['security'].get('totp')
        assert login(app.test_client()).status_code==401
    reset=create_app({**options,'two_factor_reset_request':'reset-2'})
    assert not reset.extensions['security'].get('totp')
    assert login(reset.test_client()).status_code==200


def test_blank_reset_option_does_not_disable_enrolled_factor(secured):
    app,client,options=secured
    provision(app,client)
    for value in ('', '   ', None):
        restarted=create_app({**options,'two_factor_reset_request':value})
        assert restarted.extensions['security'].get('totp')


@pytest.mark.parametrize('value',[True,123,'x'*121])
def test_reset_option_rejects_invalid_configuration_without_disabling_factor(secured,value):
    app,client,options=secured;provision(app,client)
    with pytest.raises(RuntimeError,match='short text'):
        create_app({**options,'two_factor_reset_request':value})
    assert app.extensions['security'].get('totp')
