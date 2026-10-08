import copy
from datetime import timedelta
from http.cookiejar import Cookie
import json
from pathlib import Path
import sys
import tempfile
import sqlite3
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from server import Store, apply_action
from lifestage import LifeStageClient, LifeStageError, Reauthenticate, derive_intermediate_secret, NoRedirects
from moneyhub import Moneyhub, today
from model import blank_state, problems


class FakeClient:
    """Synthetic provider fixture, never calls the real service."""
    def __init__(self):
        self.connected=True
        self.owner_hash='fixture-owner'
        self.login_token=None
        self.challenge_until=0
        self.requests=[]
        self.fail_on=None
        self.accounts=[{'uid':'source-bank','accountName':'Example bank','bankName':'Fixture bank',
                        'type':'cash','currency':'GBP','currentBalance':{'amount':123.45,'date':today().isoformat()}}]
        self.txns=[{'uid':'tx-1','accountUid':'source-bank','date':today().isoformat(),
                    'amount':-12.5,'currency':'GBP','description':'Example purchase','status':'posted'}]

    def records(self,path,params=None):
        self.requests.append((path,params))
        if self.fail_on and len(self.requests)>=self.fail_on:
            raise LifeStageError('Synthetic provider unavailable.')
        return copy.deepcopy(self.accounts if path=='/apiv2/accounts' else self.txns)

    def save(self):
        pass

    def forget(self):
        self.connected=False
        self.login_token=None


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'money.sqlite'
        self.store=Store(self.path)
        self.client=FakeClient()
        self.hub=Moneyhub(self.path,self.client)
        self.seed=blank_state()
        self.seed['accounts']=[{'id':'bank','name':'Local bank','type':'Cash','wrapper':'None','active':True}]
        self.store.mutate(0,'import',lambda s:apply_action(s,'import',{'state':self.seed}))

    def tearDown(self):
        if self.hub.worker:
            self.hub.worker.join(5)
        self.temp.cleanup()

    def sync(self,days=0):
        self.hub.start_sync((today()-timedelta(days=days)).isoformat(),today().isoformat())
        self.hub.worker.join(5)
        self.assertFalse(self.hub.worker.is_alive())

    def test_repeat_pull_updates_ids_without_touching_snapshots(self):
        original=self.store.read()
        self.sync(65)
        self.assertFalse(self.hub.status()['job']['error'])
        self.assertEqual(self.hub.transactions()['total'],1)
        self.client.txns[0]['amount']=-20.25
        self.sync()
        self.assertEqual(self.hub.transactions()['items'][0]['amount'],'-20.25')
        self.assertEqual(self.hub.transactions()['total'],1)
        self.assertEqual(self.store.read(),original)
        ranges=[p for path,p in self.client.requests if p]
        self.assertEqual(ranges[0]['endDate'],ranges[1]['startDate'])

    def test_partial_provider_failure_keeps_previous_data(self):
        self.sync()
        before=self.store.export()
        self.client.requests=[]
        self.client.fail_on=3
        self.client.accounts[0]['currentBalance']['amount']=999
        self.client.txns.append(dict(self.client.txns[0],uid='new-tx'))
        self.sync(65)
        self.assertTrue(self.hub.status()['job']['error'])
        self.assertEqual(self.store.export(),before)

    def test_malformed_transaction_is_atomic(self):
        self.sync()
        before=self.store.export()
        self.client.txns[0]['amount']='NaN'
        self.sync()
        self.assertEqual(self.store.export(),before)
        self.assertTrue(self.hub.status()['job']['error'])

    def test_deletions_pending_and_currencies_retained(self):
        self.sync()
        self.client.txns[0]['deleted']=True
        self.client.txns.append(dict(self.client.txns[0],uid='pending',deleted=False,status='pending',currency='EUR'))
        self.sync()
        self.assertEqual(self.hub.transactions()['total'],1)
        self.assertEqual(self.hub.transactions()['items'][0]['currency'],'EUR')
        self.assertEqual(len(self.store.export()['moneyhub']['transactions']),2)

    def test_mapping_draft_is_unconfirmed_and_legacy_import_preserves_records(self):
        self.sync()
        self.hub.map_account('source-bank','bank',-1)
        revision,sid=self.store.mutate(1,'moneyhub_draft',self.hub.fill_draft)
        state=self.store.read()[1]
        self.assertEqual(state['snapshots'][0]['balances']['bank']['amount'],'-123.45')
        self.assertFalse(state['snapshots'][0]['balances']['bank']['confirmed'])
        self.assertTrue(any('confirm this balance' in p for p in problems(state,state['snapshots'][0])))
        with self.assertRaises(LifeStageError):
            self.store.mutate(revision,'moneyhub_draft',self.hub.fill_draft)
        self.store.mutate(revision,'import',lambda s:apply_action(s,'import',{'state':self.seed,'confirm':'RESTORE'}))
        self.assertEqual(self.hub.transactions()['total'],1)

    def test_full_export_restore_preserves_data_and_checkpoint(self):
        self.sync()
        self.hub.map_account('source-bank','bank',1)
        export=self.store.export()
        other=Store(Path(self.temp.name)/'other.sqlite')
        other.mutate(0,'import',lambda s:apply_action(s,'import',{'state':export}))
        self.assertEqual(other.export(),export)
        changed=copy.deepcopy(export)
        changed['moneyhub']['transactions'][0]['amount']=4
        self.store.mutate(1,'import',lambda s:apply_action(s,'import',{'state':changed}))
        backup=self.store.backup(self.store.backups()[0]['id'])
        self.assertEqual(backup,export)
        self.assertNotIn('moneyhub',self.store.read()[1])
        self.assertNotIn('csrf_token',json.dumps(export))

    def test_invalid_restore_rolls_back_snapshot_and_provider_tables(self):
        self.sync()
        export=self.store.export()
        broken=copy.deepcopy(export)
        broken['moneyhub']['mappings']=[{'uid':'source-bank','account_id':'nonexistent','multiplier':1}]
        with self.assertRaises(LifeStageError):
            self.store.mutate(1,'import',lambda s:apply_action(s,'import',{'state':broken}))
        self.assertEqual(self.store.export(),export)

    def test_concurrent_sync_rejected_and_owner_isolated(self):
        self.hub.lock.acquire()
        try:
            with self.assertRaises(LifeStageError):
                self.hub.start_sync(today().isoformat(),today().isoformat())
        finally:
            self.hub.lock.release()
        self.sync()
        self.client.owner_hash='another-person'
        self.sync()
        self.assertFalse(self.client.connected)
        self.assertEqual(self.hub.transactions()['total'],1)

    def test_saved_login_not_returned_or_exported(self):
        self.hub.saved_email='example@example.test'
        self.hub.saved_password='saved-fixture-password'
        received=[]
        self.client.login=lambda email,password:received.append((email,password)) or {'needs_code':True}
        self.hub.authenticate({'email':'ignored@example.test','password':'ignored'})
        self.assertEqual(received,[('example@example.test','saved-fixture-password')])
        self.assertTrue(self.hub.status()['saved_login'])
        self.assertNotIn('saved-fixture-password',json.dumps(self.hub.status()))
        self.assertNotIn('saved-fixture-password',json.dumps(self.store.export()))

    def test_expired_session_keeps_imports_and_requests_reauthentication(self):
        self.sync()
        def expired(*args,**kwargs):
            raise Reauthenticate('Session expired.')
        self.client.records=expired
        self.sync()
        self.assertFalse(self.hub.status()['connected'])
        self.assertIn('expired',self.hub.status()['job']['error'])
        self.assertEqual(self.hub.transactions()['total'],1)

    def test_search_treats_sql_as_text(self):
        self.sync()
        self.assertEqual(self.hub.transactions("' OR 1=1 --")['total'],0)
        self.assertEqual(self.hub.transactions('EXAMPLE')['total'],1)


