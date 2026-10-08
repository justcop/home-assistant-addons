"""Deterministic browser fixture, never used by the installed app."""
import io
import copy
import uuid
from PIL import Image, ImageDraw
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
    'web_password':'fixture-owner-password','allow_support_access':True,'spotify_client_id':'fixture','spotify_redirect_uri':'https://audioshelf.example/auth/spotify/callback'})
# HTTP is limited to this local browser fixture; production cookies stay Secure.
app.config['SESSION_COOKIE_SECURE']=False
group={'id':ALBUM,'title':'The Original Album','primary-type':'Album','secondary-types':[],
       'first-release-date':'2007-04-18','artist-credit':[{'artist':{'id':ARTIST,'name':'The Artist','sort-name':'Artist, The'}}]}
release={'id':RELEASE,'title':'The Original Album','status':'Official','date':'2007-04-23','country':'GB',
    'release-group':{'id':ALBUM},'media':[{'position':1,'format':'CD','track-count':2,'tracks':[
        {'position':1,'title':'Opening','length':180000,'recording':{'id':'rec1'}},
        {'position':2,'title':'Closing','length':240000,'recording':{'id':'rec2'}}]}]}


groups=[group]
releases={RELEASE:release}
if os.environ.get('AUDIOSHELF_TEST_VINYL'):
    titles=['First Light','Blue Hours','A Quiet Kind of Thunder','The Long Way Home','Circles in the Evening','Between the Lines','Another Country','Northern Lights','Slow Motion','The Other Side','Night Swimming','Everything in its Place','Sunday Morning','Small Hours','After the Rain','Open Windows']
    for index,title in enumerate(titles):
        record=copy.deepcopy(group)
        record['id']=str(uuid.uuid5(uuid.NAMESPACE_DNS,title))
        record['title']=title
        record['first-release-date']=str(2008+index)+'-04-18'
        if index>9:record['artist-credit'][0]['artist'].update(id=str(uuid.uuid5(uuid.NAMESPACE_DNS,'June & The Satellites')),name='June & The Satellites',**{'sort-name':'June & The Satellites'})
        edition=copy.deepcopy(release)
        edition.update(id=str(uuid.uuid5(uuid.NAMESPACE_DNS,title+'release')),title=title,**{'release-group':{'id':record['id']}})
        groups.append(record);releases[edition['id']]=edition
    store=app.extensions['store']
    store.catalogue(groups)
    for index,record in enumerate(groups):
        edition=next(r for r in releases.values() if r['release-group']['id']==record['id'])
        store.set_tracks(record['id'],{'id':edition['id'],'label':'GB · Vinyl'},[
            {'title':'Opening','disc_number':1,'track_number':1,'duration_ms':180000},
            {'title':'Closing','disc_number':1,'track_number':2,'duration_ms':240000}])
        if index<14:store.shelf(record['id'],True)


def mb_get(entity,params=None):
    if os.environ.get('AUDIOSHELF_TEST_VINYL'):
        if entity=='release-group':
            found=[g for g in groups if not params.get('artist') or g['artist-credit'][0]['artist']['id']==params['artist']]
            return {'release-groups':found,'release-group-count':len(found)}
        if entity.startswith('release-group/'):return next(g for g in groups if g['id']==entity.split('/')[1])
        if entity=='release':
            found=[r for r in releases.values() if not params.get('release-group') or r['release-group']['id']==params['release-group']]
            return {'releases':found,'release-count':len(found)}
        if entity.startswith('release/'):return releases[entity.split('/')[1]]
        if entity.startswith('artist/'):return next(g['artist-credit'][0]['artist'] for g in groups if g['artist-credit'][0]['artist']['id']==entity.split('/')[1])
    if entity=='artist':return {'artists':[{'id':ARTIST,'name':'The Artist','type':'Group','country':'GB'}]}
    if entity=='artist/'+ARTIST:return {'id':ARTIST,'name':'The Artist'}
    if entity=='series':return {'series':[{'id':RELEASE,'name':'The Artist core catalogue','type':'Release group'}]}
    if entity=='series/'+RELEASE:return {'id':RELEASE,'name':'The Artist core catalogue','type':'Release group',
        'relations':[{'release-group':{'id':ALBUM}}]}
    if entity=='release-group':return {'release-groups':[group], 'release-group-count':1}
    if entity=='release-group/'+ALBUM:return group
    if entity=='release':return {'releases':[release],'release-count':1}
    if entity=='release/'+RELEASE:
        if not {'media', 'recordings'}.intersection(params.get('inc', '').split('+')):
            return {key: value for key, value in release.items() if key != 'media'}
        return release
    raise AssertionError(entity)


play_calls=[]
playback_state={}
phone_available=True
album_id='s'*22
source={'id':album_id,'name':'The Original Album (Deluxe Edition)','release_date':'2007-04-23','tracks':{'next':None,'items':[
    spotify_track('Opening','a'*22),spotify_track('Closing','b'*22,240000),spotify_track('Japanese bonus track','c'*22)]}}


