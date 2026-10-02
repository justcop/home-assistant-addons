"""Deterministic browser fixture, never used by the installed app."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from waitress import serve
from app.server import create_app
from conftest import ALBUM,ARTIST,RELEASE,spotify_track

temporary=tempfile.TemporaryDirectory()
app=create_app({'data_directory':temporary.name+'/shelf','private_directory':temporary.name+'/private',
    'spotify_client_id':'fixture','spotify_redirect_uri':'https://audioshelf.example/auth/spotify/callback'})
group={'id':ALBUM,'title':'The Original Album','primary-type':'Album','secondary-types':[],
       'first-release-date':'2007-04-18','artist-credit':[{'artist':{'id':ARTIST,'name':'The Artist','sort-name':'Artist, The'}}]}
release={'id':RELEASE,'title':'The Original Album','status':'Official','date':'2007-04-23','country':'GB',
    'release-group':{'id':ALBUM},'media':[{'position':1,'format':'CD','track-count':2,'tracks':[
        {'position':1,'title':'Opening','length':180000,'recording':{'id':'rec1'}},
        {'position':2,'title':'Closing','length':240000,'recording':{'id':'rec2'}}]}]}


def mb_get(entity,params=None):
    if entity=='artist':return {'artists':[{'id':ARTIST,'name':'The Artist','type':'Group','country':'GB'}]}
    if entity=='artist/'+ARTIST:return {'id':ARTIST,'name':'The Artist'}
    if entity=='release-group':return {'release-groups':[group], 'release-group-count':1}
    if entity=='release-group/'+ALBUM:return group
    if entity=='release':return {'releases':[release],'release-count':1}
    if entity=='release/'+RELEASE:return release
    raise AssertionError(entity)


play_calls=[]
album_id='s'*22
source={'id':album_id,'name':'The Original Album (Deluxe Edition)','release_date':'2007-04-23','tracks':{'next':None,'items':[
    spotify_track('Opening','a'*22),spotify_track('Closing','b'*22,240000),spotify_track('Japanese bonus track','c'*22)]}}


def spotify_api(method,path,params=None,body=None):
    if path=='search':return {'albums':{'items':[{'id':album_id}]}}
    if path=='albums/'+album_id:return source
    if path=='me/player':return {'device':{'id':'phone','name':'Fixture phone'},'shuffle_state':False,'repeat_state':'off'}
    if path=='me/player/play':play_calls.append(body);return {}
    raise AssertionError(path)


app.extensions['musicbrainz'].get=mb_get
app.extensions['spotify'].api=spotify_api
app.extensions['spotify']._save({'access_token':'fixture-only','refresh_token':'fixture-only','expires_in':3600})


@app.get('/__test/play-calls')
def calls():return {'calls':play_calls}


serve(app,host='127.0.0.1',port=int(os.environ.get('AUDIOSHELF_TEST_PORT','8101')),threads=4)
