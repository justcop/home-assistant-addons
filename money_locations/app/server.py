"""Dependency-free, password-protected web service with transactional local persistence."""
from copy import deepcopy
from contextlib import closing
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import csv
import hashlib
import hmac
from http.cookies import SimpleCookie
import secrets
import threading
import time
import io
import json
import mimetypes
import os
from pathlib import Path
import sqlite3
import uuid
from urllib.parse import urlsplit, parse_qs

from model import blank_state, validate_state, new_snapshot, problems, report, all_reports, money
from moneyhub import MoneyhubClient, MoneyhubError, extract_rows

ROOT = Path(__file__).parent
MAX_BODY = 20 * 1024 * 1024
MAX_LOGIN_BODY = 1024


class Conflict(Exception):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as con, con:
            con.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, body TEXT NOT NULL)')
            con.execute('CREATE TABLE IF NOT EXISTS backups (id INTEGER PRIMARY KEY, created TEXT, reason TEXT, revision INTEGER, body TEXT)')
            con.execute('CREATE TABLE IF NOT EXISTS moneyhub_settings (id INTEGER PRIMARY KEY CHECK(id=1), email TEXT NOT NULL, tenant_id TEXT NOT NULL, device_id TEXT NOT NULL)')
            con.execute('CREATE TABLE IF NOT EXISTS moneyhub_imports (id INTEGER PRIMARY KEY, pulled TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, account_count INTEGER NOT NULL, transaction_count INTEGER NOT NULL)')
            con.execute('CREATE TABLE IF NOT EXISTS moneyhub_accounts (uid TEXT PRIMARY KEY, active INTEGER NOT NULL, pulled TEXT NOT NULL, body TEXT NOT NULL)')
            con.execute('CREATE TABLE IF NOT EXISTS moneyhub_transactions (uid TEXT PRIMARY KEY, account_uid TEXT, txn_date TEXT, modified TEXT, deleted INTEGER NOT NULL, pulled TEXT NOT NULL, body TEXT NOT NULL)')
            con.execute('INSERT OR IGNORE INTO state VALUES (1, 0, ?)', (json.dumps(blank_state()),))
            con.execute("INSERT OR IGNORE INTO moneyhub_settings VALUES (1, '', '', '')")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def read(self):
        with closing(self.connect()) as con, con:
            revision, body = con.execute('SELECT revision, body FROM state WHERE id=1').fetchone()
        return revision, json.loads(body)

    def mutate(self, revision, reason, change):
        with closing(self.connect()) as con, con:
            con.execute('BEGIN IMMEDIATE')
            current, body = con.execute('SELECT revision, body FROM state WHERE id=1').fetchone()
            if revision != current:
                raise Conflict('This record changed in another tab. Reload before saving; your unsaved form is still visible.')
            state = json.loads(body)
            output = change(state)
            validate_state(state)
            con.execute('INSERT INTO backups(created,reason,revision,body) VALUES(?,?,?,?)',
                        (datetime.now(timezone.utc).isoformat(), reason, current, body))
            # Keep substantial checkpoints independently of frequent draft saves.
            for clause, limit in [("reason = 'draft'", 10), ("reason != 'draft'", 40)]:
                con.execute(f'DELETE FROM backups WHERE {clause} AND id NOT IN (SELECT id FROM backups WHERE {clause} ORDER BY id DESC LIMIT ?)', (limit,))
            con.execute('UPDATE state SET revision=?,body=? WHERE id=1', (current+1, json.dumps(state,allow_nan=False)))
        return current+1, output

    def backups(self):
        with closing(self.connect()) as con, con:
            return [dict(zip(('id','created','reason','revision'), r)) for r in con.execute('SELECT id,created,reason,revision FROM backups ORDER BY id DESC')]

    def backup(self, backup_id):
        with closing(self.connect()) as con, con:
            row = con.execute('SELECT body FROM backups WHERE id=?', (backup_id,)).fetchone()
        if not row:
            raise ValueError('Backup not found.')
        return json.loads(row[0])


    def moneyhub_settings(self):
        with closing(self.connect()) as con, con:
            email, tenant_id, device_id = con.execute(
                'SELECT email,tenant_id,device_id FROM moneyhub_settings WHERE id=1'
            ).fetchone()
        return {'email': email, 'tenant_id': tenant_id, 'device_id': device_id}

    def set_moneyhub_settings(self, email, tenant_id, device_id):
        email = str(email or '').strip()
        tenant_id = str(tenant_id or '').strip()
        device_id = str(device_id or '').strip()
        if len(email) > 320 or len(tenant_id) > 200 or len(device_id) > 200:
            raise ValueError('LifeStage connector settings are too long.')
        with closing(self.connect()) as con, con:
            con.execute(
                'UPDATE moneyhub_settings SET email=?,tenant_id=?,device_id=? WHERE id=1',
                (email, tenant_id, device_id),
            )

    def moneyhub_summary(self):
        with closing(self.connect()) as con, con:
            email, tenant_id, device_id = con.execute(
                'SELECT email,tenant_id,device_id FROM moneyhub_settings WHERE id=1'
            ).fetchone()
            account_count = con.execute('SELECT COUNT(*) FROM moneyhub_accounts').fetchone()[0]
            transaction_count = con.execute('SELECT COUNT(*) FROM moneyhub_transactions').fetchone()[0]
            row = con.execute(
                'SELECT pulled,start_date,end_date,account_count,transaction_count FROM moneyhub_imports ORDER BY id DESC LIMIT 1'
            ).fetchone()
        last_pull = None
        if row:
            last_pull = dict(zip(
                ('pulled','start_date','end_date','account_count','transaction_count'), row
            ))
        return {
            'email': email,
            'tenant_id': tenant_id,
            'device_id': device_id,
            'account_count': account_count,
            'transaction_count': transaction_count,
            'last_pull': last_pull,
        }

    def save_moneyhub_pull(self, start_date, end_date, active_payload, accounts_payload, transactions_payload):
        active_rows = extract_rows(active_payload, 'accounts', 'activeAccounts')
        account_rows = extract_rows(accounts_payload, 'accounts')
        transaction_rows = extract_rows(transactions_payload, 'transactions')
        pulled = datetime.now(timezone.utc).isoformat()
        active_ids = set()
        for row in active_rows:
            if isinstance(row, dict):
                uid = row.get('uid') or row.get('accountUid') or row.get('id')
            elif isinstance(row, (str, int)):
                uid = row
            else:
                uid = None
            if uid:
                active_ids.add(str(uid))
        stored_accounts = 0
        stored_transactions = 0
        with closing(self.connect()) as con, con:
            for row in account_rows:
                if not isinstance(row, dict):
                    continue
                uid = row.get('uid') or row.get('accountUid') or row.get('id')
                if not uid:
                    continue
                uid = str(uid)
                con.execute(
                    'INSERT INTO moneyhub_accounts(uid,active,pulled,body) VALUES(?,?,?,?) '
                    'ON CONFLICT(uid) DO UPDATE SET active=excluded.active,pulled=excluded.pulled,body=excluded.body',
                    (uid, 1 if uid in active_ids else 0, pulled, json.dumps(row, allow_nan=False)),
                )
                stored_accounts += 1
            for row in transaction_rows:
                if not isinstance(row, dict) or not row.get('uid'):
                    continue
                uid = str(row['uid'])
                con.execute(
                    'INSERT INTO moneyhub_transactions(uid,account_uid,txn_date,modified,deleted,pulled,body) VALUES(?,?,?,?,?,?,?) '
                    'ON CONFLICT(uid) DO UPDATE SET account_uid=excluded.account_uid,txn_date=excluded.txn_date,modified=excluded.modified,deleted=excluded.deleted,pulled=excluded.pulled,body=excluded.body',
                    (
                        uid,
                        str(row.get('accountUid') or ''),
                        str(row.get('date') or ''),
                        str(row.get('dateModified') or ''),
                        1 if row.get('deleted') else 0,
                        pulled,
                        json.dumps(row, allow_nan=False),
                    ),
                )
                stored_transactions += 1
            con.execute(
                'INSERT INTO moneyhub_imports(pulled,start_date,end_date,account_count,transaction_count) VALUES(?,?,?,?,?)',
                (pulled, start_date, end_date, stored_accounts, stored_transactions),
            )
        return {
            'pulled': pulled,
            'accounts_received': stored_accounts,
            'transactions_received': stored_transactions,
        }


    def moneyhub_recent_transactions(self, limit=20):
        limit = max(1, min(int(limit), 100))
        with closing(self.connect()) as con, con:
            rows = con.execute(
                'SELECT body FROM moneyhub_transactions WHERE deleted=0 '
                'ORDER BY txn_date DESC, modified DESC, uid DESC LIMIT ?',
                (limit,),
            ).fetchall()
        output = []
        for (body,) in rows:
            row = json.loads(body)
            output.append({
                'uid': row.get('uid'),
                'accountUid': row.get('accountUid'),
                'date': row.get('date'),
                'amount': row.get('amount'),
                'currency': row.get('currency'),
                'description': row.get('cleanDescription') or row.get('description') or row.get('transactionInformation'),
                'categoryId': row.get('categoryId'),
                'status': row.get('status'),
            })
        return output


