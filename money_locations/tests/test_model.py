import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from model import blank_state, new_snapshot, report, validate_state, problems
from server import Store, Conflict, apply_action, create_server


def fixture():
    state=blank_state()
    for aid,kind,wrapper in [('bank','Cash','None'),('stock','Stocks','ISA'),('p2p','P2P','ISA'),('sipp','Stocks','SIPP'),('mortgage','Mortgage','None')]:
        state['accounts'].append({'id':aid,'name':aid,'type':kind,'wrapper':wrapper,'active':True,'holder':'Example','access':'Restricted' if wrapper=='SIPP' else 'Accessible'})
    old=new_snapshot(state,'2026-01-01');old.update(status='final',activity_complete=True)
    for aid,amount in {'bank':10000,'stock':40000,'p2p':10000,'sipp':1000,'mortgage':-30000}.items():
        old['balances'][aid].update(amount=str(amount),confirmed=True)
    state['snapshots'].append(old)
    current=new_snapshot(state,'2026-02-01');current['balances']=copy.deepcopy(old['balances']);current['activity_complete']=True
    state['snapshots'].append(current)
    return state,current


class Calculations(unittest.TestCase):
    def test_sipp_transfer_relief_growth(self):
        st,s=fixture()
        s['balances']['bank']['amount']='9200'
        s['balances']['sipp'].update(amount='2050',contribution='800',relief='200')
        r=report(st,s)
        self.assertEqual(r['net_return'],50)
        self.assertEqual(r['tax_benefits'],200)
        self.assertEqual(r['savings'],0)
        self.assertEqual(r['balance_change'],250)

    def test_hmrc_refund_not_savings_or_growth(self):
        st,s=fixture();s['balances']['bank']['amount']='10200';s['pension_refund']='200'
        r=report(st,s)
        self.assertEqual((r['savings'],r['net_return'],r['tax_benefits']),(0,0,200))

    def test_investment_transfer_cancels(self):
        st,s=fixture()
        s['balances']['stock'].update(amount='45000',contribution='5000')
        s['balances']['p2p'].update(amount='5000',withdrawal='5000')
        self.assertEqual(report(st,s)['net_return'],0)
        self.assertEqual(report(st,s)['savings'],0)

    def test_mortgage_cost_is_investment_cost(self):
        st,s=fixture()
        # Income 3000, living costs 1000, mortgage 1000 (principal 400 + interest 600).
        s['income']={'Salary':'3000'};s['balances']['bank']['amount']='11000'
        s['balances']['mortgage']['amount']='-29600';s['mortgage_interest']='600'
        r=report(st,s)
        self.assertEqual((r['savings'],r['spending'],r['net_return']),(2000,1000,-600))
        self.assertEqual(r['mortgage_principal'],400)
        self.assertEqual(r['other_savings'],1600)
        self.assertEqual(r['groups']['Cash']['cost'],0)
        self.assertAlmostEqual(r['groups']['Stocks']['cost'],600*41000/51000,places=2)
        self.assertNotIn('reconciliation',r)
        self.assertAlmostEqual(r['savings_30d'],2000*30/31,places=2)

    def test_excluded_payment_deducted_once(self):
        st,s=fixture();s['income']={'Salary':'3000'};s['balances']['bank']['amount']='11000';s['excluded_payments']='500'
        r=report(st,s)
        self.assertEqual((r['savings'],r['spending'],r['adjusted_spending'],r['adjusted_income']),(1000,2000,1500,2500))

    def test_cash_interest_not_savings(self):
        st,s=fixture();s['balances']['bank'].update(amount='10025',interest='25')
        r=report(st,s);self.assertEqual((r['net_return'],r['savings']),(25,0))

    def test_capital_writeoff(self):
        st,s=fixture();s['balances']['bank'].update(amount='9000',capital='-1000')
        self.assertEqual(report(st,s)['savings'],0)

    def test_property_valuation_does_not_change_savings(self):
        st,s=fixture();st['valuations']=[{'date':'2026-01-01','value':'100000'},{'date':'2026-02-01','value':'110000'}]
        r=report(st,s);self.assertEqual(r['property_change'],10000);self.assertEqual(r['savings'],0)
        self.assertEqual(r['with_home']-r['net_worth'],110000)

    def test_draft_not_used_as_baseline(self):
        st,s=fixture();draft=new_snapshot(st,'2026-01-15');draft['balances']['bank']['amount']='1000000';st['snapshots'].append(draft)
        self.assertEqual(report(st,s)['previous_date'],'2026-01-01')

    def test_historical_group_flows_no_fake_account_returns(self):
        st,s=fixture();s['balances']['stock']['amount']='45000';s['balances']['bank']['amount']='5000'
        s['legacy']={'group_contributions':{'Stocks':'5000','P2P':'0'}}
        r=report(st,s);self.assertEqual(r['net_return'],0)
        self.assertTrue(all(a['net'] is None for a in r['accounts']))

    def test_unallocated_mortgage_cost(self):
        st,s=fixture()
        for snap in st['snapshots']:
            for aid in ('stock','p2p','sipp'):snap['balances'][aid]['amount']='0'
        s['mortgage_interest']='5';s['balances']['bank']['amount']='9995'
        r=report(st,s);self.assertEqual(r['unallocated_cost'],5);self.assertEqual(r['savings'],0)

    def test_missing_confirmation_blocks_final(self):
        st,s=fixture();s['balances']['stock']['confirmed']=False;s['status']='final'
        with self.assertRaises(ValueError):apply_action(st,'snapshot',{'snapshot':s})

    def test_duplicate_dates_rejected(self):
        st,s=fixture();s['status']='final';s['date']=st['snapshots'][0]['date']
        with self.assertRaises(ValueError):validate_state(st)

    def test_nonfinite_money_rejected(self):
        st,s=fixture();s['balances']['bank']['amount']='NaN'
        with self.assertRaises(ValueError):validate_state(st)

    def test_account_classification_locked(self):
        st,s=fixture();a=copy.deepcopy(st['accounts'][0]);a['type']='Stocks'
        with self.assertRaises(ValueError):apply_action(st,'account',{'account':a})

    def test_invalid_wrapper_and_access_rejected(self):
        st,_=fixture();st['accounts'][0]['wrapper']='=cmd'
        with self.assertRaises(ValueError):validate_state(st)
        st,_=fixture();st['accounts'][0]['access']='Anything'
        with self.assertRaises(ValueError):validate_state(st)

    def test_implausible_inferred_return_warns(self):
        st,s=fixture();s['balances']['stock']['amount']='60000'
        r=report(st,s)
        self.assertTrue(any('inferred return' in w and 'stock' in w for w in r['warnings']))

    def test_negative_inferred_spending_warns(self):
        st,s=fixture();s['balances']['bank']['amount']='11000'
        r=report(st,s)
        self.assertLess(r['adjusted_spending'],0)
        self.assertTrue(any('Inferred spending is negative' in w for w in r['warnings']))

    def test_new_mortgage_borrowing_capital_adjustment(self):
        st,s=fixture();s['balances']['mortgage'].update(amount='-31000',capital='-1000');s['balances']['bank'].update(amount='11000',capital='1000')
        r=report(st,s);self.assertEqual(r['mortgage_principal'],0);self.assertEqual(r['savings'],0)

    def test_inactive_nonzero_balance_still_requires_closure(self):
        st,s=fixture();st['accounts'][0]['active']=False
        new=new_snapshot(st,'2026-03-01')
        self.assertIn('bank',new['required_accounts'])
        self.assertTrue(any('bank' in issue for issue in problems(st,new)))

    def test_allocation_pennies_are_nonnegative_and_reconcile(self):
        st,s=fixture()
        for a in st['accounts']:a['type']='Stocks'
        for b in s['balances'].values():b['amount']='10'
        s['mortgage_interest']='0.03'
        r=report(st,s)
        self.assertTrue(all(a['cost']>=0 for a in r['accounts']))
        self.assertAlmostEqual(sum(a['cost'] for a in r['accounts']),0.03)

    def test_import_ids_cannot_contain_html(self):
        st,s=fixture();st['accounts'][0]['id']='x\" onclick=\"alert(1)'
        with self.assertRaises(ValueError):validate_state(st)

    def test_correcting_prior_balance_recalculates_next_period(self):
        st,s=fixture();before=report(st,s)
        st['snapshots'][0]['balances']['bank']['amount']='9900'
        after=report(st,s)
        self.assertEqual(after['savings']-before['savings'],100)

    def test_backdated_final_snapshot_requires_later_period_reopened(self):
        st,s=fixture();candidate=copy.deepcopy(s);candidate['status']='final';apply_action(st,'snapshot',{'snapshot':candidate})
        middle=new_snapshot(st,'2026-01-15');middle['activity_complete']=True
        for aid,b in middle['balances'].items():
            b['amount']=st['snapshots'][0]['balances'][aid]['amount'];b['confirmed']=True
        st['snapshots'].append(middle)
        candidate=copy.deepcopy(middle);candidate['status']='final'
        with self.assertRaisesRegex(ValueError,'later final snapshot'):
            apply_action(st,'snapshot',{'snapshot':candidate})

    def test_reopen_reopens_downstream_snapshots_and_preserves_data(self):
        st,s=fixture();candidate=copy.deepcopy(s);candidate['status']='final';apply_action(st,'snapshot',{'snapshot':candidate})
        later=new_snapshot(st,'2026-03-01');later['activity_complete']=True
        for aid,b in later['balances'].items():
            b['amount']=s['balances'][aid]['amount'];b['confirmed']=True
        later['status']='final';st['snapshots'].append(later)
        count=apply_action(st,'reopen_snapshot',{'id':s['id']})
        self.assertEqual(count,2)
        self.assertEqual(s['status'],'draft')  # detached client payload is unchanged by server-side reopening
        stored=[x for x in st['snapshots'] if x['date']>='2026-02-01']
        self.assertTrue(all(x['status']=='draft' for x in stored))
        self.assertEqual(stored[0]['balances']['bank']['amount'],'10000')

    def test_final_snapshot_cannot_be_downgraded_or_moved_without_reopen(self):
        st,s=fixture();candidate=copy.deepcopy(s);candidate['status']='final';apply_action(st,'snapshot',{'snapshot':candidate})
        final=copy.deepcopy(next(x for x in st['snapshots'] if x['id']==s['id']))
        final['status']='draft'
        with self.assertRaisesRegex(ValueError,'Reopen as draft'):
            apply_action(st,'snapshot',{'snapshot':final})
        final['status']='final';final['date']='2026-02-02'
        with self.assertRaisesRegex(ValueError,'Reopen this snapshot'):
            apply_action(st,'snapshot',{'snapshot':final})


