"""Dependency-free, ingress-only web service with transactional local persistence."""
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import csv
import io
import json
import mimetypes
import os
from pathlib import Path
import sqlite3
import uuid
from urllib.parse import urlsplit, parse_qs

from model import blank_state, validate_state, new_snapshot, problems, report, all_reports, money

ROOT = Path(__file__).parent
MAX_BODY = 20 * 1024 * 1024


class Conflict(Exception):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, body TEXT NOT NULL)')
            con.execute('CREATE TABLE IF NOT EXISTS backups (id INTEGER PRIMARY KEY, created TEXT, reason TEXT, revision INTEGER, body TEXT)')
            con.execute('INSERT OR IGNORE INTO state VALUES (1, 0, ?)', (json.dumps(blank_state()),))

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def read(self):
        with self.connect() as con:
            revision, body = con.execute('SELECT revision, body FROM state WHERE id=1').fetchone()
        return revision, json.loads(body)

    def mutate(self, revision, reason, change):
        with self.connect() as con:
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
        with self.connect() as con:
            return [dict(zip(('id','created','reason','revision'), r)) for r in con.execute('SELECT id,created,reason,revision FROM backups ORDER BY id DESC')]

    def backup(self, backup_id):
        with self.connect() as con:
            row = con.execute('SELECT body FROM backups WHERE id=?', (backup_id,)).fetchone()
        if not row:
            raise ValueError('Backup not found.')
        return json.loads(row[0])


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
        if any(v['id']!=s['id'] and v['date']==s['date'] for v in state['snapshots']):
            raise ValueError('A snapshot already exists on this date.')
        state['snapshots'] = [s if v['id']==s['id'] else v for v in state['snapshots']]
        validate_state(state)
        if s['status']=='final':
            issues = problems(state,s)
            if issues:
                raise ValueError(' '.join(issues))
        return s['id']
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

    def allowed(self):
        expected = '127.0.0.1' if self.server.local else '172.30.32.2'
        return self.client_address[0] == expected

    def reply(self, status, payload, content_type='application/json', attachment=None):
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
        if attachment:
            self.send_header('Content-Disposition', 'attachment; filename="'+attachment+'"')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if not self.allowed():
            self.reply(403, {'error':'Access through Home Assistant is required.'})
            return
        try:
            path = urlsplit(self.path).path
            revision,state = self.server.store.read()
            if path=='/api/state':
                self.reply(200, {'revision':revision,'state':state,'reports':all_reports(state)})
            elif path=='/api/export':
                self.reply(200,state,attachment='money-locations.json')
            elif path=='/api/backups':
                self.reply(200,self.server.store.backups())
            elif path=='/api/backup':
                bid=int(parse_qs(urlsplit(self.path).query)['id'][0])
                self.reply(200,self.server.store.backup(bid),attachment='money-locations-backup.json')
            elif path=='/api/csv':
                output=io.StringIO()
                writer=csv.writer(output)
                writer.writerow(['Date','Status','Account','Type','Wrapper','Balance','Contribution','Withdrawal','Relief or bonus','Interest','Capital adjustment'])
                accounts={a['id']:a for a in state['accounts']}
                def safe(v):
                    return "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v
                for s in sorted(state['snapshots'],key=lambda s:s['date']):
                    for aid,b in s['balances'].items():
                        a=accounts[aid]
                        writer.writerow([s['date'],s['status'],safe(a['name']),a['type'],a['wrapper']]+[float(money(b[f])) if b.get(f) not in (None,'') else '' for f in ('amount','contribution','withdrawal','relief','interest','capital')])
                self.reply(200,output.getvalue(),'text/csv; charset=utf-8',attachment='money-locations-balances.csv')
            elif path=='/health':
                self.reply(200,{'status':'ok'})
            elif path in ('/','/index.html','/app.js','/style.css'):
                name='index.html' if path=='/' else path[1:]
                self.reply(200,(ROOT/'static'/name).read_bytes(), mimetypes.guess_type(name)[0] or 'text/plain')
            else:
                self.reply(404,{'error':'Not found.'})
        except (ValueError, KeyError, TypeError):
            self.reply(400,{'error':'Invalid request.'})

    def do_POST(self):
        if not self.allowed() or self.headers.get('X-Money-Request')!='1':
            self.reply(403,{'error':'Access through the app is required.'})
            return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=MAX_BODY:
                raise ValueError('Upload must be between 1 byte and 20 MB.')
            data=json.loads(self.rfile.read(size),parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid number.')))
            path=urlsplit(self.path).path
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


def create_server(path, host='0.0.0.0', port=8099, local=False):
    server=ThreadingHTTPServer((host,port),Handler)
    server.store=Store(path)
    server.local=local
    return server


if __name__=='__main__':
    os.umask(0o077)
    local=os.environ.get('MONEY_LOCAL')=='1'
    path=Path(os.environ.get('MONEY_DATA','/data'))/'money.sqlite'
    server=create_server(path,host='127.0.0.1' if local else '0.0.0.0',port=int(os.environ.get('PORT','8099')),local=local)
    print('Money Locations ready', flush=True)
    server.serve_forever()