class LegacyUpgradeTests(unittest.TestCase):
    def test_upgrade_retains_old_data_once_and_restore_does_not_resurrect_it(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'money.sqlite'
            source=FakeClient()
            with sqlite3.connect(path) as con:
                con.execute('CREATE TABLE moneyhub_accounts(uid TEXT PRIMARY KEY, active INTEGER, pulled TEXT, body TEXT)')
                con.execute('CREATE TABLE moneyhub_transactions(uid TEXT PRIMARY KEY, account_uid TEXT, txn_date TEXT, modified TEXT, deleted INTEGER, pulled TEXT, body TEXT)')
                con.execute('CREATE TABLE moneyhub_settings(id INTEGER PRIMARY KEY, email TEXT, tenant_id TEXT, device_id TEXT)')
                con.execute('CREATE TABLE moneyhub_imports(id INTEGER PRIMARY KEY, pulled TEXT, start_date TEXT, end_date TEXT, account_count INTEGER, transaction_count INTEGER)')
                con.execute('INSERT INTO moneyhub_accounts VALUES(?,?,?,?)',('source-bank',1,'2026-10-08',json.dumps(source.accounts[0])))
                con.execute('INSERT INTO moneyhub_transactions VALUES(?,?,?,?,?,?,?)',('tx-1','source-bank','2026-10-08','',0,'2026-10-08',json.dumps(source.txns[0])))
                con.execute('INSERT INTO moneyhub_settings VALUES(1,?,?,?)',('example@example.test','public-tenant','device'))
                con.execute('INSERT INTO moneyhub_imports VALUES(1,?,?,?,?,?)',('2026-10-08T12:00:00+00:00','2026-07-01','2026-10-08',1,1))
            store=Store(path)
            first=store.export()
            self.assertEqual(len(first['moneyhub']['transactions']),1)
            self.assertEqual(len(first['moneyhub']['accounts']),1)
            self.assertEqual(first['moneyhub']['meta']['last_range']['start'],'2026-07-01')
            self.assertEqual(Store(path).export(),first)
            first['moneyhub']['transactions']=[]
            store.mutate(0,'import',lambda s:apply_action(s,'import',{'state':first}))
            self.assertEqual(Store(path).export()['moneyhub']['transactions'],[])
            with sqlite3.connect(path) as con:
                self.assertEqual(con.execute('SELECT count(*) FROM moneyhub_transactions').fetchone()[0],1)



class ClientTests(unittest.TestCase):
    def test_session_roundtrip_excludes_password_and_challenge(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'session.json'
            client=LifeStageClient(path)
            client.owner_hash='fixture-owner'
            client.csrf_token='fixture-csrf'
            client.login_token='must-not-persist'
            client.cookies.set_cookie(Cookie(0,'session','fixture-cookie',None,False,'asm.wpsa-app.com',True,False,'/',True,True,None,True,None,None,{}))
            client.save()
            saved=path.read_text()
            self.assertNotIn('must-not-persist',saved)
            restored=LifeStageClient(path)
            self.assertTrue(restored.connected)
            self.assertEqual(restored.device_id,client.device_id)
            self.assertEqual(next(iter(restored.cookies)).value,'fixture-cookie')
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            restored.forget()
            self.assertFalse(path.exists())

    def test_two_factor_gate_requires_authenticated_probe(self):
        with tempfile.TemporaryDirectory() as root:
            client=LifeStageClient(Path(root)/'session.json')
            calls=[]
            def transport(method,path,data=None,params=None):
                calls.append((method,path,data))
                client.csrf_token='fixture-csrf'
                if len(calls)==1:return {'data':{'loginToken':'fixture-challenge'}}
                if path=='/apiv2/accounts':return {'data':{'result':[],'meta':{}}}
                return {'email':'example@example.test'}
            client.api_call=transport
            self.assertTrue(client.login('example@example.test','fixture-password')['needs_code'])
            self.assertFalse(client.session_path.exists())
            self.assertNotIn('password',calls[0][2])
            self.assertFalse(client.verify('123456')['needs_code'])
            self.assertEqual(calls[1][2]['loginToken'],'fixture-challenge')
            self.assertEqual(calls[-1][1],'/apiv2/accounts')
            self.assertTrue(client.session_path.exists())

    def test_unknown_pagination_cannot_silently_truncate(self):
        with tempfile.TemporaryDirectory() as root:
            client=LifeStageClient(Path(root)/'session.json')
            client.api_call=lambda *a,**kw:{'data':{'result':[],'meta':{'next':'page-2'}}}
            with self.assertRaises(LifeStageError):client.records('/apiv2/accounts')

    def test_no_redirect_or_arbitrary_endpoint(self):
        with tempfile.TemporaryDirectory() as root:
            client=LifeStageClient(Path(root)/'session.json')
            with self.assertRaises(LifeStageError):client.api_call('GET','https://untrusted.test')
            with self.assertRaises(Reauthenticate):NoRedirects().redirect_request(None,None,302,'',{},'https://untrusted.test')


if __name__=='__main__':
    unittest.main()
