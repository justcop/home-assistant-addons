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
        if entity.startswith('recording/'):
            return {'id':entity.split('/')[1], 'title':'', 'aliases':[], 'isrcs':[]}
        raise AssertionError(entity)
    monkeypatch.setattr(mb,'get',get)
    def candidates(album, broad=False):
        calls['candidates']+=1
        return [{'id':SPOTIFY_ID}]
    monkeypatch.setattr(spotify,'candidates',candidates)
    monkeypatch.setattr(spotify,'labelled_editions',lambda album:[])
    def album(identifier):
        calls['albums']+=1
        return {'id':identifier,'name':'The Album (2024 Remaster)','release_date':'2024',
                'all_tracks':[spotify_track('Opening','a'*22),
                              spotify_track('Closing','b'*22,240000),
                              spotify_track('Bonus Demo','c'*22,240000)]}
    monkeypatch.setattr(spotify,'album',album)
    return store,vinyl,cd,deluxe,reorder,calls


def test_browse_only_shows_spotify_verified_grouped_tracklists(application,client,variants):
    store,vinyl,cd,deluxe,reorder,calls=variants
    before=copy.deepcopy(store.album(ALBUM))
    result=client.get('/api/albums/'+ALBUM+'/tracklist-variants')
    assert result.status_code==200,result.json
    # Each request is intentionally short to stay inside proxy timeouts.
    following=client.get('/api/albums/'+ALBUM+'/tracklist-variants?offset=2')
    assert following.status_code==200,following.json
    grouped={}
    for variant in result.json['variants']+following.json['variants']:
        previous=grouped.get(variant['signature'])
        if previous:
            previous['pressings']+=variant['pressings']
            previous['layouts'].extend(variant['layouts'])
        else:
            grouped[variant['signature']]=copy.deepcopy(variant)
    groups=list(grouped.values())
    assert len(groups)==2, 'Equivalent tracklists from separate pages should consolidate'
    original=next(g for g in groups if g['track_count']==2)
    assert original['pressings']==2 and len(original['layouts'])==2
    assert all(layout['spotify_matches'] for g in groups for layout in g['layouts'])
    assert all(match['matched_tracks']==g['track_count'] for g in groups
               for layout in g['layouts'] for match in layout['spotify_matches'])
    assert any(layout['sides'] for layout in original['layouts'])
    rejected=result.json['rejected']+following.json['rejected']
    assert len(rejected)==1
    assert rejected[0]['track_count']==2
    assert rejected[0]['missing']>0
    assert rejected[0]['tracks'], 'Show which recording prevented a match'
    assert result.json['checked']==2 and result.json['next_offset']==2
    assert following.json['checked']==2 and following.json['next_offset'] is None
    assert all(len(x)==64 for g in groups for layout in g['layouts']
               for match in layout['spotify_matches'] for x in [match['recording_signature']])
    assert calls['candidates']>=1 and calls['albums']>=1
    assert store.album(ALBUM)==before, 'Spotify probing must not mutate shelf tracks'
    matched=post(client,'/api/albums/'+ALBUM+'/tracklist-matches',{'release_id':vinyl['id']})
    assert matched.status_code==200,matched.json
    assert matched.json['matches'][0]['matched_tracks']==2
    assert matched.json['failure'] is None
    assert store.album(ALBUM)==before, 'Rechecking must not mutate canonical tracks'


def test_edition_picker_only_counts_gb_worldwide_and_us_releases(application,client,variants):
    store,vinyl,cd,deluxe,reorder,calls=variants
    vinyl['country']='FR'
    cd['country']='US'
    deluxe['country']='XW'
    reorder['country']='GB'
    response=client.get('/api/albums/'+ALBUM+'/tracklist-variants')
    assert response.status_code==200,response.json
    assert response.json['all_total']==4
    assert response.json['total']==3
    assert response.json['checked']==2
    assert response.json['next_offset']==2
    last=client.get('/api/albums/'+ALBUM+'/tracklist-variants?offset=2')
    assert last.json['checked']==1 and last.json['next_offset'] is None
    assert response.json['countries']==['GB','XW','US']
    assert all(layout['country']!='FR' for group in response.json['variants']
               for layout in group['layouts'])


def test_scanning_continues_across_all_eligible_pages(application,client,variants,monkeypatch):
    store,vinyl,cd,deluxe,reorder,calls=variants
    mb=application.extensions['musicbrainz']
    extra=[make_release(100+i,country='XW') for i in range(9)]
    releases=[vinyl,cd,deluxe,reorder,*extra]
    real_get=mb.get
    monkeypatch.setattr(mb,'releases',lambda album_id:releases)
    def get(entity,params=None):
        if entity.startswith('release/'):
            selected=next((r for r in extra if r['id']==entity.split('/')[1]),None)
            if selected is not None:return selected
        return real_get(entity,params)
    monkeypatch.setattr(mb,'get',get)
    checked=0
    cursor=0
    first_page=client.get('/api/albums/'+ALBUM+'/tracklist-variants?offset=0')
    assert first_page.status_code==200,first_page.json
    assert first_page.json['total']==13
    assert first_page.json['checked']==2
    assert first_page.json['next_offset']==2
    pages=[first_page.json]
    for offset in range(2,13,2):
        page=client.get('/api/albums/'+ALBUM+'/tracklist-variants?offset='+str(offset))
        assert page.status_code==200,page.json
        pages.append(page.json)
        assert page.json['checked']==min(2,13-offset)
        assert page.json['next_offset']==(offset+2 if offset+2<13 else None)
    assert sum(page['checked'] for page in pages)==13
    assert any(v['layouts'][0]['spotify_matches'] for page in pages for v in page['variants'])


