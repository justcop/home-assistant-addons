import ast
import asyncio
import inspect
import threading
import unittest
from pathlib import Path

PAYLOAD={'matches':[{'offset':2}], 'track':{'title':'Example','subtitle':'Artist','sections':[]}}


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
