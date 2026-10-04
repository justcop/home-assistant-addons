import ast
import asyncio
import inspect
import threading
import unittest
from pathlib import Path

PAYLOAD={'matches':[{'offset':2}], 'track':{'title':'Example','subtitle':'Artist','albumadamid':'99','trackadamid':'101','key':'shz','sections':[]}}


def recognition_function(factory):
    path=Path(__file__).resolve().parents[1]/'integrations.py'
    tree=ast.parse(path.read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='recognize_shazam')
    env=dict(Shazam=factory,asyncio=asyncio,inspect=inspect,DEBUG=False,log=lambda message:None)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'recognition','exec'),env)
    return env['recognize_shazam']


class ShazamCompatibilityTests(unittest.TestCase):
    def test_installed_legacy_client_without_context_manager_or_close(self):
        class Legacy:
            async def recognize(self,path): return PAYLOAD
        result=recognition_function(Legacy)('example.wav')
        self.assertEqual(result['title'],'Example')
        self.assertEqual(result['offset_seconds'],2)
        self.assertEqual(result['album_adamid'],'99')
        self.assertEqual(result['adamid'],'101')
        self.assertEqual(result['shazam_key'],'shz')

    def test_modern_client_closes_after_success_and_failure(self):
        for failure in (False,True):
            closed=[]
            class Modern:
                async def recognize(self,path):
                    if failure: raise RuntimeError('API failed')
                    return PAYLOAD
                async def close(self): closed.append(True)
            result=recognition_function(Modern)('example.wav')
            self.assertEqual(closed,[True])
            self.assertEqual(result is None,failure)

    def test_cleanup_failure_does_not_discard_a_valid_match(self):
        class Client:
            async def recognize(self,path): return PAYLOAD
            async def close(self): raise RuntimeError('cleanup failure')
        self.assertEqual(recognition_function(Client)('example.wav')['title'],'Example')

    def test_concurrent_requests_own_separate_clients_and_loops(self):
        clients=[]
        results=[]
        class Legacy:
            def __init__(self): clients.append(self)
            async def recognize(self,path):
                self.loop=asyncio.get_running_loop()
                await asyncio.sleep(.01)
                return PAYLOAD
        recognize=recognition_function(Legacy)
        jobs=[threading.Thread(target=lambda:results.append(recognize('example.wav'))) for _ in range(2)]
        for job in jobs: job.start()
        for job in jobs: job.join()
        self.assertEqual(len(results),2)
        self.assertTrue(all(r['title']=='Example' for r in results))
        self.assertIsNot(clients[0],clients[1])
        self.assertIsNot(clients[0].loop,clients[1].loop)


class AlbumSequenceTests(unittest.TestCase):
    def parser(self):
        path=Path(__file__).resolve().parents[1]/'integrations.py'
        tree=ast.parse(path.read_text())
        names={'_catalogue_identity','_next_album_track_from_rows'}
        nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in names]
        env={}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'catalogue','exec'),env)
        return env['_next_album_track_from_rows']

    def test_finds_next_track_by_apple_id_and_disc_order(self):
        rows=[
            {'wrapperType':'collection','collectionName':'Album'},
            {'wrapperType':'track','trackId':1,'artistName':'Artist','trackName':'A','discNumber':1,'trackNumber':1,'trackTimeMillis':100000,'collectionName':'Album'},
            {'wrapperType':'track','trackId':2,'artistName':'Artist','trackName':'B','discNumber':1,'trackNumber':2,'trackTimeMillis':110000,'collectionName':'Album'},
            {'wrapperType':'track','trackId':3,'artistName':'Artist','trackName':'C','discNumber':2,'trackNumber':1,'trackTimeMillis':120000,'collectionName':'Album'},
        ]
        result=self.parser()(rows,current_adamid='2')
        self.assertEqual(result['title'],'C')
        self.assertEqual(result['adamid'],3)
        self.assertEqual(result['duration'],120)

    def test_last_track_has_no_expected_successor(self):
        rows=[
            {'wrapperType':'track','trackId':1,'artistName':'Artist','trackName':'A','discNumber':1,'trackNumber':1},
        ]
        self.assertIsNone(self.parser()(rows,current_adamid=1))
