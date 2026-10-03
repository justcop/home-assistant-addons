import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.server import create_app

ARTIST='f181961b-20f7-459e-89de-920ef03c7ed0'
ALBUM='f5093c06-23e3-4f01-aeaa-40f72885ee3a'
RELEASE='55df510f-2f2b-4d1e-b7d6-0b54c1c9f48e'


@pytest.fixture
def application(tmp_path):
    app=create_app({'data_directory':str(tmp_path/'shelf'),'private_directory':str(tmp_path/'private'),
        'spotify_client_id':'test-client','spotify_redirect_uri':'https://audioshelf.example/auth/spotify/callback'})
    app.config['TESTING']=True
    store=app.extensions['store']
    store.catalogue([{'id':ALBUM,'title':'The Album','first-release-date':'2007-04-18','primary-type':'Album',
        'artist-credit':[{'artist':{'id':ARTIST,'name':'The Artist','sort-name':'Artist, The'}}]}])
    store.set_tracks(ALBUM,{'id':RELEASE,'label':'GB · 2007 · CD'},[
        {'title':'Opening','disc_number':1,'track_number':1,'duration_ms':180000,'recording_id':'rec-1'},
        {'title':'Closing','disc_number':1,'track_number':2,'duration_ms':240000,'recording_id':'rec-2'}])
    return app


@pytest.fixture
def client(application):
    client=application.test_client()
    client.environ_base.update(REMOTE_ADDR='172.30.32.2', HTTP_X_INGRESS_PATH='/api/hassio_ingress/test')
    return client


def spotify_track(name, identifier, duration=180000):
    return {'id':identifier,'name':name,'artists':[{'name':'The Artist'}], 'duration_ms':duration,'is_playable':True}


def post(client,path,data=None,method='POST'):
    return client.open(path,method=method,json=data or {},headers={'X-AudioShelf-Request':'1'})
