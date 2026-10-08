"""Provider records kept separately from the monthly snapshot ledger."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

from lifestage import LifeStageClient, LifeStageError, Reauthenticate


def today():
    return datetime.now(ZoneInfo('Europe/London')).date()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def init_schema(con):
    con.execute('CREATE TABLE IF NOT EXISTS mh_accounts (uid TEXT PRIMARY KEY, body TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS mh_transactions (uid TEXT PRIMARY KEY, account_uid TEXT NOT NULL, day TEXT NOT NULL, body TEXT NOT NULL)')
    con.execute('CREATE INDEX IF NOT EXISTS mh_transaction_date ON mh_transactions(day)')
    con.execute('CREATE TABLE IF NOT EXISTS mh_mapping (uid TEXT PRIMARY KEY, account_id TEXT UNIQUE NOT NULL, multiplier INTEGER NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS mh_meta (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
    migrate_legacy(con)


def migrate_legacy(con):
    """Retain 0.4.0 raw imports and their source tables during the upgrade."""
    if con.execute("SELECT 1 FROM mh_meta WHERE key='legacy_migrated'").fetchone():
        return
    tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'moneyhub_accounts' not in tables:
        return
    for uid,body in con.execute('SELECT uid,body FROM moneyhub_accounts').fetchall():
        a=account_record(json.loads(body))
        con.execute('INSERT OR IGNORE INTO mh_accounts VALUES(?,?)',(a['uid'],json.dumps(a,allow_nan=False)))
    if 'moneyhub_transactions' in tables:
        for uid,body in con.execute('SELECT uid,body FROM moneyhub_transactions').fetchall():
            t=transaction_record(json.loads(body))
            con.execute('INSERT OR IGNORE INTO mh_transactions VALUES(?,?,?,?)',
                        (t['uid'],t['account_uid'],t['date'],json.dumps(t,allow_nan=False)))
    if 'moneyhub_settings' in tables:
        row=con.execute('SELECT email FROM moneyhub_settings WHERE id=1').fetchone()
        if row and row[0]:
            con.execute('INSERT OR IGNORE INTO mh_meta VALUES(?,?)',
                        ('owner_hash',json.dumps(hashlib.sha256(row[0].strip().lower().encode()).hexdigest())))
    if 'moneyhub_imports' in tables:
        row=con.execute('SELECT pulled,start_date,end_date FROM moneyhub_imports ORDER BY id DESC LIMIT 1').fetchone()
        if row:
            con.execute('INSERT OR IGNORE INTO mh_meta VALUES(?,?)',('last_sync',json.dumps(row[0])))
            con.execute('INSERT OR IGNORE INTO mh_meta VALUES(?,?)',('last_range',json.dumps({'start':row[1],'end':row[2]})))
    set_meta(con,'legacy_migrated',True)


def identifier(value):
    if not isinstance(value, str) or not value or len(value)>200:
        raise LifeStageError('LifeStage returned a record without a valid ID.')
    return value


def text_value(value, limit=10000):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value)>limit:
        raise LifeStageError('LifeStage returned an invalid text field.')
    return value


def amount(value):
    try:
        if isinstance(value, bool):
            raise ValueError()
        d=Decimal(str(value))
        if not d.is_finite() or abs(d)>Decimal('1e15'):
            raise ValueError()
        return format(d, 'f')
    except (InvalidOperation, ValueError):
        raise LifeStageError('LifeStage returned an invalid amount.') from None


def day_value(value):
    text=text_value(value)
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        raise LifeStageError('LifeStage returned an invalid date.') from None


def account_record(row):
    if not isinstance(row, dict):
        raise LifeStageError('Invalid LifeStage account.')
    current=row.get('currentBalance') or {}
    if not isinstance(current, dict):
        raise LifeStageError('Invalid LifeStage balance.')
    return {'uid': identifier(row.get('uid')), 'name': text_value(row.get('accountName')),
            'bank': text_value(row.get('bankName')), 'type': text_value(row.get('type')),
            'currency': text_value(row.get('currency')), 'balance': amount(current['amount']) if current.get('amount') is not None else None,
            'balance_date': day_value(current['date']) if current.get('date') else None,
            'closed': bool(row.get('deleted') or row.get('closed') or row.get('deletedConnection')),
            'raw': row}


def transaction_record(row):
    if not isinstance(row, dict):
        raise LifeStageError('Invalid LifeStage transaction.')
    return {'uid': identifier(row.get('uid')), 'account_uid': identifier(row.get('accountUid')),
            'date': day_value(row.get('date')), 'amount': amount(row.get('amount')),
            'currency': text_value(row.get('currency')), 'description': text_value(row.get('description')),
            'merchant': text_value(row.get('merchantName')), 'category': text_value(row.get('categoryId')),
            'status': text_value(row.get('status')), 'deleted': bool(row.get('deleted')), 'raw': row}


def export_data(con):
    return {'version': 1,
            'accounts': [json.loads(r[0])['raw'] for r in con.execute('SELECT body FROM mh_accounts ORDER BY uid')],
            'transactions': [json.loads(r[0])['raw'] for r in con.execute('SELECT body FROM mh_transactions ORDER BY day,uid')],
            'mappings': [dict(zip(('uid','account_id','multiplier'),r)) for r in con.execute('SELECT uid,account_id,multiplier FROM mh_mapping')],
            'meta': {r[0]:json.loads(r[1]) for r in con.execute("SELECT key,body FROM mh_meta WHERE key!='legacy_migrated'")}}


def validate_export(payload, state):
    if not isinstance(payload, dict) or payload.get('version') != 1:
        raise LifeStageError('Unsupported LifeStage backup format.')
    accounts=[account_record(r) for r in payload['accounts']]
    transactions=[transaction_record(r) for r in payload['transactions']]
    aids={a['uid'] for a in accounts}
    if len(aids)!=len(accounts) or len({t['uid'] for t in transactions})!=len(transactions):
        raise LifeStageError('Duplicate IDs in LifeStage backup.')
    local_ids={a['id'] for a in state['accounts']}
    seen=set()
    source_seen=set()
    for m in payload['mappings']:
        if m['uid'] not in aids or m['uid'] in source_seen or m['account_id'] not in local_ids or m['account_id'] in seen or type(m['multiplier']) is not int or m['multiplier'] not in (-1,1):
            raise LifeStageError('Invalid account mapping in LifeStage backup.')
        seen.add(m['account_id'])
        source_seen.add(m['uid'])
    if not isinstance(payload.get('meta'),dict):
        raise LifeStageError('Invalid LifeStage backup metadata.')
    meta=payload['meta']
    if meta.get('last_sync') is not None:
        try:
            datetime.fromisoformat(meta['last_sync'])
        except (TypeError,ValueError):
            raise LifeStageError('Invalid last-sync date in backup.') from None
    if meta.get('last_range') is not None:
        if not isinstance(meta['last_range'],dict):
            raise LifeStageError('Invalid sync range in backup.')
        for k in ('start','end'):
            day_value(meta['last_range'].get(k))
    if meta.get('owner_hash') is not None:
        text_value(meta['owner_hash'],200)
    return accounts,transactions


def restore_data(con, payload, state):
    accounts,transactions=validate_export(payload,state)
    legacy_migrated=bool(con.execute("SELECT 1 FROM mh_meta WHERE key='legacy_migrated'").fetchone())
    for table in ('mh_accounts','mh_transactions','mh_mapping','mh_meta'):
        con.execute('DELETE FROM '+table)
    insert_records(con, accounts, transactions)
    if legacy_migrated:
        set_meta(con,'legacy_migrated',True)
    for m in payload['mappings']:
        con.execute('INSERT INTO mh_mapping VALUES(?,?,?)',(m['uid'],m['account_id'],m['multiplier']))
    # No session/token/password ever belongs in an export. Background sync stays
    # off after a restore until the user explicitly enables it again.
    for k in ('owner_hash','last_sync','last_range'):
        if k in payload['meta']:
            set_meta(con,k,payload['meta'][k])


def set_meta(con,key,value):
    con.execute('INSERT OR REPLACE INTO mh_meta VALUES(?,?)',(key,json.dumps(value,allow_nan=False)))


def insert_records(con,accounts,transactions):
    for a in accounts:
        con.execute('INSERT OR REPLACE INTO mh_accounts VALUES(?,?)',(a['uid'],json.dumps(a,allow_nan=False)))
    for t in transactions:
        con.execute('INSERT OR REPLACE INTO mh_transactions VALUES(?,?,?,?)',
                    (t['uid'],t['account_uid'],t['date'],json.dumps(t,allow_nan=False)))


class Moneyhub:
    def __init__(self, path, client=None, saved_email="", saved_password=""):
        self.path=str(path)
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.saved_email=saved_email
        self.saved_password=saved_password
        self.client=client or LifeStageClient(Path(path).parent/'lifestage-session.json')
        self.lock=threading.Lock()
        self.state_lock=threading.Lock()
        self.job={'running':False, 'message':'', 'error':None}
        self.stop=threading.Event()
        self.next_background=time.time()+60
        self.worker=None
        self.last_login=0
        with closing(self.connect()) as con, con:
            init_schema(con)

    def connect(self):
        return sqlite3.connect(self.path,timeout=30)

    def meta(self):
        with closing(self.connect()) as con:
            return {r[0]:json.loads(r[1]) for r in con.execute('SELECT key,body FROM mh_meta')}

    def status(self):
        meta=self.meta()
        with closing(self.connect()) as con:
            count=con.execute('SELECT count(*) FROM mh_transactions WHERE json_extract(body,\'$.deleted\')=0').fetchone()[0]
            account_count=con.execute('SELECT count(*) FROM mh_accounts').fetchone()[0]
        with self.state_lock:
            job=dict(self.job)
        return {'saved_login':bool(self.saved_email and self.saved_password), 'connected':self.client.connected, 'needs_code':bool(self.client.login_token and self.client.challenge_until>time.time()),
                'last_sync':meta.get('last_sync'), 'last_range':meta.get('last_range'),
                'background':meta.get('background',False), 'transaction_count':count,
                'account_count':account_count, 'job':job}

    def authenticate(self, data, verify=False):
        if not self.lock.acquire(blocking=False):
            raise LifeStageError('A LifeStage operation is already running.')
        try:
            if time.time()-self.last_login<2:
                raise LifeStageError('Please wait a moment before trying again.')
            self.last_login=time.time()
            if verify:
                return self.client.verify(data.get('code',''))
            if (self.saved_email and self.saved_password) or data.get('use_saved') is True:
                if not self.saved_email or not self.saved_password:
                    raise LifeStageError('Add lifestage_email and lifestage_password in the add-on configuration, then restart.')
                data={'email':self.saved_email,'password':self.saved_password}
            email=data.get('email','')
            if not isinstance(email,str):
                raise LifeStageError('Enter your LifeStage email.')
            owner=hashlib.sha256(email.strip().lower().encode()).hexdigest()
            existing=self.meta().get('owner_hash')
            if existing and existing!=owner:
                raise LifeStageError('This dataset is linked to another LifeStage login. Use the original login to avoid mixing households.')
            self.client.owner_hash=owner
            return self.client.login(email,data.get('password',''))
        finally:
            self.lock.release()

    def disconnect(self):
        if not self.lock.acquire(blocking=False):
            raise LifeStageError('Wait for the current sync before disconnecting.')
        try:
            self.client.forget()
            self.settings(False)
        finally:
            self.lock.release()

    def settings(self, enabled):
        if not isinstance(enabled,bool):
            raise LifeStageError('Invalid background sync setting.')
        with closing(self.connect()) as con, con:
            set_meta(con,'background',enabled)
        self.next_background=time.time()+86400

    def accounts(self):
        with closing(self.connect()) as con:
            mapping={r[0]:{'account_id':r[1],'multiplier':r[2]} for r in con.execute('SELECT uid,account_id,multiplier FROM mh_mapping')}
            result=[]
            for row in con.execute('SELECT body FROM mh_accounts'):
                a=json.loads(row[0]);a.pop('raw',None)
                a['mapping']=mapping.get(a['uid'])
                result.append(a)
            return sorted(result,key=lambda a:(a['closed'],a['name'].lower()))

    def map_account(self, uid, account_id, multiplier):
        if type(multiplier) is not int or multiplier not in (1,-1):
            raise LifeStageError('Select a valid balance sign.')
        with closing(self.connect()) as con, con:
            con.execute('BEGIN IMMEDIATE')
            local=json.loads(con.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            if not con.execute('SELECT 1 FROM mh_accounts WHERE uid=?',(uid,)).fetchone():
                raise LifeStageError('Source account not found.')
            if not account_id:
                con.execute('DELETE FROM mh_mapping WHERE uid=?',(uid,));return
            if account_id not in {a['id'] for a in local['accounts']}:
                raise LifeStageError('Money Locations account not found.')
            if con.execute('SELECT 1 FROM mh_mapping WHERE account_id=? AND uid!=?',(account_id,uid)).fetchone():
                raise LifeStageError('That account is already mapped. Unmap it first to avoid counting it twice.')
            con.execute('INSERT OR REPLACE INTO mh_mapping VALUES(?,?,?)',(uid,account_id,multiplier))

    def transactions(self, search='', account='', offset=0):
        offset=max(0,int(offset))
        clauses=["json_extract(t.body,'$.deleted')=0"]
        args=[]
        if account:
            clauses.append('t.account_uid=?');args.append(account)
        if search:
            clauses.append("(instr(lower(json_extract(t.body,'$.description')),lower(?))>0 OR instr(lower(json_extract(t.body,'$.merchant')),lower(?))>0)")
            args += [search[:200],search[:200]]
        where=' AND '.join(clauses)
        with closing(self.connect()) as con:
            total=con.execute('SELECT count(*) FROM mh_transactions t WHERE '+where,args).fetchone()[0]
            rows=con.execute('SELECT t.body,a.body FROM mh_transactions t LEFT JOIN mh_accounts a ON t.account_uid=a.uid WHERE '+where+' ORDER BY t.day DESC,t.uid LIMIT 100 OFFSET ?',args+[offset]).fetchall()
        result=[]
        for raw,account_body in rows:
            t=json.loads(raw);t.pop('raw',None)
            t['account_name']=json.loads(account_body)['name'] if account_body else 'Unknown account'
            result.append(t)
        return {'items':result,'total':total,'offset':offset}

    def start_sync(self, start, end):
        try:
            first,last=date.fromisoformat(start),date.fromisoformat(end)
        except (ValueError,TypeError):
            raise LifeStageError('Choose valid start and end dates.') from None
        if first>last or last>today() or (last-first).days>3660:
            raise LifeStageError('Choose a range of up to ten years ending today or earlier.')
        if not self.client.connected:
            raise LifeStageError('Connect to LifeStage before syncing.')
        if not self.lock.acquire(blocking=False):
            raise LifeStageError('A LifeStage operation is already running.')
        with self.state_lock:
            self.job={'running':True,'message':'Fetching accounts…','error':None}
        self.worker=threading.Thread(target=self._sync,args=(first,last),daemon=True)
        self.worker.start()
        return {'started':True}

    def _sync(self, first, last):
        try:
            owner=self.meta().get('owner_hash')
            if owner and owner!=self.client.owner_hash:
                self.client.forget()
                raise Reauthenticate('The saved session belongs to a different dataset. Reconnect to LifeStage.')
            accounts=[account_record(r) for r in self.client.records('/apiv2/accounts')]
            if len({a['uid'] for a in accounts})!=len(accounts):
                raise LifeStageError('LifeStage returned duplicate account IDs.')
            transactions={}
            cursor=first
            while cursor<=last:
                until=min(last,cursor+timedelta(days=30))
                with self.state_lock:
                    self.job['message']=f'Fetching transactions: {cursor} to {until}…'
                rows=self.client.records('/apiV2/transactions',{'startDate':cursor.isoformat(),'endDate':until.isoformat()})
                for row in rows:
                    t=transaction_record(row)
                    # Never discard a provider row because its effective date
                    # differs from the statement date used by the endpoint.
                    transactions[t['uid']]=t
                if until==last:
                    break
                cursor=until  # overlap boundaries, then deduplicate by provider ID
            self.client.save()
            with closing(self.connect()) as con, con:
                con.execute('BEGIN IMMEDIATE')
                old={r[0] for r in con.execute('SELECT uid FROM mh_transactions')}
                insert_records(con,accounts,transactions.values())
                set_meta(con,'last_sync',stamp())
                set_meta(con,'last_range',{'start':first.isoformat(),'end':last.isoformat()})
                set_meta(con,'owner_hash',self.client.owner_hash)
            added=len(set(transactions)-old)
            with self.state_lock:
                self.job={'running':False,'message':f'Sync complete. {added} new transactions, {len(transactions)-added} refreshed.','error':None}
        except Reauthenticate as exc:
            self.client.forget()
            with self.state_lock:
                self.job={'running':False,'message':'Reconnect to continue syncing.','error':str(exc)}
        except Exception as exc:
            # Provider bodies and credentials never enter logs or status messages.
            message=str(exc) if isinstance(exc,LifeStageError) else 'Sync failed. Check storage and connectivity, then try again.'
            with self.state_lock:
                self.job={'running':False,'message':'Sync did not complete.','error':message}
        finally:
            self.next_background=time.time()+86400
            self.lock.release()

    def background_loop(self):
        while not self.stop.wait(30):
            if time.time()<self.next_background:
                continue
            self.next_background=time.time()+86400
            meta=self.meta()
            if not meta.get('background') or not self.client.connected:
                continue
            try:
                end=today()
                start=date.fromisoformat(meta['last_sync'][:10])-timedelta(days=7) if meta.get('last_sync') else end-timedelta(days=90)
                self.start_sync(min(start,end).isoformat(),end.isoformat())
            except (LifeStageError,ValueError):
                pass

    def fill_draft(self, state):
        from model import new_snapshot
        day=today().isoformat()
        if any(s['date']==day for s in state['snapshots']):
            raise LifeStageError('A snapshot already exists for today. Open it in History rather than creating a duplicate.')
        snapshot=new_snapshot(state,day)
        applied=0
        for source in self.accounts():
            mapping=source['mapping']
            if not mapping or source['closed'] or source['currency']!='GBP' or source['balance'] is None or source['type']=='properties':
                continue
            aid=mapping['account_id']
            if aid not in snapshot['balances']:
                continue
            balance=snapshot['balances'][aid]
            balance['amount']=format(Decimal(source['balance'])*mapping['multiplier'],'f')
            balance['confirmed']=False
            balance['source_note']='LifeStage balance dated '+(source['balance_date'] or 'unknown')+'; fetched '+str(self.meta().get('last_sync','unknown'))[:10]
            applied+=1
        if not applied:
            raise LifeStageError('Map at least one open GBP account with a balance to an active Money Locations account first.')
        snapshot['notes']='Balances imported from LifeStage. Review source dates and confirm each balance. Income and flows still need completing.'
        state['snapshots'].append(snapshot)
        return snapshot['id']