class Persistence(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'test.sqlite';self.store=Store(self.path)
    def tearDown(self):self.tmp.cleanup()
    def test_roundtrip_conflict_and_backup(self):
        st,_=fixture();self.store.mutate(0,'import',lambda s:s.update(st))
        self.assertEqual(self.store.read(),(1,st))
        with self.assertRaises(Conflict):self.store.mutate(0,'stale',lambda s:s.clear())
        self.assertEqual(self.store.read()[1],st)
        self.assertEqual(self.store.backup(self.store.backups()[0]['id']),blank_state())
        self.assertEqual(Store(self.path).read()[1],st)
    def test_invalid_restore_is_atomic(self):
        with self.assertRaises(ValueError):self.store.mutate(0,'import',lambda s:s.update({'accounts':[{}]}))
        self.assertEqual(self.store.read()[0],0)
    def test_unauthenticated_post_is_rejected_before_large_body_parse(self):
        server=create_server(self.path,'127.0.0.1',0,True,password='secret');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_address[1]}'
        try:
            req=Request(base+'/api/action',data=b'{' + b'x'*5000,headers={'Content-Type':'application/json','X-Money-Request':'1'})
            with self.assertRaises(HTTPError) as cm:urlopen(req)
            self.assertEqual(cm.exception.code,401)
            login=Request(base+'/api/auth/login',data=json.dumps({'password':'x'*2000}).encode(),headers={'Content-Type':'application/json','X-Money-Request':'1'})
            with self.assertRaises(HTTPError) as cm:urlopen(login)
            self.assertEqual(cm.exception.code,400)
        finally:server.shutdown();server.server_close();thread.join()

    def test_http_csrf_export_and_restore(self):
        server=create_server(self.path,'127.0.0.1',0,True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_address[1]}'
        try:
            data={'action':'import','revision':0,'state':fixture()[0]}
            req=Request(base+'/api/action',data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
            with self.assertRaises(HTTPError) as cm:urlopen(req)
            self.assertEqual(cm.exception.code,403)
            req.add_header('X-Money-Request','1')
            with urlopen(req) as response:self.assertEqual(response.status,200)
            with urlopen(base+'/api/export') as response:self.assertEqual(json.load(response),data['state'])
            with urlopen(base+'/') as response:self.assertIn(b'Money Locations',response.read())
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
