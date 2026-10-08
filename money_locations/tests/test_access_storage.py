import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from server import Store, Handler, create_server, persistent_path


class DurableStorage(unittest.TestCase):
    def test_migration_restart_reinstall_and_existing_shared_precedence(self):
        with tempfile.TemporaryDirectory() as root:
            data = Path(root)/'data'
            share = Path(root)/'share'
            legacy = Store(data/'money.sqlite')
            legacy.mutate(0, 'income_source', lambda s: s['income_sources'].append('Migration fixture'))
            target = persistent_path(data, share)
            self.assertEqual(Store(target).read(), legacy.read())
            self.assertEqual(Store(target).backups(), legacy.backups())
            self.assertTrue((data/'money.sqlite').exists())
            Store(target).mutate(1, 'income_source', lambda s: s['income_sources'].append('New shared entry'))
            self.assertEqual(Store(persistent_path(data, share)).read()[0], 2)
            shutil.rmtree(data)  # Supervisor uninstall removes the private volume.
            data.mkdir()
            self.assertIn('New shared entry', Store(persistent_path(data, share)).read()[1]['income_sources'])

    def test_corrupt_migration_stops_without_empty_target(self):
        with tempfile.TemporaryDirectory() as root:
            data=Path(root)/'data'
            data.mkdir()
            (data/'money.sqlite').write_text('broken database')
            with self.assertRaises(sqlite3.DatabaseError):
                persistent_path(data, Path(root)/'share')
            self.assertFalse((Path(root)/'share/money.sqlite').exists())


class Login(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = create_server(Path(self.temp.name)/'money.sqlite', host='127.0.0.1', port=0, local=True, password='fixture-password')
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.jar=CookieJar()
        self.client=build_opener(HTTPCookieProcessor(self.jar))

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        h={'Content-Type':'application/json', 'X-Money-Request':'1'}
        h.update(headers or {})
        return self.client.open(Request(self.base+path, data=json.dumps(body).encode() if body is not None else None, headers=h))

    def denied(self, path, code=401, body=None, headers=None):
        with self.assertRaises(HTTPError) as error:
            self.request(path, body, headers)
        self.assertEqual(error.exception.code, code)

    def test_login_export_restart_logout_and_cookie(self):
        for path in ('/api/state','/api/export','/api/csv','/api/backups','/api/backup?id=1','/api/moneyhub/status','/api/moneyhub/accounts','/api/moneyhub/transactions'):
            self.denied(path)
        self.denied('/api/moneyhub/login',body={'use_saved':True})
        self.denied('/api/action', body={'action':'income_source','revision':0,'name':'blocked'})
        self.denied('/api/auth/login', body={'password':'wrong'})
        response=self.request('/api/auth/login', {'password':'fixture-password'}, {'X-Ingress-Path':'/untrusted/'})
        cookie=response.headers['Set-Cookie']
        self.assertIn('Path=/;',cookie)
        self.assertIn('HttpOnly',cookie)
        self.assertIn('SameSite=Strict',cookie)
        self.assertEqual(self.request('/api/export').status,200)
        self.request('/api/auth/logout', {})
        self.denied('/api/export')
        self.request('/api/auth/login', {'password':'fixture-password'})
        self.server.sessions.clear()  # Restart/password change invalidate sessions.
        self.denied('/api/state')

    def test_rate_limit(self):
        for _ in range(5):
            self.denied('/api/auth/login',body={'password':'wrong'})
        self.denied('/api/auth/login',429,{'password':'fixture-password'})

    def test_no_password_fails_closed_in_production(self):
        self.server.local=False
        self.server.direct=True
        self.server.password_hash=None
        self.denied('/api/state')
        self.denied('/api/auth/login',403,{'password':''})

    def test_ingress_port_rejects_nonproxy(self):
        self.server.local=False
        self.denied('/api/auth/status',403)

    def test_proxy_cookie_prefix(self):
        fake=SimpleNamespace(client_address=('172.30.32.2',123),headers={'X-Ingress-Path':'/api/hassio_ingress/fixture'})
        self.assertIn('Path=/api/hassio_ingress/fixture/;',Handler.session_cookie(fake,'fixture',30))
        fake.headers={'X-Ingress-Path':'/bad; injected'}
        self.assertIn('Path=/;',Handler.session_cookie(fake,'fixture',30))


if __name__=='__main__':
    unittest.main()
