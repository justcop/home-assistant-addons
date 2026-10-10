"""Song-level MusicBrainz edition collapsing, layouts, vinyl-side playback and Spotify gating."""
import copy
import uuid

import pytest

from app.errors import AppError
from app.tracklist_variants import difference, group_releases, signature, title_key, vinyl_sides
from conftest import ALBUM, spotify_track, post

SPOTIFY_ID = 'z' * 22
REC = lambda n: str(uuid.uuid5(uuid.NAMESPACE_DNS, 'audioshelf-variant-'+str(n)))


def make_release(index, titles=('Opening', 'Closing'), country='GB', medium='CD', numbers=None):
    return {'id': REC(index), 'title': 'The Album', 'status': 'Official',
            'date': '2007-04-23', 'country': country, 'release-group': {'id': ALBUM},
            'media': [{'position':1,'format':medium,'track-count':len(titles),
                       'tracks':[{'position':i,'number':numbers[i-1] if numbers else str(i),
                                  'title':title,'length':180000 if i==1 else 240000,
                                  'recording':{'id': REC('rec'+str(i))}}
                                 for i,title in enumerate(titles,1)]}]}


def test_equivalent_pressings_collapse_despite_remaster_and_recording_ids():
    standard = make_release(1)
    remaster = make_release(2, ('Opening (2020 Remaster)', 'Closing (2020 Remaster)'), medium='12" Vinyl',
                            numbers=['A1', 'B1'])
    # Distinct MusicBrainz recording IDs on remasters should not alter the
    # musical contents: the lyrics and sequence are the same.
    remaster['media'][0]['tracks'][0]['recording']['id'] = REC('different')
    bonus = make_release(3, ('Opening','Closing','Bonus Demo'))
    reorder = make_release(4, ('Closing','Opening'))
    tracks = lambda r: [
        {'title':t['title'],'artist_names':[],'disc_number':m['position']}
        for m in r['media'] for t in m['tracks']]
    groups = group_releases([(r, tracks(r)) for r in (standard,remaster,bonus,reorder)])
    assert len(groups)==3
    assert groups[0]['pressings']==2 and len(groups[0]['layouts'])==2
    assert groups[0]['layouts'][1]['sides']==[
        {'disc_number':1,'side':'A','positions':[1]},
        {'disc_number':1,'side':'B','positions':[2]}]
    assert difference(groups[0]['tracks'],groups[1]['tracks'])['added']==['Bonus Demo']
    assert difference(groups[0]['tracks'],groups[2]['tracks'])['reordered']
    assert title_key('Closing (2020 Remaster)') == title_key('Closing')
    assert title_key('Closing (Live)') != title_key('Closing')
    assert title_key('Closing (Remix)') != title_key('Closing')


def test_multidisc_layout_kept_independent_of_same_song_sequence():
    cd = make_release(5)
    vinyl = make_release(6, medium='12" Vinyl', numbers=['A1','B1'])
    second = vinyl['media'][0]['tracks'].pop()
    vinyl['media'].append({'position':2,'format':'12" Vinyl','track-count':1,
                           'tracks':[dict(second,position=1,number='C1')]})
    flatten = lambda rel:[{'title':track['title'],'artist_names':[],'disc_number':medium['position']}
                          for medium in rel['media'] for track in medium['tracks']]
    groups=group_releases([(cd,flatten(cd)),(vinyl,flatten(vinyl))])
    assert len(groups)==1 and len(groups[0]['layouts'])==2
    assert groups[0]['layouts'][0]['discs']==[[1,2]]
    assert groups[0]['layouts'][1]['discs']==[[1,1],[2,1]]
    assert vinyl_sides(vinyl)==[], 'Never infer sides from disc boundaries or guess the midpoint'


def test_unknown_or_inconsistent_vinyl_numbers_do_not_create_fictitious_sides():
    bad = make_release(7,('Opening','Closing','Third'),medium='12" Vinyl',
                       numbers=['A1','B1','A2'])
    assert vinyl_sides(bad)==[]
    missing = make_release(8,medium='12" Vinyl',numbers=['A1','2'])
    assert vinyl_sides(missing)==[]
    cd = make_release(9,medium='CD',numbers=['A1','B1'])
    assert vinyl_sides(cd)==[]


