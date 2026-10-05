import pytest
from analytics.db import Database
from analytics.review import review


def versions(db):
    with db.connect() as conn:
        return {v['name']: dict(v) for v in conn.execute('SELECT * FROM resolved_variants')}


def test_choose_subset_of_four_versions_with_name_and_undo(tmp_path):
    db = Database(tmp_path / 'choices.sqlite3')
    names = ['Alpha', 'Alpha (2009 Remaster)', 'Alpha (Anniversary Edition)', 'Alpha (Anniversary Edition) (2009 Remaster)']
    db.apply_window(0, 1000, [dict(ts=100+i, title=n, artist='The Beatles', album='') for i,n in enumerate(names)])
    before = versions(db)
    # Four names are members of two existing groups, not four independent groups.
    assert len({v['group_id'] for v in before.values()}) == 2
    candidate = review(db)['rows'][0]
    assert len(candidate['versions']) == 4
    selected = [before[names[0]]['id'], before[names[2]]['id']]
    db.change_groups('merge_versions', selected, 'My Alpha')
    after = versions(db)
    assert after[names[0]]['group_id'] == after[names[2]]['group_id']
    assert after[names[1]]['group_id'] == before[names[1]]['group_id']
    assert after[names[3]]['group_id'] == before[names[3]]['group_id']
    with db.connect() as conn:
        assert conn.execute('SELECT name FROM groups WHERE id=?',(after[names[0]]['group_id'],)).fetchone()[0] == 'My Alpha'
    db.apply_window(0, 1000, [dict(ts=100+i, title=n, artist='The Beatles', album='') for i,n in enumerate(names)], reconcile=True)
    assert versions(db)[names[0]]['group_id'] == after[names[0]]['group_id']
    db.undo_grouping()
    assert {n:v['group_id'] for n,v in versions(db).items()} == {n:v['group_id'] for n,v in before.items()}


def test_name_validation_and_learning_undo(tmp_path):
    db = Database(tmp_path / 'choices.sqlite3')
    db.apply_window(0, 1000, [dict(ts=100+i, title=n, artist='The Beatles', album='') for i,n in enumerate(['Alpha','Alpha (Anniversary Edition)'])])
    candidate = review(db)['rows'][0]
    with pytest.raises(ValueError):
        db.change_groups('merge_versions', [v['id'] for v in candidate['versions']], ' ')
    with pytest.raises(ValueError):
        db.change_groups('merge_versions', [candidate['versions'][0]['id']], 'Alpha')
    with db.connect() as conn:
        old_name = conn.execute('SELECT name FROM groups WHERE id=?', (candidate['ids'][0],)).fetchone()[0]
    db.change_groups('merge_learn', candidate['ids'], 'Custom Alpha')
    with db.connect() as conn:
        assert conn.execute('SELECT name FROM groups WHERE id=?', (candidate['ids'][0],)).fetchone()[0] == 'Custom Alpha'
    db.undo_grouping()
    with db.connect() as conn:
        assert conn.execute('SELECT name FROM groups WHERE id=?', (candidate['ids'][0],)).fetchone()[0] == old_name
    assert db.meta('learned_rules') == []
