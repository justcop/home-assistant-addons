from unittest.mock import Mock

from app.musicbrainz import literal
from conftest import ALBUM,ARTIST,RELEASE


def group(identifier=ALBUM,title='The Album',secondary=None):
    return {'id':identifier,'title':title,'primary-type':'Album','secondary-types':secondary or [],
        'first-release-date':'2007','artist-credit':[{'artist':{'id':ARTIST,'name':'The Artist'}}]}


def test_artist_discography_paginates_filters_and_sorts(application,monkeypatch):
    mb=application.extensions['musicbrainz'];calls=[]
    second='a09b1a42-a90d-41b0-afcb-0e49652e2441'
    def get(entity,params=None):
        calls.append(params)
        if params['offset']==0:return {'release-groups':[group(secondary=['Live'])],'release-group-count':3}
        later=group(second,'Later');later['first-release-date']='2018'
        return {'release-groups':[later,group()],'release-group-count':3}
    monkeypatch.setattr(mb,'get',get)
    result=mb.artist_albums(ARTIST)
    assert [a['id'] for a in result]==[ALBUM,second]
    assert [c['offset'] for c in calls]==[0,1]


def test_release_pagination_increments_actual_page_size(application,monkeypatch):
    mb=application.extensions['musicbrainz'];offsets=[]
    def get(entity,params=None):
        offsets.append(params['offset'])
        return {'releases':[{'id':str(params['offset']),'title':'Album','date':'2007','country':'GB','media':[{'format':'CD'}]}], 'release-count':3}
    monkeypatch.setattr(mb,'get',get)
    assert len(mb.releases(ALBUM))==3
    assert offsets==[0,1,2]


def test_musicbrainz_requests_cache_and_wait_between_calls(application,monkeypatch):
    mb=application.extensions['musicbrainz'];waits=[]
    clock=Mock(side_effect=[100.,100.,100.2,100.2])
    monkeypatch.setattr('app.musicbrainz.time.monotonic',clock)
    monkeypatch.setattr('app.musicbrainz.time.sleep',lambda n:waits.append(n))
    response=Mock(status_code=200,ok=True);response.json.return_value={'id':ARTIST}
    remote=Mock(return_value=response);monkeypatch.setattr(mb.session,'get',remote)
    assert mb.get('artist/'+ARTIST)==mb.get('artist/'+ARTIST)
    mb.get('release-group/'+ALBUM)
    assert remote.call_count==2
    assert waits[0]==0 and .84<waits[1]<.86


def test_first_tracklist_selection_and_group_validation(application,monkeypatch):
    mb=application.extensions['musicbrainz'];store=application.extensions['store']
    with store.connect() as db:db.execute('DELETE FROM tracks')
    def get(entity,params=None):
        if entity=='release':return {'releases':[{'id':RELEASE,'title':'Album','date':'2007','country':'GB','media':[{'format':'CD'}]}],'release-count':1}
        return {'id':RELEASE,'title':'Album','status':'Official','country':'GB','release-group':{'id':ALBUM},'media':[
            {'position':1,'format':'CD','tracks':[{'title':'Opening','position':1,'length':180000,'recording':{'id':'recording'}}]}]}
    monkeypatch.setattr(mb,'get',get)
    assert mb.ensure_tracks(ALBUM)['tracks'][0]['title']=='Opening'
    assert not store.album(ALBUM)['canonical_reviewed']


def test_lucene_special_characters_are_literal():
    assert literal('AC/DC')=='"AC\\/DC"'
    assert literal('A:B + C')=='"A\\:B \\+ C"'
