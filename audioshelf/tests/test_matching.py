import pytest

from app.matching import align,candidate,normalize,track_score
from conftest import ALBUM,spotify_track


def test_deluxe_bonus_tracks_are_never_mapped(application):
    album=application.extensions['store'].album(ALBUM)
    tracks=[spotify_track('Opening','a'*22),spotify_track('Bonus demo','b'*22),
        spotify_track('Closing - Remastered 2017','c'*22,240000),spotify_track('Closing - Live','d'*22,240000)]
    result=align(album['tracks'],tracks,['The Artist'])
    assert [t['spotify_id'] for t in result]==['a'*22,'c'*22]
    assert all(t['verified'] for t in result)


@pytest.mark.parametrize('variant',['Opening - Live','Opening (Demo)','Opening - Acoustic',
                                  'Opening - Radio Edit','Opening - Remix','Opening - Instrumental'])
def test_alternative_recordings_not_silently_accepted(application,variant):
    track=application.extensions['store'].album(ALBUM)['tracks'][0]
    assert track_score(track,spotify_track(variant,'a'*22),['The Artist']) == (0,False)


def test_wrong_artist_and_unavailable_tracks_are_rejected(application):
    track=application.extensions['store'].album(ALBUM)['tracks'][0]
    source=spotify_track('Opening','a'*22)
    source['artists']=[{'name':'Tribute Band'}]
    assert track_score(track,source,['The Artist'])==(0,False)
    source['artists']=[{'name':'The Artist'}];source['is_playable']=False
    assert track_score(track,source,['The Artist'])==(0,False)


def test_same_title_with_different_duration_needs_review(application):
    track=application.extensions['store'].album(ALBUM)['tracks'][0]
    score,verified=track_score(track,spotify_track('Opening','a'*22,195000),['The Artist'])
    assert score>0 and not verified


def test_duplicate_titles_use_distinct_ordered_recordings():
    canonical=[{'position':1,'title':'Theme','duration_ms':60000}, {'position':2,'title':'Theme','duration_ms':120000}]
    sources=[spotify_track('Theme','a'*22,60000),spotify_track('Theme','b'*22,120000)]
    assert [t['spotify_id'] for t in align(canonical,sources,['The Artist'])]==['a'*22,'b'*22]


def test_missing_track_is_not_replaced_by_a_bonus_track(application):
    album=application.extensions['store'].album(ALBUM)
    result=align(album['tracks'],[spotify_track('Opening','a'*22),spotify_track('Extra','b'*22)],['The Artist'])
    assert result[0]['verified'] and result[1]['spotify_id'] is None


def test_clean_standard_edition_wins_equal_matching(application):
    album=application.extensions['store'].album(ALBUM)
    common={'id':'a'*22,'name':'The Album','release_date':'2007','all_tracks':[
        spotify_track('Opening','b'*22),spotify_track('Closing','c'*22,240000)]}
    deluxe={**common,'id':'d'*22,'name':'The Album (Deluxe Edition)','all_tracks':common['all_tracks']+[spotify_track('Bonus','e'*22)]}
    assert candidate(album,common)['score']>candidate(album,deluxe)['score']


def test_normalization_keeps_meaningful_version_labels():
    assert normalize('Closing (2017 Remastered)',True)=='closing'
    assert normalize('Closing - 2017 Remaster',True)=='closing'
    assert normalize('Closing - Live',True)=='closing live'


def test_latest_labelled_remaster_wins_even_with_bonus_tracks(application):
    album=application.extensions['store'].album(ALBUM)
    def edition(year,identifier,bonus=False):
        tracks=[spotify_track(f'Opening - Remastered {year}','a'*22),spotify_track(f'Closing - Remastered {year}','b'*22,240000)]
        if bonus:tracks.append(spotify_track('Bonus demo','c'*22))
        return {'id':identifier*22,'name':f'The Album (Remastered {year})','release_date':str(year),'all_tracks':tracks}
    old=edition(2009,'o');new=edition(2022,'n',True)
    assert candidate(album,new)['score']>candidate(album,old)['score']
    assert candidate(album,new)['verified']==2
    assert [t['spotify_id'] for t in candidate(album,new)['mappings']]==['a'*22,'b'*22]


def test_recent_reissue_date_does_not_prove_new_remaster(application):
    album=application.extensions['store'].album(ALBUM)
    tracks=[spotify_track('Opening','a'*22),spotify_track('Closing','b'*22,240000)]
    source={'id':'s'*22,'name':'The Album','release_date':'2025','all_tracks':tracks}
    assert candidate(album,source)['edition_year']==0


def test_dated_studio_mix_matches_but_dance_remix_still_rejected(application):
    track=application.extensions['store'].album(ALBUM)['tracks'][0]
    assert track_score(track,spotify_track('Opening - 2022 Mix','a'*22),['The Artist'])[1]
    assert track_score(track,spotify_track('Opening (2022 Stereo Mix)','a'*22),['The Artist'])[1]
    assert track_score(track,spotify_track('Opening - Club Remix','a'*22),['The Artist'])==(0,False)
    assert track_score(track,spotify_track('Opening - Live - 2022 Mix','a'*22),['The Artist'])==(0,False)


def test_incomplete_recent_remaster_does_not_beat_complete_older_edition(application):
    album=application.extensions['store'].album(ALBUM)
    old={'id':'o'*22,'name':'The Album','release_date':'2007','all_tracks':[spotify_track('Opening','a'*22),spotify_track('Closing','b'*22,240000)]}
    new={'id':'n'*22,'name':'The Album (2025 Remaster)','release_date':'2025','all_tracks':[spotify_track('Opening - 2025 Remaster','c'*22)]}
    assert candidate(album,old)['score']>candidate(album,new)['score']



def test_bonus_track_remaster_year_does_not_change_album_preference(application):
    album=application.extensions['store'].album(ALBUM)
    source={'id':'s'*22,'name':'The Album','release_date':'2007','all_tracks':[
        spotify_track('Opening','a'*22),spotify_track('Closing','b'*22,240000),spotify_track('Bonus - 2025 Remaster','c'*22)]}
    assert candidate(album,source)['edition_year']==0