def test_successful_page_cached_for_retry_without_refetching(application,client,variants):
    store,vinyl,cd,deluxe,reorder,calls=variants
    first=client.get('/api/albums/'+ALBUM+'/tracklist-variants?offset=0')
    assert first.status_code==200,first.json
    previous=dict(calls)
    again=client.get('/api/albums/'+ALBUM+'/tracklist-variants?offset=0')
    assert again.status_code==200 and again.json==first.json
    assert calls==previous, 'Retry must use cached verification results'


def test_long_unplayable_editions_do_not_trigger_many_recording_requests(application,client,variants,monkeypatch):
    store,vinyl,cd,deluxe,reorder,calls=variants
    mb=application.extensions['musicbrainz']
    extra=make_release(250,('Opening','Closing',*[f'Unrelated Track {i}' for i in range(3,37)]))
    lookup=mb.get
    requested=[]
    monkeypatch.setattr(mb,'releases',lambda album_id:[extra])
    def get(entity,params=None):
        if entity=='release/'+extra['id']:
            return extra
        if entity.startswith('recording/'):
            requested.append(entity)
        return lookup(entity,params)
    monkeypatch.setattr(mb,'get',get)
    result=client.get('/api/albums/'+ALBUM+'/tracklist-variants?offset=0')
    assert result.status_code==200,result.json
    assert result.json['rejected'][0]['track_count']==36
    assert result.json['rejected'][0]['missing']>=30
    assert not requested, 'Clearly incompatible editions must not trigger many costly recording lookups'


def test_unconnected_account_cannot_see_unverified_tracklists(application,client,variants):
    application.extensions['spotify'].tokens={}
    result=client.get('/api/albums/'+ALBUM+'/tracklist-variants')
    assert result.status_code==409
    assert 'Connect Spotify' in result.json['error']


def test_matching_fallback_when_structured_search_has_no_complete_album(application,client,variants,monkeypatch):
    store,vinyl,cd,deluxe,reorder,calls=variants
    spotify=application.extensions['spotify']
    queries=[]
    def candidates(album,broad=False):
        queries.append('broad' if broad else 'structured')
        return [{'id':('s' if not broad else 'z')*22}]
    def source(identifier):
        titles=['Not the Song'] if identifier.startswith('s') else ['Opening','Closing']
        return {'id':identifier,'name':'The Album','release_date':'2007',
                'all_tracks':[spotify_track(title,('x' if i==0 else 'y')*22,
                                            180000 if i==0 else 240000)
                              for i,title in enumerate(titles)]}
    monkeypatch.setattr(spotify,'candidates',candidates)
    monkeypatch.setattr(spotify,'album',source)
    result=client.get('/api/albums/'+ALBUM+'/tracklist-variants')
    assert result.status_code==200,result.json
    assert queries==['structured','broad']
    assert next(g for g in result.json['variants'] if g['track_count']==2)['layouts'][0][
        'spotify_matches'][0]['spotify_album_id']=='z'*22
    assert store.album(ALBUM)['tracks'][0]['spotify_id'] is None


def test_unverified_disc_layout_not_offered_even_with_same_song_sequence(application,client,variants):
    store,vinyl,cd,deluxe,reorder,calls=variants
    # Same musical fingerprint; different timings can make a physical pressing
    # unmatchable. Never let its layout be selected using another pressing's ID.
    cd['media'][0]['tracks'][1]['length']=999999
    result=client.get('/api/albums/'+ALBUM+'/tracklist-variants')
    assert result.status_code==200,result.json
    original=next(g for g in result.json['variants'] if g['track_count']==2)
    assert len(original['layouts'])==1
    assert original['layouts'][0]['release_id']==vinyl['id']
    assert original['layouts'][0]['spotify_matches']
    assert store.album(ALBUM)['tracks'][1]['duration_ms']==240000


def test_equivalent_layout_tries_later_pressing_if_first_cannot_match(application,client,variants):
    store,vinyl,cd,deluxe,reorder,calls=variants
    vinyl['media'][0]['format']='CD'
    vinyl['media'][0]['tracks'][1]['length']=999999
    response=client.get('/api/albums/'+ALBUM+'/tracklist-variants')
    later=client.get('/api/albums/'+ALBUM+'/tracklist-variants?offset=2')
    assert response.status_code==200,response.json
    assert later.status_code==200,later.json
    original=next(g for g in response.json['variants']+later.json['variants'] if g['track_count']==2)
    assert len(original['layouts'])==1, 'The later CD pressing rescues the initial rejected CD layout'
    layout=original['layouts'][0]
    assert layout['pressings']>=1
    assert layout['release_id']==cd['id'], 'The unverified first pressing must not be selected'
    assert layout['spotify_matches'][0]['matched_tracks']==2


def test_unmatched_album_has_reasons_and_no_selectable_tracklists(application,client,variants,monkeypatch):
    store,vinyl,cd,deluxe,reorder,calls=variants
    spotify=application.extensions['spotify']
    monkeypatch.setattr(spotify,'album',lambda identifier: {
        'id': identifier,'name':'Wrong album','release_date':'2007',
        'all_tracks':[spotify_track('Unrelated Song','x'*22)]})
    result=client.get('/api/albums/'+ALBUM+'/tracklist-variants')
    assert result.status_code==200,result.json
    assert result.json['variants']==[]
    assert result.json['rejected']
    assert all(issue['missing']>0 for issue in result.json['rejected'])
    assert all(issue['reason'] and issue['tracks'] for issue in result.json['rejected'])
    assert store.album(ALBUM)['tracks'][0]['spotify_id'] is None


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
