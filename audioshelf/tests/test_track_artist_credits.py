"""A mixed-performer album must match track-level MusicBrainz credits.

Yellow Submarine is a real-world example, but this tests general metadata
handling only; the matcher never branches on particular artist or album names.
"""
import pytest

from app.matching import candidate, track_assessment
from app.musicbrainz import release_tracks
from app.storage import Store
from conftest import ALBUM, RELEASE, spotify_track

SOUNDTRACK_TITLES = [
    'Yellow Submarine', 'Only A Northern Song', 'All Together Now',
    'Hey Bulldog', "It's All Too Much", 'All You Need Is Love',
    'Pepperland', 'Sea Of Time', 'Sea Of Holes', 'Sea Of Monsters',
    'March Of The Meanies', 'Pepperland Laid Waste',
    'Yellow Submarine In Pepperland'
]
DURATIONS = [
    159000, 204000, 130000, 191000, 385000, 231000,
    140000, 180000, 136000, 216000, 139000, 132000, 134000
]
SPOTIFY_ALBUM = 's' * 22


def soundtrack_release():
    medium = []
    for pos, (title, length) in enumerate(zip(SOUNDTRACK_TITLES, DURATIONS), 1):
        performer = 'The Beatles' if pos <= 6 else 'George Martin'
        credited = performer if pos <= 6 else 'George Martin and His Orchestra'
        medium.append({'position':pos, 'title':title, 'length':length,
            'artist-credit':[{'name': credited, 'artist': {'name': performer}}],
            'recording':{'id':str(pos), 'title':title}})
    return {'id':RELEASE, 'title':'Yellow Submarine', 'country':'GB',
        'date':'1969-01-17', 'status':'Official',
        'release-group':{'id':ALBUM},
        'media':[{'position':1, 'format':'CD', 'track-count':13, 'tracks':medium}]}


def spotify_source():
    tracks = []
    for pos, (title, length) in enumerate(zip(SOUNDTRACK_TITLES, DURATIONS), 1):
        performer = 'The Beatles' if pos <= 6 else 'George Martin'
        track = spotify_track(title+' - Remastered 2009',str(pos).zfill(22),length)
        track['artists']=[{'name':performer}]
        tracks.append(track)
    return {'id':SPOTIFY_ALBUM, 'name':'Yellow Submarine (Remastered)',
        'release_date':'1969', 'all_tracks':tracks}


def test_musicbrainz_track_performers_are_extracted_from_each_medium():
    tracks = release_tracks(soundtrack_release())
    assert len(tracks)==13
    assert tracks[0]['artist_names']==['The Beatles']
    assert tracks[6]['artist_names']==['George Martin and His Orchestra','George Martin']
    assert tracks[-1]['artist_names']==['George Martin and His Orchestra','George Martin']


def test_complete_soundtrack_matches_without_special_cases():
    album={'title':'Yellow Submarine','release_date':'1969',
           'artists':[{'name':'The Beatles'}],
           'tracks':[dict(t, position=i) for i,t in enumerate(release_tracks(soundtrack_release()),1)]}
    result=candidate(album,spotify_source())
    assert result['matched']==13 and result['verified']==13
    assert [m['spotify_id'] for m in result['mappings']]==[str(i).zfill(22) for i in range(1,14)]


def test_artist_safeguard_stays_strict_for_uncredited_and_wrong_credit():
    track=release_tracks(soundtrack_release())[6]
    source=spotify_track('Pepperland - Remastered 2009','a'*22,140000)
    source['artists']=[{'name':'George Martin'}]
    assert track_assessment(track,source,['The Beatles'])['verified']
    assert track_assessment({**track,'artist_names':[]},source,['The Beatles'])['reason']=='artist_mismatch'
    source['artists']=[{'name':'An unrelated tribute orchestra'}]
    assert track_assessment(track,source,['The Beatles'])['reason']=='artist_mismatch'
    source['artists']=[{'name':'George Martin'}]
    assert not track_assessment(track,{**source,'name':'Pepperland - Live'},['The Beatles'])['verified']
    assert not track_assessment(track,{**source,'duration_ms':175000},['The Beatles'])['verified']


def test_performer_credits_are_general_for_any_collaborative_album():
    track={'title':'Duet','duration_ms':160000,'artist_names':['Guest Singer']}
    guest=spotify_track('Duet','a'*22,160000)
    guest['artists']=[{'name':'Guest Singer'}]
    assert track_assessment(track,guest,['Headliner'])['verified']
    guest['artists']=[{'name':'Different Singer'}]
    assert not track_assessment(track,guest,['Headliner'])['verified']


def test_store_migration_keeps_shelf_and_hand_mappings(application):
    store=application.extensions['store']
    store.shelf(ALBUM,True)
    with store.connect() as db:
        db.execute("UPDATE tracks SET spotify_id=?,method='manual',verified=1 WHERE position=1",('m'*22,))
        db.execute('ALTER TABLE tracks DROP COLUMN artist_names')
        db.execute('PRAGMA user_version=4')
    upgraded=Store(store.directory,store.cache_directory)
    album=upgraded.album(ALBUM)
    assert album['on_shelf'] and album['tracks'][0]['spotify_id']=='m'*22
    assert album['tracks'][0]['method']=='manual' and album['tracks'][0]['verified']
    assert all(track['artist_names']==[] for track in album['tracks'])
    with upgraded.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==5


def test_selecting_soundtrack_maps_every_track_and_keeps_review(application,client,monkeypatch):
    store=application.extensions['store']
    musicbrainz=application.extensions['musicbrainz']
    spotify=application.extensions['spotify']
    release=soundtrack_release()
    spotify.tokens={'refresh_token':'fixture'}
    monkeypatch.setattr(musicbrainz,'releases',lambda album_id:[release])
    monkeypatch.setattr(musicbrainz,'get',lambda entity,params=None:release if entity=='release/'+RELEASE else None)
    monkeypatch.setattr(spotify,'candidates',lambda album:[{'id':SPOTIFY_ALBUM}])
    monkeypatch.setattr(spotify,'album',lambda identifier:spotify_source())
    response=client.get('/api/albums/'+ALBUM+'/releases?playable=1&offset=0')
    assert response.status_code==200,response.json
    assert len(response.json['releases'])==1
    assert response.json['releases'][0]['matched_tracks']==13
    selection=client.post('/api/albums/'+ALBUM+'/release',
        json={'release_id':RELEASE,'spotify_album_id':SPOTIFY_ALBUM,'confirmed':True},
        headers={'X-AudioShelf-Request':'1'})
    assert selection.status_code==200,selection.json
    album=store.album(ALBUM)
    assert album['playable'] and len(album['tracks'])==13
    assert album['tracks'][6]['artist_names'][-1]=='George Martin'
