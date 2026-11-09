"""Signed identity, migration, isolation and one-use Android login boundaries."""
import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from app.server import create_app
from conftest import post


@pytest.fixture
def access(tmp_path, monkeypatch):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    monkeypatch.setattr(jwt.PyJWKClient,'get_signing_key_from_jwt',lambda self, token: SimpleNamespace(key=key.public_key()))
    options={'data_directory':str(tmp_path/'shelf'),'private_directory':str(tmp_path/'private'),
             'cloudflare_team_domain':'justcop.cloudflareaccess.com','cloudflare_audience':'test-audience',
             'cloudflare_owner_email':'justcop@gmail.com','web_password':'old-password'}
    app=create_app(options);app.config['TESTING']=True
    def signed(email='justcop@gmail.com',sub='owner-subject',**overrides):
        claims={'iss':'https://justcop.cloudflareaccess.com','sub':sub,'email':email,'aud':['test-audience'],
                'iat':int(time.time()),'exp':int(time.time()+600),'type':'app',**overrides}
        return jwt.encode(claims,key,algorithm='RS256',headers={'kid':'test'})
    def client(email='justcop@gmail.com',sub='owner-subject',**claims):
        c=app.test_client(); c.environ_base['HTTP_CF_ACCESS_JWT_ASSERTION']=signed(email,sub,**claims);return c
    return app,client,signed,options


def test_owner_library_and_spotify_remain_and_no_second_login(access):
    app,client,_,_=access
    app.extensions['store'].set_setting('preserved','yes')
    app.extensions['security'].set('totp','old-secret')
    c=client();status=c.get('/api/status').json
    assert status['authenticated'] and status['account']['id']=='owner'
    assert not status['two_factor_enabled'] and not status['password_required']
    assert app.extensions['store'].setting('preserved')=='yes'
    for route in ['/api/login','/api/security/totp/start','/api/security/totp/confirm','/api/security/support','/api/security/password']:
        assert post(c,route).status_code==403
    assert c.get('/api/security').json['auth_provider']=='cloudflare'


@pytest.mark.parametrize('claims',[{'aud':['other']},{'iss':'https://evil.cloudflareaccess.com'},{'exp':1},
    {'sub':''},{'type':'org'},{'common_name':'service-token'},{'email':None}])
def test_reject_wrong_or_nonhuman_signed_identity(access,claims):
    _,client,_,_=access
    assert client(**claims).get('/api/status').status_code==401


def test_no_password_session_or_forged_email_header_can_bypass_access(access):
    app,_,_,_=access
    c=app.test_client()
    with c.session_transaction() as s:
        s['account']='owner';s['sid']=app.extensions['security'].create_session()
    assert c.get('/api/status',headers={'Cf-Access-Authenticated-User-Email':'justcop@gmail.com'}).status_code==401
    assert c.get('/api/status',headers={'Cf-Access-Jwt-Assertion':'forged'}).status_code==401
    assert c.get('/api/status',headers={'X-Ingress-Path':'/fake'}).status_code==401
    assert c.get('/health').status_code==200


def test_users_provision_once_isolate_data_and_cannot_become_admin(access):
    app,client,_,_=access
    owner=client(); alice=client('alice@gmail.com','alice');bob=client('bob@gmail.com','bob')
    owner.get('/api/status')
    first=alice.get('/api/status').json['account']
    assert first['id']!='owner' and not first['admin']
    assert alice.get('/api/status').json['account']==first
    assert bob.get('/api/status').json['account']['id']!=first['id']
    a=app.extensions['accounts'].context(first['id']);a.store.set_setting('private','alice')
    assert app.extensions['store'].setting('private') is None
    assert alice.get('/api/accounts').status_code==403
    assert client('justcop@gmail.com','alice').get('/api/status').json['account']['id']==first['id']
    assert client('justcop@gmail.com','different-owner').get('/api/status').status_code==403
    assert post(owner,'/api/accounts/'+first['id'],{'disabled':True},method='PUT').status_code==200
    assert alice.get('/api/status').status_code==403


def test_identity_rechecked_even_with_previous_owner_browser_session(access):
    app,client,_,_=access
    c=client('alice@gmail.com','alice')
    with c.session_transaction() as s:s['account']='owner';s['sid']=app.extensions['security'].create_session()
    assert c.get('/api/status').json['account']['id']!='owner'


def test_android_code_requires_pkce_expires_and_is_one_use(access):
    app,client,_,_=access
    verifier='v'*43; challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    response=client().get('/auth/android/start?challenge='+challenge+'&state='+'s'*43)
    import re,html
    callback=html.unescape(re.search('href="([^"]+)"',response.text).group(1))
    code=parse_qs(urlsplit(callback).query)['code'][0]
    public=app.test_client()
    assert post(public,'/auth/android/exchange',{'code':code,'verifier':'x'*43}).status_code==401
    result=post(public,'/auth/android/exchange',{'code':code,'verifier':verifier})
    assert result.status_code==200 and result.json['access_token']
    assert post(public,'/auth/android/exchange',{'code':code,'verifier':verifier}).status_code==401
    with app.extensions['accounts'].connect() as db:
        db.execute('INSERT INTO android_login VALUES (?,?,?,?)',(hashlib.sha256(b'expired').hexdigest(),challenge,'irrelevant',0))
    assert post(public,'/auth/android/exchange',{'code':'expired','verifier':verifier}).status_code==401


def test_helper_endpoint_keeps_job_bearer_boundary(access):
    app,_,_,_=access
    c=app.test_client()
    assert c.get('/api/helper/playback-handoff/owner/'+'a'*32).status_code in (401,404)
