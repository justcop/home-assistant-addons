"""Private authentication state, separate from collection exports and backups."""
import base64
import io
import qrcode
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import struct
import threading
import time
from urllib.parse import quote

from werkzeug.security import check_password_hash, generate_password_hash

from .errors import AppError


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def totp(secret, counter):
    key = base64.b32decode(secret + '=' * (-len(secret) % 8))
    value = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = value[-1] & 15
    return f'{(struct.unpack(">I", value[offset:offset+4])[0] & 0x7fffffff) % 1000000:06d}'


class Security:
    def __init__(self, directory, password, enabled=False):
        self.path = directory / 'security.db'
        self.password_hash = generate_password_hash(password) if password else None
        self.password_version = hmac.new(json.loads((directory / 'session.json').read_text())['key'].encode(), password.encode(), hashlib.sha256).hexdigest()
        self.support_enabled = enabled is True
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, role TEXT, grant_id TEXT, expires REAL, version TEXT);
                CREATE TABLE IF NOT EXISTS grants (id TEXT PRIMARY KEY, password TEXT, role TEXT, expires REAL, revoked INTEGER DEFAULT 0, created REAL);
                CREATE TABLE IF NOT EXISTS trusted (token TEXT PRIMARY KEY, expires REAL, version TEXT);
                CREATE TABLE IF NOT EXISTS attempts (bucket TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS events (created REAL, event TEXT, details TEXT);
            ''')
            previous = db.execute("SELECT value FROM settings WHERE key='password_version'").fetchone()
            if not self.support_enabled or (previous and json.loads(previous[0]) != self.password_version):
                db.execute('UPDATE grants SET revoked=1')
            db.execute("INSERT OR REPLACE INTO settings VALUES ('password_version',?)", (json.dumps(self.password_version),))
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def connect(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=30)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def event(self, event, details=''):
        with self.connect() as db:
            db.execute('INSERT INTO events VALUES (?,?,?)', (time.time(), event, details))
            db.execute('DELETE FROM events WHERE rowid NOT IN (SELECT rowid FROM events ORDER BY created DESC LIMIT 100)')

    def throttle(self, address):
        now = time.time()
        with self.connect() as db:
            db.execute('DELETE FROM attempts WHERE created<?', (now-300,))
            count = db.execute('SELECT count(*) FROM attempts WHERE bucket=?', (digest(address or 'unknown'),)).fetchone()[0]
            total = db.execute('SELECT count(*) FROM attempts').fetchone()[0]
            if count >= 10 or total >= 100:
                raise AppError('Too many authentication attempts. Try again in five minutes.', 429)
            db.execute('INSERT INTO attempts VALUES (?,?)', (digest(address or 'unknown'), now))

    def clear_attempts(self, address):
        with self.connect() as db:
            db.execute('DELETE FROM attempts WHERE bucket=?', (digest(address or 'unknown'),))

    def verify_factor(self, code):
        if not self.get('totp'):
            return True
        if not isinstance(code, str) or len(code) > 100:
            return False
        with self.connect() as db:
            secret = self.get('totp')
            if not secret:
                return True
            last = self.get('last_counter', -1)
            current = int(time.time() // 30)
            for counter in (current, current-1, current+1):
                if counter > last and hmac.compare_digest(totp(secret, counter), code.strip()):
                    self.set('last_counter', counter)
                    return True
            recovery = self.get('recovery', [])
            hashed = digest(code.strip().upper())
            if hashed in recovery:
                recovery.remove(hashed)
                self.set('recovery', recovery)
                self.event('Recovery code used')
                return True
        return False

    def owner_credentials(self, password, code, trusted=None):
        if not isinstance(password, str) or len(password) > 1024 or not self.password_hash or not check_password_hash(self.password_hash, password):
            return False
        if trusted and self.trusted_valid(trusted):
            return True
        return self.verify_factor(code)

    def version(self):
        return self.password_version + ':' + str(self.get('epoch', 0))

    def trusted_valid(self, token):
        with self.connect() as db:
            row = db.execute('SELECT * FROM trusted WHERE token=?', (digest(token),)).fetchone()
        return bool(row and row['expires'] > time.time() and row['version'] == self.version())

    def forget_trust(self, token):
        if token:
            with self.connect() as db:
                db.execute('DELETE FROM trusted WHERE token=?', (digest(token),))

    def trust(self):
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('INSERT INTO trusted VALUES (?,?,?)', (digest(token), time.time()+30*86400, self.version()))
        return token

    def create_session(self, role='owner', grant_id=None, expires=None):
        token = secrets.token_urlsafe(32)
        expiry = min(expires or float('inf'), time.time()+12*3600)
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            db.execute('DELETE FROM trusted WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)', (digest(token), role, grant_id, expiry, self.version()))
        return token

    def identity(self, token):
        if not token:
            return None
        with self.connect() as db:
            row = db.execute('SELECT * FROM sessions WHERE token=?', (digest(token),)).fetchone()
            if not row or row['expires'] <= time.time() or row['version'] != self.version():
                return None
            if row['role'] != 'owner':
                grant = db.execute('SELECT * FROM grants WHERE id=?', (row['grant_id'],)).fetchone()
                if not self.support_enabled or not grant or grant['revoked'] or grant['expires'] <= time.time():
                    return None
        return dict(row)

    def logout(self, token):
        if token:
            with self.connect() as db:
                db.execute('DELETE FROM sessions WHERE token=?', (digest(token),))

    def login(self, password, code, address, trusted=None):
        self.throttle(address)
        if not self.password_hash:
            raise AppError('Set a standalone web password in Home Assistant configuration. Home Assistant ingress remains available.', 403)
        if self.owner_credentials(password, code, trusted):
            self.clear_attempts(address)
            self.event('Owner login')
            return self.create_session(), 'owner'
        if isinstance(password, str) and len(password) < 200 and '.' in password and self.support_enabled:
            identifier, secret = password.split('.', 1)
            with self.connect() as db:
                grant = db.execute('SELECT * FROM grants WHERE id=?', (identifier,)).fetchone()
                if grant and not grant['revoked'] and grant['expires'] > time.time() and check_password_hash(grant['password'], secret):
                    self.clear_attempts(address)
                    self.event('Support login', identifier)
                    return self.create_session(grant['role'], identifier, grant['expires']), grant['role']
        raise AppError('Incorrect password or verification code.', 401)

    def start_totp(self):
        if self.get('totp'):
            raise AppError('Two-factor authentication is already enabled.')
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
        self.set('pending_totp', {'secret': secret, 'expires': time.time()+600})
        uri = 'otpauth://totp/'+quote('AudioShelf:Owner')+'?secret='+secret+'&issuer=AudioShelf&digits=6&period=30'
        buffer = io.BytesIO()
        qrcode.make(uri).save(buffer, format='PNG')
        return {'secret': secret, 'uri': uri, 'qr': 'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()}

    def confirm_totp(self, code):
        with self.connect():
            pending = self.get('pending_totp', {})
            if not pending or pending['expires'] <= time.time() or not isinstance(code, str):
                raise AppError('Setup expired. Start again.')
            current = int(time.time() // 30)
            accepted = next((c for c in (current, current-1, current+1) if hmac.compare_digest(totp(pending['secret'], c), code.strip())), None)
            if accepted is None:
                raise AppError('Enter a fresh six-digit code from your authenticator app.')
            codes = [secrets.token_hex(8).upper() for _ in range(10)]
            self.set('totp', pending['secret']); self.set('last_counter', accepted)
            self.set('recovery', [digest(c) for c in codes]); self.set('pending_totp', None)
            self.invalidate()
            self.event('Two-factor authentication enabled')
            return codes

    def invalidate(self):
        with self.connect() as db:
            self.set('epoch', self.get('epoch', 0)+1)
            db.execute('DELETE FROM sessions'); db.execute('DELETE FROM trusted')

    def disable_totp(self):
        self.set('totp', None); self.set('recovery', []); self.set('pending_totp', None)
        self.invalidate(); self.event('Two-factor authentication disabled')

    def create_grant(self, role, hours):
        if not self.support_enabled:
            raise AppError('Enable temporary support access in Home Assistant configuration first.', 403)
        if role not in {'view', 'control'} or type(hours) not in (int, float) or not 0 < hours <= 8:
            raise AppError('Choose view-only or changes/playback access for up to eight hours.')
        identifier, password = secrets.token_hex(8), secrets.token_urlsafe(24)
        expiry = time.time()+hours*3600
        with self.connect() as db:
            db.execute('INSERT INTO grants(id,password,role,expires,created) VALUES (?,?,?,?,?)',
                       (identifier, generate_password_hash(password), role, expiry, time.time()))
        self.event('Support login created', identifier+':'+role)
        return {'id': identifier, 'password': identifier+'.'+password, 'expires': expiry, 'role': role}

    def revoke_grant(self, identifier):
        with self.connect() as db:
            db.execute('UPDATE grants SET revoked=1 WHERE id=?', (identifier,))
            db.execute('DELETE FROM sessions WHERE grant_id=?', (identifier,))
        self.event('Support login revoked', identifier)

    def overview(self):
        with self.connect() as db:
            grants = [dict(r) for r in db.execute('SELECT id,role,expires,revoked,created FROM grants ORDER BY created DESC LIMIT 50')]
            events = [dict(r) for r in db.execute('SELECT created,event,details FROM events ORDER BY created DESC LIMIT 30')]
            trusted = db.execute('SELECT count(*) FROM trusted WHERE expires>? AND version=?', (time.time(), self.version())).fetchone()[0]
        return {'two_factor': bool(self.get('totp')), 'support_enabled': self.support_enabled,
                'grants': grants, 'events': events, 'trusted_devices': trusted, 'password_configured': bool(self.password_hash)}
