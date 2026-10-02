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
