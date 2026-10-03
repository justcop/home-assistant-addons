import base64
import io
import sqlite3
from unittest.mock import Mock

import pytest
import requests
from PIL import Image

from app.artwork import MAX_BYTES, allowed_url, image_type
from app.errors import AppError
from app.server import create_app
from app.storage import Store
from conftest import ALBUM, post


def png():
    buffer=io.BytesIO();Image.new('RGB',(20,20),'red').save(buffer,format='PNG');return buffer.getvalue()


def test_downloaded_artwork_is_cached_outside_collection(application,client,monkeypatch):
    artwork=application.extensions['artwork'];download=Mock(return_value=(png(),'image/png'))
    monkeypatch.setattr(artwork,'download',download)
    first=client.get(f'/api/albums/{ALBUM}/artwork')
    second=client.get(f'/api/albums/{ALBUM}/artwork')
    assert first.data==second.data==png()
    assert first.headers['X-Artwork-Source']=='cover-art-archive'
    assert download.call_count==1
    assert application.extensions['store'].directory not in artwork.directory.parents
    assert list(artwork.directory.glob('*.img'))


def test_canonical_release_then_spotify_fallback(application,monkeypatch):
    artwork=application.extensions['artwork'];spotify=application.extensions['spotify'];calls=[]
    spotify.tokens={'refresh_token':'test'}
    monkeypatch.setattr(spotify,'api',lambda *args:{'albums':{'items':[{'images':[{'url':'https://i.scdn.co/image/test','width':640}]}]}})
    def download(url):
        calls.append(url)
        if 'scdn.co' in url:return png(),'image/png'
        raise requests.HTTPError('404')
    monkeypatch.setattr(artwork,'download',download)
    assert artwork.get(ALBUM)[2]=='spotify'
    assert '/release/' in calls[0] and '/release-group/' in calls[1]
    assert calls[2]=='https://i.scdn.co/image/test'


def test_missing_cover_retries_after_spotify_connects(application,monkeypatch):
    artwork=application.extensions['artwork']
    monkeypatch.setattr(artwork,'download',Mock(side_effect=requests.HTTPError('404')))
    assert artwork.get(ALBUM)[2]=='placeholder'
    spotify=application.extensions['spotify'];spotify.tokens={'refresh_token':'test'}
    monkeypatch.setattr(artwork,'_spotify_url',lambda album:'https://i.scdn.co/image/test')
    monkeypatch.setattr(artwork,'download',lambda url:(png(),'image/png') if 'scdn.co' in url else (_ for _ in ()).throw(requests.HTTPError('404')))
    assert artwork.get(ALBUM)[2]=='spotify'


def test_uploaded_cover_survives_restart_and_reset(application,client,monkeypatch):
    assert post(client,f'/api/albums/{ALBUM}/artwork',{'image':base64.b64encode(png()).decode()}).status_code==200
    original=application.extensions['store']
    restarted=create_app({'data_directory':str(original.directory),'cache_directory':str(original.cache_directory),
        'private_directory':str(original.directory.parent/'private')})
    assert restarted.test_client().get(f'/api/albums/{ALBUM}/artwork').data==png()
    assert list((original.directory/'custom-artwork').glob('*.img'))
    assert post(client,f'/api/albums/{ALBUM}/artwork',method='DELETE').status_code==200
    assert original.artwork_override(ALBUM) is None
    assert not list((original.directory/'custom-artwork').glob('*.img'))


def test_invalid_artwork_upload_leaves_previous_cover(application,client):
    url=f'/api/albums/{ALBUM}/artwork'
    post(client,url,{'image':base64.b64encode(png()).decode()})
    previous=application.extensions['store'].artwork_override(ALBUM)
    assert post(client,url,{'image':base64.b64encode(b'<svg><script/></svg>').decode()}).status_code==400
    assert post(client,url,{'image':'not base64!'}).status_code==400
    assert application.extensions['store'].artwork_override(ALBUM)==previous


def test_artwork_requires_password(tmp_path):
    app=create_app({'data_directory':str(tmp_path/'s'),'private_directory':str(tmp_path/'p'),'web_password':'test'})
    assert app.test_client().get(f'/api/albums/{ALBUM}/artwork').status_code==401


def test_download_hosts_and_size_are_restricted():
    assert allowed_url('https://archive.org/download/cover.jpg')
    assert allowed_url('https://ia800100.us.archive.org/image.jpg')
    assert not allowed_url('https://127.0.0.1/secret')
    assert not allowed_url('https://archive.org.evil.example/cover')
    assert not allowed_url('http://i.scdn.co/image/test')
    with pytest.raises(AppError):image_type(b'x'*(MAX_BYTES+1))


def test_schema_upgrade_removes_old_reproducible_cache(application):
    store=application.extensions['store']
    with store.connect() as db:
        db.execute('CREATE TABLE cache (key TEXT PRIMARY KEY,value TEXT,expires REAL)')
        db.execute('INSERT INTO cache VALUES (?,?,?)',('old','x'*1000000,99999999999))
        db.execute('PRAGMA user_version=1')
    old_size=store.path.stat().st_size
    upgraded=Store(store.directory,store.cache_directory)
    assert len(upgraded.album(ALBUM)['tracks'])==2
    with upgraded.connect() as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='cache'").fetchone()
    assert store.path.stat().st_size<old_size


def test_database_backup_excludes_metadata_cache(application):
    store=application.extensions['store'];store.cache_put('big',{'data':'x'*100000})
    with sqlite3.connect(store.backup()) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='cache'").fetchone()
        assert db.execute('SELECT count(*) FROM tracks').fetchone()[0]==2


def test_cache_can_be_rebuilt_without_losing_collection(application):
    store=application.extensions['store'];store.shelf(ALBUM,True);store.cache_put('test',{'value':1})
    store.cache_path.unlink()
    restarted=Store(store.directory,store.cache_directory)
    assert restarted.cache_get('test') is None
    assert restarted.album(ALBUM)['on_shelf']


def test_cache_cannot_live_inside_collection(tmp_path):
    with pytest.raises(RuntimeError,match='separate'):
        create_app({'data_directory':str(tmp_path/'s'),'cache_directory':str(tmp_path/'s/cache')})
