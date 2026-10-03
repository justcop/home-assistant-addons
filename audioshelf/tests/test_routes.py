import pytest

from app.server import create_app
from conftest import ALBUM,post


def test_shell_assets_share_content_version_and_update_worker_is_not_cached(client):
    # Inspect the generated URLs rather than asserting a hardcoded release number.
    html=client.get('/').text
    version=client.get('/api/status').json['build']['asset_version']
    assert 'data-asset-version="'+version+'"' in html
    assert 'static/app.js?v='+version in html and 'static/style.css?v='+version in html
    assert 'static/vinyl.js?v='+version in html and 'static/vinyl.css?v='+version in html
    assert client.get('/sw.js').headers['Cache-Control']=='no-store'
    assert client.get('/api/status').headers['Cache-Control']=='no-store'


def test_shell_health_and_empty_shelf(client):
    assert client.get('/').status_code==200
    assert client.get('/health').json['ok']
    assert client.get('/api/shelf').json=={'artists':[],'albums':[]}
    assert client.get('/').headers['Cache-Control']=='no-store'


def test_mutations_require_same_origin_custom_header(client):
    assert client.post('/api/spotify/connect',json={}).status_code==403
    assert client.post('/api/spotify/connect',data={}).status_code==403
    assert post(client,'/api/spotify/connect').status_code==200


def test_password_protection_and_spoofed_ingress_header(tmp_path):
    app=create_app({'data_directory':str(tmp_path/'s'),'private_directory':str(tmp_path/'p'),'web_password':'correct'})
    client=app.test_client()
    assert client.get('/api/shelf').status_code==401
    assert client.get('/api/status').json['authenticated'] is False
    assert client.get('/api/shelf',headers={'X-Ingress-Path':'/fake'},environ_base={'REMOTE_ADDR':'10.0.0.4'}).status_code==401
    assert client.get('/api/shelf',headers={'X-Ingress-Path':'/api/hassio_ingress/test'},environ_base={'REMOTE_ADDR':'172.30.32.2'}).status_code==200
    assert post(client,'/api/login',{'password':'wrong'}).status_code==401
    assert post(client,'/api/login',{'password':'correct'}).status_code==200
    assert client.get('/api/shelf').status_code==200
    post(client,'/api/logout')
    assert client.get('/api/shelf').status_code==401


def test_ingress_assets_and_urls_are_prefixed(client):
    response=client.get('/',headers={'X-Ingress-Path':'/api/hassio_ingress/token'},environ_base={'REMOTE_ADDR':'172.30.32.2'})
    assert b'<base href="/api/hassio_ingress/token/">' in response.data
    assert b'href="static/style.css?v=' in response.data


def test_invalid_ids_rejected_without_remote_request(client):
    assert client.get('/api/albums/../../etc/passwd').status_code==404
    assert client.get('/api/albums/not-a-mbid').status_code==400


def test_canonical_change_requires_confirmation(client):
    assert post(client,f'/api/albums/{ALBUM}/release',{'release_id':'invalid'}).status_code==400


def test_export_contains_library_without_tokens_or_oauth(application,client):
    application.extensions['store'].shelf(ALBUM,True)
    response=client.get('/api/export')
    assert response.json['albums'][0]['id']==ALBUM
    assert response.json['format']=='audioshelf-1'
    assert 'access_token' not in response.get_data(as_text=True)
    assert 'oauth_states' not in response.get_data(as_text=True)


def test_callback_bad_state_does_not_connect(client,application):
    assert client.get('/auth/spotify/callback?code=fake&state=fake').status_code==400
    assert not application.extensions['spotify'].connected


def test_preferred_device_settings_validation_and_clear(application,client,monkeypatch):
    device={'id':'phone','name':'Phone','type':'Smartphone','is_restricted':False,'is_active':True}
    monkeypatch.setattr(application.extensions['spotify'],'devices',lambda:[device])
    headers={'X-AudioShelf-Request':'1'}
    assert client.get('/api/spotify/devices').json['devices']==[device]
    assert client.put('/api/settings',json={'preferred_device':{'id':'missing','name':'Phone','type':'Smartphone'}},headers=headers).status_code==400
    preferred={k:device[k] for k in ('id','name','type')}
    assert client.put('/api/settings',json={'preferred_device':preferred},headers=headers).json['preferred_device']==preferred
    assert client.get('/api/status').json['preferred_device']==preferred
    assert client.put('/api/settings',json={'preferred_device':None},headers=headers).json['preferred_device'] is None
    assert client.put('/api/settings',json={'preferred_device':'phone'},headers=headers).status_code==400