def spotify_api(method,path,params=None,body=None):
    if path=='me/player/devices':return {'devices':[{'id':'phone','name':'Fixture phone','type':'Smartphone','is_active':True,'is_restricted':False}] if phone_available else [{'id':'speaker','name':'Speaker','type':'Speaker','is_active':True,'is_restricted':False}]}
    if path=='search':return {'albums':{'items':[{'id':album_id}]}}
    if path=='albums/'+album_id:return source
    if path=='me/player':return {'device':{'id':'phone','name':'Fixture phone'},'shuffle_state':False,'repeat_state':'off',**playback_state}
    if path=='me/player/pause':
        playback_state['is_playing']=False
        return {}
    if path=='me/player/next':
        playback_state['item']['name']='Closing'
        playback_state['item']['id']='b'*22
        playback_state['progress_ms']=0
        return {}
    if path=='me/player/previous':
        playback_state['item']['name']='Opening'
        playback_state['item']['id']='a'*22
        playback_state['progress_ms']=0
        return {}
    if path=='me/player/play' and body is None:
        playback_state['is_playing']=True
        return {}
    if path=='me/player/play':
        play_calls.append(body)
        playback_state.update(is_playing=True,progress_ms=0,currently_playing_type='track',item={**next(t for t in source['tracks']['items'] if 'spotify:track:'+t['id']==body['uris'][0]),'album':{'id':album_id,'name':source['name'],'artists':[{'name':'The Artist'}],
        'images':[{'url':'https://i.scdn.co/image/ab67616d00001e02abcde1234','width':300}]}})
        return {}
    raise AssertionError(path)


cover_buffer=io.BytesIO()
Image.new('RGB',(50,50),'#486359').save(cover_buffer,format='PNG')
app.extensions['artwork'].download=lambda url:(cover_buffer.getvalue(),'image/png')
app.extensions['musicbrainz'].get=mb_get
app.extensions['spotify'].api=spotify_api
app.extensions['spotify']._save({'access_token':'fixture-only','refresh_token':'fixture-only','expires_in':3600})


# New account contexts use deterministic services while retaining independent tokens/data.
original_context=app.extensions['accounts'].context

def fixture_account_context(identifier):
    context=original_context(identifier)
    if identifier!='owner' and not getattr(context,'fixture_ready',False):
        context.fixture_ready=True
        context.musicbrainz.get=mb_get
        context.artwork.download=lambda url:(cover_buffer.getvalue(),'image/png')
        context.spotify._token_request=lambda body:{'access_token':identifier,'refresh_token':identifier,'expires_in':3600}
        context.spotify.api=lambda method,path,params=None,body=None: (
            {'devices':[{'id':identifier,'name':'Personal player','type':'Computer','is_active':True,'is_restricted':False}]}
            if path=='me/player/devices' else {})
    return context

app.extensions['accounts'].context=fixture_account_context


@app.post('/__test/playback-options')
def playback_options():
    from flask import request
    global phone_available
    phone_available=request.json.get('phone_available',phone_available)
    if 'external_album' in request.json:
        if request.json['external_album']:
            title=request.json.get('album','After the Rain (2025 Remaster)')
            playback_state.update(is_playing=True,progress_ms=21000,currently_playing_type='track',
                item={'id':'z'*22,'name':'A Different Song','artists':[{'name':'June & The Satellites'}],
                    'duration_ms':180000,'album':{'id':'z'*22,'name':title,'artists':[{'name':'June & The Satellites'}],
                        'images':[{'url':'https://i.scdn.co/image/ab67616d00001e02external98765','width':320}]}})
        else:
            playback_state.clear()
    if 'disc' in request.json:
        with app.extensions['store'].connect() as db:
            db.execute('UPDATE tracks SET disc_number=? WHERE album_id=? AND position=2', (request.json['disc'],ALBUM))
    return {'ok':True}


@app.get('/__test/current-code')
def current_code():
    from app.security import totp
    import time
    secret=app.extensions['security'].get('pending_totp')['secret']
    return {'code':totp(secret,int(time.time()//30))}


@app.get('/__test/play-calls')
def calls():return {'calls':play_calls}


@app.get('/__test/vinyl-cover/<int:index>')
def vinyl_cover(index):
    from flask import send_file
    palettes=[('#213f53','#d8bfa1'),('#a64532','#f3d69c'),('#d0ad65','#29362f'),('#364f42','#efc2a0'),('#c2cace','#433a52'),('#693d50','#eeb398')]
    bg,fg=palettes[index%len(palettes)]
    cover=Image.new('RGB',(500,500),bg);draw=ImageDraw.Draw(cover)
    if index%3==0:
        for radius in range(210,20,-25):draw.ellipse((250-radius,250-radius,250+radius,250+radius),outline=fg,width=10)
    elif index%3==1:
        for x in range(-300,600,70):draw.polygon([(x,500),(x+300,0),(x+330,0),(x+30,500)],fill=fg)
    else:
        draw.ellipse((80,65,420,405),fill=fg);draw.rectangle((0,260,500,500),fill=bg)
        for y in range(290,490,28):draw.line((0,y,500,y),fill=fg,width=7)
    buffer=io.BytesIO();cover.save(buffer,format='PNG');buffer.seek(0)
    return send_file(buffer,mimetype='image/png')


serve(app,host='127.0.0.1',port=int(os.environ.get('AUDIOSHELF_TEST_PORT','8101')),threads=4)
