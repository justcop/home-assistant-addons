"""Artwork rebuild preserves manual choices, keeps jobs account-scoped, and reports progress."""
from unittest.mock import Mock

import pytest

from app.artwork_refresh import ArtworkRefresh
from app.errors import AppError
from conftest import ALBUM, post


def test_bulk_refresh_preserves_custom_covers_and_downloads_both_sides(application, monkeypatch):
    store=application.extensions['store']
    art=application.extensions['artwork']
    store.shelf(ALBUM,True)
    store.set_artwork_override(ALBUM,ALBUM+'-custom.img')
    art.custom_directory.joinpath(ALBUM+'-custom.img').write_bytes(b'original custom art')
    worker=ArtworkRefresh()
    front=Mock(return_value=(b'valid','image/png','custom'))
    back=Mock(return_value=(b'valid','image/png','cover-art-archive'))
    sized=Mock(return_value=(b'valid','image/webp','custom'))
    back_sized=Mock(return_value=(b'valid','image/webp','cover-art-archive'))
    monkeypatch.setattr(art,'get',front)
    monkeypatch.setattr(art,'back',back)
    monkeypatch.setattr(art,'sized',sized)
    monkeypatch.setattr(art,'back_sized',back_sized)
    worker._run('owner',store,art,[ALBUM],lambda _:True,'a')
    assert front.call_count==1
    assert front.call_args.kwargs['force'] is False
    back.assert_called_once_with(ALBUM,force=True)
    assert [x.args[1] for x in back_sized.call_args_list]==[320,640]
    assert art.custom_directory.joinpath(ALBUM+'-custom.img').read_bytes()==b'original custom art'
    assert store.artwork_override(ALBUM)==ALBUM+'-custom.img'


def test_refresh_job_progress_survives_requests(application, client, monkeypatch):
    import time
    art=application.extensions['artwork'];store=application.extensions['store']
    store.shelf(ALBUM,True)
    monkeypatch.setattr(art,'get',lambda *args,**kwargs:(b'cover','image/png','spotify'))
    monkeypatch.setattr(art,'sized',lambda *args,**kwargs:(b'cover','image/webp','spotify'))
    monkeypatch.setattr(art,'back',lambda *args,**kwargs:(b'back','image/png','cover-art-archive'))
    monkeypatch.setattr(art,'back_sized',lambda *args,**kwargs:(b'back','image/webp','cover-art-archive'))
    response=post(client,'/api/artwork/refresh',{})
    assert response.status_code==202
    for _ in range(120):
        job=client.get('/api/artwork/refresh').json['job']
        if job['state']!='running':
            break
        time.sleep(.01)
    assert job['state']=='complete'
    assert job['processed']==job['total']==1
    assert job['fronts']==job['backs']==1
    assert application.extensions['store'].setting('artwork_refresh_status')['state']=='complete'


def test_refresh_requires_authentication_and_nonempty_shelf(application,client):
    response=post(client,'/api/artwork/refresh',{})
    assert response.status_code==409
    assert client.get('/api/artwork/refresh').status_code==200


def test_refresh_rejects_duplicate_running_jobs(application,monkeypatch):
    store=application.extensions['store'];art=application.extensions['artwork']
    store.shelf(ALBUM,True)
    # Keep the worker from running so a second start must detect the active job.
    monkeypatch.setattr('app.artwork_refresh.threading.Thread.start',lambda self:None)
    jobs=ArtworkRefresh()
    jobs.start('owner',store,art)
    with pytest.raises(AppError,match='already running'):
        jobs.start('owner',store,art)
