import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import game_library as g

class GameLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file=patch.object(g,"FILE",Path(self.tmp.name)/"games.json")
        self.file.start()
        self.addCleanup(self.file.stop)
        self.items=[{"id":"a","mappings":[{"kind":"game","value":"Game A"}]},{"id":"b"},{"id":"c"}]
        self.gallery=patch.object(g.artwork,"gallery",side_effect=lambda:{"items":self.items})
        self.gallery.start()
        self.addCleanup(self.gallery.stop)

    def test_detection_deduplication(self):
        g.observe("unknown","playing",100)
        g.observe("Game B","idle",100)
        g.observe("Game A™","playing",100)
        g.observe(" game a ","playing",170)
        rows=g.list_games()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["first_seen"],100)
        self.assertEqual(rows[0]["last_seen"],170)
        self.assertEqual(rows[0]["artwork_ids"],["a"])

    def test_rotation(self):
        g.observe("Game A","playing",100)
        g.update({"title":"Game A","artwork_ids":["a","b","c"],"rotation_minutes":10})
        rot=g.Rotation()
        for now,expected in [(10,"a"),(609,"a"),(610,"b"),(1210,"c"),(1810,"a")]:
            self.assertEqual(rot.select("Game A","playing",True,now),expected)
        self.assertIsNone(rot.select("Game A","idle",False,1900))
        self.assertEqual(rot.select("Game A","playing",True,2000),"a")

    def test_removed_artwork_and_empty_playlist(self):
        g.observe("Game A","playing",100)
        g.update({"title":"Game A","artwork_ids":["a","b"],"rotation_minutes":1})
        self.items.pop(0)
        self.assertEqual(g.list_games()[0]["artwork_ids"],["b"])
        self.assertEqual(g.Rotation().select("Game A","playing",True,0),"b")
        g.update({"title":"Game A","artwork_ids":[]})
        self.assertEqual(g.Rotation().select("Game A","playing",True,0),"ps5_default")

    def test_invalid_updates(self):
        g.observe("Game A","playing",100)
        for ids,minutes in [(["a","a"],10),(["missing"],10),(["a"],0),(["a"],121),(["a"],True)]:
            with self.assertRaises(ValueError):
                g.update({"title":"Game A","artwork_ids":ids,"rotation_minutes":minutes})
        self.assertEqual(g.list_games()[0]["artwork_ids"],["a"])