def apply_action(state, action, data):
    if action == 'new':
        s = new_snapshot(state, data['date'])
        if any(v['date']==s['date'] for v in state['snapshots']):
            raise ValueError('A snapshot already exists on this date. Open it in History.')
        state['snapshots'].append(s)
        return s['id']
    if action == 'snapshot':
        s = deepcopy(data['snapshot'])
        prior = next((v for v in state['snapshots'] if v['id']==s['id']), None)
        if not prior:
            raise ValueError('Snapshot not found.')
        # Preserve the account checklist captured when this snapshot was created.
        s['required_accounts'] = prior['required_accounts']
        if prior['status']=='final' and s['status']!='final':
            raise ValueError('Use Reopen as draft so later periods are reopened safely as well.')
        if prior['status']=='final' and s['date']!=prior['date']:
            raise ValueError('Reopen this snapshot before changing its date.')
        if any(v['id']!=s['id'] and v['date']==s['date'] for v in state['snapshots']):
            raise ValueError('A snapshot already exists on this date.')
        if s['status']=='final' and prior['status']!='final':
            baseline = previous_snapshot(state, s)
            earlier_drafts = [v for v in state['snapshots']
                              if v['id']!=s['id'] and v['status']=='draft' and v['date']<s['date']
                              and (not baseline or v['date']>baseline['date'])]
            if earlier_drafts:
                raise ValueError('An earlier draft snapshot exists in this period. Finalise or discard it first.')
            later = [v for v in state['snapshots'] if v['id']!=s['id'] and v['status']=='final' and v['date']>s['date']]
            if later:
                raise ValueError('A later final snapshot exists. Reopen that snapshot first so affected periods can be reviewed.')
        state['snapshots'] = [s if v['id']==s['id'] else v for v in state['snapshots']]
        validate_state(state)
        if s['status']=='final':
            issues = problems(state,s)
            if issues:
                raise ValueError(' '.join(issues))
        return s['id']
    if action == 'reopen_snapshot':
        target = next((v for v in state['snapshots'] if v['id']==data['id']), None)
        if not target or target['status']!='final':
            raise ValueError('Only final snapshots can be reopened.')
        affected = [v for v in state['snapshots'] if v['status']=='final' and v['date']>=target['date']]
        for snapshot in affected:
            snapshot['status'] = 'draft'
        return len(affected)
    if action == 'delete_draft':
        s = next((v for v in state['snapshots'] if v['id']==data['id']), None)
        if not s or s['status']!='draft':
            raise ValueError('Only drafts can be deleted.')
        state['snapshots'].remove(s)
    elif action == 'account':
        a = deepcopy(data['account'])
        existing = next((v for v in state['accounts'] if v['id']==a.get('id')), None)
        if existing:
            used = any(a['id'] in s['balances'] for s in state['snapshots'])
            if used and any(a.get(k)!=existing.get(k) for k in ('type','wrapper','access')):
                raise ValueError('Classification is locked once an account has history. Create a new account for a different classification.')
            existing.update(a)
        else:
            a['id'] = uuid.uuid4().hex
            state['accounts'].append(a)
        return a['id']
    elif action == 'valuation':
        if data.get('value') in (None, ''):
            raise ValueError('Enter a home valuation.')
        v = {'date':data['date'], 'value':str(money(data['value'])), 'notes':data.get('notes','')}
        state['valuations'] = [x for x in state['valuations'] if x['date']!=v['date']] + [v]
    elif action == 'income_source':
        name = str(data['name']).strip()
        if not name or len(name)>100:
            raise ValueError('Enter a source name up to 100 characters.')
        if name not in state['income_sources']:
            state['income_sources'].append(name)
    elif action == 'import':
        imported = data['state']
        validate_state(imported)
        if state['snapshots'] and data.get('confirm') != 'RESTORE':
            raise ValueError('Type RESTORE to replace current data. A backup is retained.')
        state.clear()
        state.update(imported)
    else:
        raise ValueError('Unknown action.')


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *_):
        pass  # Do not log financial payloads or identifying paths.

    def authenticated(self):
        if self.server.local and not self.server.password_hash:
            return True
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            token = cookie['money_session'].value
        except (KeyError, ValueError):
            return False
        with self.server.auth_lock:
            expiry = self.server.sessions.get(token, 0)
            return expiry > time.time()

    def session_cookie(self, token, age):
        # Only the actual ingress proxy may supply the cookie path.
        path = self.headers.get('X-Ingress-Path', '/') if self.client_address[0] == '172.30.32.2' else '/'
        if not path.startswith('/') or any(c in path for c in ';\r\n'):
            path = '/'
        path = path.rstrip('/') + '/'
        return f'money_session={token}; Path={path}; HttpOnly; SameSite=Strict; Max-Age={age}'

    def allowed(self):
        expected = '127.0.0.1' if self.server.local else '172.30.32.2'
        return self.server.direct or self.client_address[0] == expected

    def reply(self, status, payload, content_type='application/json', attachment=None, cookie=None):
        if content_type=='application/json':
            payload = json.dumps(payload, allow_nan=False).encode()
        elif isinstance(payload,str):
            payload = payload.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        if attachment:
            self.send_header('Content-Disposition', 'attachment; filename="'+attachment+'"')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/health':
            self.reply(200, {'status':'ok'})
            return
        if not self.allowed():
            self.reply(403, {'error':'Access through Home Assistant is required.'})
            return
        try:
            if path == '/api/auth/status':
                self.reply(200, {'authenticated': self.authenticated(), 'configured': bool(self.server.password_hash) or self.server.local})
                return
            if path in ('/','/index.html','/app.js','/style.css','/icon.png'):
                name='index.html' if path=='/' else path[1:]
                self.reply(200,(ROOT/'static'/name).read_bytes(), mimetypes.guess_type(name)[0] or 'text/plain')
                return
            if path.startswith('/api/') and not self.authenticated():
                self.reply(401, {'error': 'Please log in to access your financial data.'})
                return
            if path=='/api/backups':
                self.reply(200,self.server.store.backups())
                return
            if path=='/api/moneyhub/status':
                summary = self.server.store.moneyhub_summary()
                with self.server.moneyhub_lock:
                    summary['auth_status'] = self.server.moneyhub.status if self.server.moneyhub else 'signed_out'
                self.reply(200, summary)
                return
            if path=='/api/moneyhub/transactions':
                query = parse_qs(urlsplit(self.path).query)
                limit = int(query.get('limit', ['20'])[0])
                self.reply(200, self.server.store.moneyhub_recent_transactions(limit))
                return
            if path=='/api/backup':
                bid=int(parse_qs(urlsplit(self.path).query)['id'][0])
                self.reply(200,self.server.store.backup(bid),attachment='money-locations-backup.json')
                return
            if path not in ('/api/state','/api/export','/api/csv'):
                self.reply(404,{'error':'Not found.'})
                return
            revision,state = self.server.store.read()
            if path=='/api/state':
                self.reply(200, {'revision':revision,'state':state,'reports':all_reports(state)})
            elif path=='/api/export':
                self.reply(200,state,attachment='money-locations.json')
            else:
                output=io.StringIO()
                writer=csv.writer(output)
                writer.writerow(['Date','Status','Account','Type','Wrapper','Balance','Contribution','Withdrawal','Relief or bonus','Interest','Capital adjustment'])
                accounts={a['id']:a for a in state['accounts']}
                def safe(v):
                    return "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v
                for s in sorted(state['snapshots'],key=lambda s:s['date']):
                    for aid,b in s['balances'].items():
                        a=accounts[aid]
                        text_fields=[safe(v) for v in (s['date'],s['status'],a['name'],a['type'],a.get('wrapper','None'))]
                        values=[float(money(b[f])) if b.get(f) not in (None,'') else '' for f in ('amount','contribution','withdrawal','relief','interest','capital')]
                        writer.writerow(text_fields+values)
                self.reply(200,output.getvalue(),'text/csv; charset=utf-8',attachment='money-locations-balances.csv')
        except (ValueError, KeyError, TypeError):
            self.reply(400,{'error':'Invalid request.'})

    def do_POST(self):
        if not self.allowed() or self.headers.get('X-Money-Request')!='1':
            self.reply(403,{'error':'Access through the app is required.'})
            return
        try:
            path=urlsplit(self.path).path
            is_login = path == '/api/auth/login'
            if not is_login and not self.authenticated():
                self.reply(401, {'error': 'Please log in to access your financial data.'})
                return
            size=int(self.headers.get('Content-Length','0'))
            limit = MAX_LOGIN_BODY if is_login else MAX_BODY
            if not 0<size<=limit:
                raise ValueError('Login request is too large.' if is_login else 'Upload must be between 1 byte and 20 MB.')
            data=json.loads(self.rfile.read(size),parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid number.')))
            if is_login:
                if not self.server.password_hash:
                    self.reply(403, {'error': 'Set web_password in the add-on configuration, then restart.'})
                    return
                key = self.client_address[0]
                now = time.time()
                with self.server.auth_lock:
                    count, until = self.server.failures.get(key, (0, 0))
                if until > now:
                    self.reply(429, {'error': 'Too many login attempts. Try again in one minute.'})
                    return
                supplied = str(data.get('password', ''))
                digest = hashlib.pbkdf2_hmac('sha256', supplied.encode(), self.server.salt, 200000)
                ok = hmac.compare_digest(digest, self.server.password_hash)
                token = None
                with self.server.auth_lock:
                    if not ok:
                        count = count + 1 if until > now - 60 else 1
                        self.server.failures[key] = (count, now + 60 if count >= 5 else now)
                    else:
                        self.server.failures.pop(key, None)
                        self.server.sessions = {k: v for k, v in self.server.sessions.items() if v > now}
                        token = secrets.token_urlsafe(32)
                        self.server.sessions[token] = now + 12 * 3600
                if not ok:
                    self.reply(401, {'error': 'Incorrect password.'})
                    return
                self.reply(200, {'ok': True}, cookie=self.session_cookie(token, 12 * 3600))
                return
            if path == '/api/auth/logout':
                cookie = SimpleCookie(self.headers.get('Cookie', ''))
                with self.server.auth_lock:
                    if 'money_session' in cookie:
                        self.server.sessions.pop(cookie['money_session'].value, None)
                self.reply(200, {'ok': True}, cookie=self.session_cookie('', 0))
                return
            if path == '/api/moneyhub/disconnect':
                with self.server.moneyhub_lock:
                    self.server.moneyhub = None
                self.reply(200, {'status': 'signed_out'})
                return
            if path == '/api/moneyhub/login':
                email = str(data.get('email', '')).strip()
                tenant_id = str(data.get('tenant_id', '')).strip()
                password = str(data.get('password', ''))
                settings = self.server.store.moneyhub_settings()
                device_id = settings.get('device_id') or str(uuid.uuid4())
                client = MoneyhubClient(email, tenant_id, device_id=device_id)
                result = client.start_login(password)
                self.server.store.set_moneyhub_settings(email, tenant_id, client.device_id)
                with self.server.moneyhub_lock:
                    self.server.moneyhub = client
                self.reply(200, result)
                return
            if path == '/api/moneyhub/verify':
                with self.server.moneyhub_lock:
                    client = self.server.moneyhub
                if not client:
                    raise ValueError('Start LifeStage login first.')
                result = client.verify_totp(data.get('totp'))
                self.reply(200, result)
                return
            if path == '/api/moneyhub/pull':
                start_date = str(data.get('start_date', ''))
                end_date = str(data.get('end_date', ''))
                start = date.fromisoformat(start_date)
                end = date.fromisoformat(end_date)
                if end < start:
                    raise ValueError('LifeStage pull end date must be on or after the start date.')
                if (end - start).days > 3700:
                    raise ValueError('LifeStage pull range is too large.')
                with self.server.moneyhub_lock:
                    client = self.server.moneyhub
                if not client or not client.authenticated:
                    raise ValueError('Authenticate with LifeStage before pulling data.')
                payload = client.pull(start_date, end_date)
                result = self.server.store.save_moneyhub_pull(
                    start_date,
                    end_date,
                    payload['active_accounts'],
                    payload['accounts'],
                    payload['transactions'],
                )
                result['summary'] = self.server.store.moneyhub_summary()
                self.reply(200, result)
                return
            if path=='/api/preview':
                _,state=self.server.store.read()
                s=data['snapshot']
                state['snapshots']=[v for v in state['snapshots'] if v['id']!=s['id']]+[s]
                validate_state(state)
                self.reply(200,report(state,s))
                return
            if path!='/api/action':
                self.reply(404,{'error':'Not found.'})
                return
            action=data['action']
            reason='draft' if action=='snapshot' and data['snapshot']['status']=='draft' else action
            revision,result=self.server.store.mutate(data['revision'],reason,lambda s:apply_action(s,action,data))
            self.reply(200,{'revision':revision,'result':result})
        except Conflict as exc:
            self.reply(409,{'error':str(exc)})
        except (ValueError,KeyError,TypeError,AttributeError) as exc:
            self.reply(400,{'error':str(exc) if isinstance(exc,ValueError) else 'Invalid data format.'})
        except Exception:
            self.reply(500,{'error':'The operation could not be saved. Your previous data is unchanged.'})


def create_server(path, host='0.0.0.0', port=8099, local=False, password='', direct=False):
    server=ThreadingHTTPServer((host,port),Handler)
    server.store=Store(path)
    server.local=local
    server.direct=direct
    server.salt=secrets.token_bytes(32)
    server.password_hash=hashlib.pbkdf2_hmac('sha256', password.encode(), server.salt, 200000) if password else None
    server.sessions={}
    server.failures={}
    server.auth_lock=threading.Lock()
    server.moneyhub=None
    server.moneyhub_lock=threading.Lock()
    return server


def persistent_path(data_dir, shared_dir=None):
    """Migrate once using SQLite's backup API; never overwrite shared data.

    Failure is fatal rather than silently opening a fresh, empty database.
    The legacy database remains untouched for recovery.
    """
    legacy = Path(data_dir) / 'money.sqlite'
    if shared_dir is None:
        return legacy
    folder = Path(shared_dir)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / 'money.sqlite'
    if not target.exists() and legacy.exists():
        temp = folder / 'migration.sqlite'
        try:
            with closing(sqlite3.connect(f'file:{legacy}?mode=ro', uri=True)) as source, closing(sqlite3.connect(temp)) as dest:
                source.backup(dest)
                dest.commit()
                if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Existing database failed its integrity check.')
                row = dest.execute('SELECT body FROM state WHERE id=1').fetchone()
                validate_state(json.loads(row[0]))
            os.chmod(temp, 0o600)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
    return target


if __name__=='__main__':
    os.umask(0o077)
    local=os.environ.get('MONEY_LOCAL')=='1'
    data_dir=Path(os.environ.get('MONEY_DATA','/data'))
    options_path=data_dir/'options.json'
    options=json.loads(options_path.read_text()) if options_path.exists() else {}
    password=options.get('web_password', '')
    shared=None if local else os.environ.get('MONEY_SHARED','/share/money_locations')
    path=persistent_path(data_dir, shared)
    server=create_server(path,host='127.0.0.1' if local else '0.0.0.0',port=int(os.environ.get('PORT','8099')),local=local,password=password)
    if not local and password:
        direct=create_server(path,port=8100,password=password,direct=True)
        threading.Thread(target=direct.serve_forever, daemon=True).start()
    print('Money Locations ready; database: '+str(path), flush=True)
    server.serve_forever()
