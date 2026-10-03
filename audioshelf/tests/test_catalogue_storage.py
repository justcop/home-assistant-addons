import json
import sqlite3

import pytest

from app.musicbrainz import release_rank,release_tracks,studio
from app.storage import Store
from conftest import ALBUM,ARTIST,RELEASE


@pytest.mark.parametrize('secondary',['Live','Compilation','Remix','Soundtrack','Demo','DJ-mix'])
def test_discography_excludes_minor_and_nonstudio_releases(secondary):
    assert studio({'primary-type':'Album','secondary-types':[]})
    assert not studio({'primary-type':'Album','secondary-types':[secondary]})
    assert not studio({'primary-type':'EP','secondary-types':[]})


def test_default_country_order_and_standard_edition_preference():
    standard={'id':'s','title':'Album','date':'2007-04-23','country':'GB','media':[{'format':'CD'}]}
    japan={**standard,'id':'j','country':'JP','date':'2007-04-18'}
    deluxe={**standard,'id':'d','disambiguation':'deluxe edition','date':'2007-04-23'}
    remaster={**standard,'id':'r','date':'2017-01-01','disambiguation':'remastered'}
    assert sorted([japan,deluxe,remaster,standard],key=lambda r:release_rank(r,'2007-04-18'))[0]['id']=='s'


def test_original_two_disc_album_retains_both_discs():
    release={'media':[{'position':2,'format':'CD','tracks':[{'title':'Two','position':1,'recording':{'id':'2'}}]},
        {'position':1,'format':'CD','tracks':[{'title':'One','position':1,'recording':{'id':'1','isrcs':['GBTEST']}}]},
        {'position':3,'format':'DVD-Video','tracks':[{'title':'Video','position':1}]}]}
    tracks=release_tracks(release)
    assert [t['title'] for t in tracks]==['One','Two']
    assert tracks[0]['isrcs']==['GBTEST']


def test_shelf_persists_across_restart_and_remove_retains_mapping(application):
    store=application.extensions['store'];store.shelf(ALBUM,True)
    reloaded=Store(store.directory)
    assert reloaded.artists()[0]['id']==ARTIST
    assert reloaded.albums(owned=True)[0]['on_shelf']
    reloaded.shelf(ALBUM,False)
    assert reloaded.artists()==[]
    assert len(reloaded.album(ALBUM)['tracks'])==2


def test_collaborative_album_belongs_to_both_credited_artists(application):
    store=application.extensions['store']
    store.catalogue([{'id':ALBUM,'title':'The Album','first-release-date':'2007',
        'artist-credit':[{'artist':{'id':ARTIST,'name':'The Artist'}},{'artist':{'id':'other','name':'Other Artist'}}]}])
    store.shelf(ALBUM,True)
    assert {a['id'] for a in store.artists()}=={ARTIST,'other'}


def test_manual_mapping_survives_automatic_reresolution(application):
    store=application.extensions['store']
    with store.connect() as db:
        db.execute("UPDATE tracks SET spotify_id=?,method='manual',verified=1 WHERE album_id=? AND position=1",('a'*22,ALBUM))
    store.mapping(ALBUM,{'id':'z'*22,'name':'Other','mappings':[
        {'position':1,'spotify_id':'b'*22,'score':100,'verified':True},
        {'position':2,'spotify_id':'c'*22,'score':100,'verified':True}]})
    assert store.album(ALBUM)['tracks'][0]['spotify_id']=='a'*22
    assert store.album(ALBUM)['playable']


def test_changing_canonical_edition_clears_stale_mappings(application):
    store=application.extensions['store']
    with store.connect() as db:
        db.execute("UPDATE tracks SET spotify_id=?,verified=1,method='manual'",('a'*22,))
    store.set_tracks(ALBUM,{'id':RELEASE},[{'title':'New','disc_number':1,'track_number':1}],True)
    album=store.album(ALBUM)
    assert album['canonical_reviewed'] and not album['playable']
    assert album['tracks'][0]['spotify_id'] is None


def test_unresolved_manual_edition_can_be_fixed_by_automatic_matching(application):
    store=application.extensions['store']
    missing={'id':'a'*22,'name':'Wrong edition','mappings':[
        {'position':1,'spotify_id':None,'score':0,'verified':False},
        {'position':2,'spotify_id':'b'*22,'score':100,'verified':True}]}
    store.mapping(ALBUM,missing,manual=True)
    corrected={**missing,'id':'c'*22,'mappings':[
        {'position':1,'spotify_id':'d'*22,'score':100,'verified':True},
        {'position':2,'spotify_id':'e'*22,'score':100,'verified':True}]}
    store.mapping(ALBUM,corrected)
    tracks=store.album(ALBUM)['tracks']
    assert tracks[0]['spotify_id']=='d'*22
    assert tracks[1]['spotify_id']=='b'*22


def test_backup_is_valid_and_excludes_pending_authorization(application):
    store=application.extensions['store']
    with store.connect() as db:
        db.execute('INSERT INTO oauth_states VALUES (?,?,?)',('state','secret-verifier',99999999999))
    with sqlite3.connect(store.backup()) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('SELECT count(*) FROM tracks').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM oauth_states').fetchone()[0]==0
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM oauth_states').fetchone()[0]==1


def test_future_schema_does_not_get_downgraded(application):
    store=application.extensions['store']
    with store.connect() as db: db.execute('PRAGMA user_version=99')
    with pytest.raises(RuntimeError,match='newer AudioShelf'):Store(store.directory)