@pytest.fixture
def variants(application,monkeypatch):
    mb=application.extensions['musicbrainz']
    spotify=application.extensions['spotify']
    store=application.extensions['store']
    vinyl=make_release(20,medium='12" Vinyl',numbers=['A1','B1'])
    cd=make_release(21,medium='CD',country='US')
    deluxe=make_release(22,('Opening','Closing','Bonus Demo'),country='GB')
    reorder=make_release(23,('Closing','Opening'),country='US')
    data=[vinyl,cd,deluxe,reorder]
    spotify.tokens={'refresh_token':'fixture'}
    calls={'candidates':0,'albums':0}
    def get(entity,params=None):
        if entity=='release':
            return {'releases':data,'release-count':len(data)}
        if entity.startswith('release/'):
            return next(r for r in data if r['id']==entity.split('/')[1])
        raise AssertionError(entity)
    monkeypatch.setattr(mb,'get',get)
    def candidates(album):
        calls['candidates']+=1
        return [{'id':SPOTIFY_ID}]
    monkeypatch.setattr(spotify,'candidates',candidates)
    def album(identifier):
        calls['albums']+=1
        return {'id':identifier,'name':'The Album (2024 Remaster)','release_date':'2024',
                'all_tracks':[spotify_track('Opening','a'*22),
                              spotify_track('Closing','b'*22,240000),
                              spotify_track('Bonus Demo','c'*22,240000)]}
    monkeypatch.setattr(spotify,'album',album)
    return store,vinyl,cd,deluxe,reorder,calls


def test_browse_without_spotify_then_compare_and_match_only_chosen_release(application,client,variants):
    store,vinyl,cd,deluxe,reorder,calls=variants
    before=copy.deepcopy(store.album(ALBUM))
    result=client.get('/api/albums/'+ALBUM+'/tracklist-variants')
    assert result.status_code==200,result.json
    groups=result.json['variants']
    assert len(groups)==3
    original=next(g for g in groups if g['track_count']==2 and g['pressings']==2)
    assert len(original['layouts'])==2
    assert any(layout['sides'] for layout in original['layouts'])
    assert result.json['checked']==4 and result.json['next_offset'] is None
    assert calls=={'candidates':0,'albums':0}
    assert store.album(ALBUM)==before,'Exploration must not alter canonical tracks'
    matched=post(client,'/api/albums/'+ALBUM+'/tracklist-matches',{'release_id':vinyl['id']})
    assert matched.status_code==200,matched.json
    assert matched.json['matches'][0]['matched_tracks']==2
    assert calls['candidates']==1 and calls['albums']>=1
    assert store.album(ALBUM)==before,'Preview Spotify matches must not mutate canonical tracks'


def test_select_vinyl_sides_preserves_disc_and_canonical_mapping(application,client,variants):
    store,vinyl,cd,deluxe,reorder,calls=variants
    album=post(client,'/api/albums/'+ALBUM+'/release',
               {'confirmed':True,'release_id':vinyl['id'],'spotify_album_id':SPOTIFY_ID,
                'split_sides':True})
    assert album.status_code==200,album.json
    assert album.json['playback_sides']==[
        {'disc_number':1,'side':'A','positions':[1]},
        {'disc_number':1,'side':'B','positions':[2]}]
    spotify=application.extensions['spotify']
    first,uris=spotify.play_tracks(album.json,'1:A')
    assert [t['title'] for t in first]==['Opening']
    assert uris==['spotify:track:'+'a'*22]
    second,uris=spotify.play_tracks(album.json,'1:B')
    assert [t['title'] for t in second]==['Closing']
    assert uris==['spotify:track:'+'b'*22]
    all_tracks,uris=spotify.play_tracks(album.json)
    assert len(all_tracks)==2
    assert spotify.play_tracks(album.json,1)[1]==[
        'spotify:track:'+'a'*22,'spotify:track:'+'b'*22]
    with pytest.raises(AppError,match='not available'):
        spotify.play_tracks(album.json,'1:C')
    with pytest.raises(AppError,match='valid disc'):
        spotify.play_tracks(album.json,'bad')
    # Switching away from this vinyl printing clears its side offsets.
    replacement=post(client,'/api/albums/'+ALBUM+'/release',
                     {'confirmed':True,'release_id':cd['id'],'spotify_album_id':SPOTIFY_ID})
    assert replacement.status_code==200,replacement.json
    assert replacement.json['playback_sides']==[]


def test_side_choice_validation_and_stored_album_intact_on_failure(application,client,variants):
    store,vinyl,cd,deluxe,reorder,calls=variants
    before=copy.deepcopy(store.album(ALBUM))
    invalid=post(client,'/api/albums/'+ALBUM+'/release',
                 {'confirmed':True,'release_id':cd['id'],'spotify_album_id':SPOTIFY_ID,
                  'split_sides':True})
    assert invalid.status_code==400
    assert store.album(ALBUM)==before
    assert 'vinyl sides' in invalid.json['error']


def test_invalid_tracklist_page_is_rejected(application,client,variants):
    for value in ('-1','banana','9999'):
        response=client.get('/api/albums/'+ALBUM+'/tracklist-variants?offset='+value)
        assert response.status_code==400
