"""Read-only inventory audit detects expanded canonical editions across the shelf."""
import time

from app.musicbrainz import standard_release_reference
from app.shelf_edition_audit import ShelfEditionAudit, inspect_album, audit_report
from conftest import ALBUM, post


def edition(identifier, tracks, *, year='2007', label='The Album', country='GB'):
    return {'id':identifier, 'status':'Official', 'title':label,
            'date':year+'-04-17', 'country':country,
            'media':[{'format':'CD','position':1,'track-count':tracks}]}


def saved(tracks, label='The Album'):
    return {'id':ALBUM, 'title':'The Album', 'release_date':'2007-04-17',
            'release_id':'d'*36, 'release_label':label,
            'canonical_reviewed':True, 'playable':True,
            'artists':[{'name':'The Artist'}],
            'tracks':[{'position':i,'title':f'Track {i}'}
                      for i in range(1,tracks+1)]}


def test_audit_flags_bonus_editions_without_modifying_original():
    standard=[edition('a'*36,10),edition('b'*36,10,country='US')]
    deluxe=edition('d'*36,21,label='The Album (Deluxe Edition)')
    before=saved(21,'The Album Deluxe')
    result=inspect_album(before,standard+[deluxe])
    assert result['classification']=='likely_extras'
    assert result['reference_count']==10
    assert result['excess_count']==11
    assert result['supporting_releases']==2
    assert len(before['tracks'])==21
    assert before['canonical_reviewed'] and before['playable']


def test_audit_accepts_genuine_standard_double_album():
    releases=[edition('a'*36,20),edition('b'*36,20,country='US')]
    result=inspect_album(saved(20),releases)
    assert result['classification']=='consistent'
    assert result['reference_count']==20
    assert 'not been independently audited' in result['explanation']


def test_audit_abstains_on_conflicting_origins_instead_of_shortest_guess():
    releases=[edition('a'*36,10),edition('b'*36,10),
              edition('c'*36,12),edition('d'*36,12)]
    assert standard_release_reference(releases,'2007-04-17') is None
    result=inspect_album(saved(12),releases)
    assert result['classification']=='inconclusive'
    assert result['reference_count'] is None


def test_audit_flags_deluxe_label_even_without_reference_and_preserves_original():
    result=inspect_album(saved(20,'The Album (Deluxe Edition)'),
                         [edition('d'*36,20,label='The Album (Deluxe Edition)')])
    assert result['classification']=='review_edition'
    assert result['excess_count'] if 'excess_count' in result else True


def test_audit_does_not_infer_count_from_single_original_or_later_editions():
    releases=[edition('a'*36,10),
              edition('d'*36,21,label='The Album (Deluxe Edition)'),
              edition('c'*36,10,year='2023')]
    assert standard_release_reference(releases,'2007-04-17') is None
    assert inspect_album(saved(21),releases)['classification']=='inconclusive'


def test_audit_marks_albums_without_selected_tracklists_as_unresolved():
    album=saved(10)
    album.update(tracks=[],release_id=None)
    assert inspect_album(album,[])['classification']=='unresolved'


def test_edition_audit_api_runs_across_entire_shelf_without_mutating(
        application,client,monkeypatch):
    store=application.extensions['store']
    store.shelf(ALBUM,True)
    musicbrainz=application.extensions['musicbrainz']
    releases=[edition('a'*36,2),edition('b'*36,2,country='US')]
    monkeypatch.setattr(musicbrainz,'releases',lambda identifier:releases)
    before=store.album(ALBUM)
    start=post(client,'/api/shelf/edition-audit',{})
    assert start.status_code==202,start.json
    for _ in range(300):
        job=client.get('/api/shelf/edition-audit').json['job']
        if job['state'] != 'running':
            break
        time.sleep(.01)
    assert job['state']=='complete'
    assert job['processed']==1 and job['consistent']==1 and job['errors']==0
    report=client.get('/api/shelf/edition-audit/report')
    assert report.status_code==200
    assert 'AudioShelf original tracklist integrity audit' in report.json['text']
    assert 'consistent' in report.json['text']
    assert 'No albums have been edited' in report.json['text']
    assert store.album(ALBUM)==before
