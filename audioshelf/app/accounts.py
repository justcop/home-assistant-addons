"""Private account registry and independent collection/Spotify contexts."""
import contextlib
import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
from types import SimpleNamespace

from werkzeug.security import generate_password_hash

from .artwork import Artwork
from .errors import AppError
from .musicbrainz import MusicBrainz
from .playback import PlaybackHandoff
from .playable_releases import PlayableReleases
from .security import Security
from .spotify import Spotify, atomic_private_json
from .storage import Store


class Accounts:
    def __init__(self, collection, cache, private, options, owner):
        self.collection, self.cache, self.private = collection, cache, private
        self.options, self.owner = options, owner
        self.path = private / 'accounts.db'
        self.lock = threading.RLock()
        self.contexts = {'owner': (None, owner)}
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    password_hash TEXT, disabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS oauth (
                    state TEXT PRIMARY KEY, account_id TEXT NOT NULL, expires REAL NOT NULL);
            ''')
            db.execute("INSERT OR IGNORE INTO accounts(id,username) VALUES ('owner','owner')")
        os.chmod(self.path, 0o600)
        self.dummy_hash = generate_password_hash(secrets.token_urlsafe(32))

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

    def find(self, username):
        if not isinstance(username, str) or len(username) > 32:
            return None
        with self.connect() as db:
            row = db.execute('SELECT * FROM accounts WHERE username=?', (username.strip().lower(),)).fetchone()
        return dict(row) if row else None

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT * FROM accounts WHERE id=?', (identifier,)).fetchone()
        return dict(row) if row else None

    def enabled(self, identifier):
        # Workers may hold the playback lock. Avoid reversing the lock order
        # used by account updates (registry, then playback cancellation).
        with sqlite3.connect(self.path, timeout=30) as db:
            row = db.execute('SELECT disabled FROM accounts WHERE id=?', (identifier,)).fetchone()
        return bool(row and not row[0])

    def listing(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,username,disabled FROM accounts ORDER BY id!='owner',username")]

    @staticmethod
    def validate_password(password):
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise AppError('Use a password of 12 to 1024 characters.')

    def create(self, username, password):
        if not isinstance(username, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,31}', username):
            raise AppError('Use 1 to 32 letters, numbers, dots, underscores or hyphens for the username.')
        self.validate_password(password)
        identifier = secrets.token_hex(16)
        hashed = generate_password_hash(password)
        with self.connect() as db:
            if db.execute('SELECT count(*) FROM accounts').fetchone()[0] >= 50:
                raise AppError('AudioShelf supports up to 50 accounts.')
            try:
                db.execute('INSERT INTO accounts(id,username,password_hash) VALUES (?,?,?)', (identifier, username.lower(), hashed))
            except sqlite3.IntegrityError:
                raise AppError('That username is already in use.', 409) from None
        return self.get(identifier)

    def context(self, identifier):
        with self.lock:
            account = self.get(identifier)
            if not account or account['disabled']:
                raise AppError('Sign in to an active AudioShelf account.', 401)
            if identifier == 'owner':
                return self.owner
            cached = self.contexts.get(identifier)
            if cached and cached[0] == account['password_hash']:
                return cached[1]
            private = self.private / 'users' / identifier
            private.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not (private / 'session.json').exists():
                atomic_private_json(private / 'session.json', {'key': secrets.token_hex(32)})
            store = Store(self.collection / 'users' / identifier, self.cache / 'users' / identifier)
            spotify, musicbrainz = Spotify(store, self.options, private), MusicBrainz(store)
            security = Security(private, '', self.options.get('allow_support_access', False), password_hash=account['password_hash'])
            security.account_name = account['username']
            context = SimpleNamespace(store=store, spotify=spotify, musicbrainz=musicbrainz,
                                      artwork=Artwork(store, spotify, musicbrainz), security=security, handoff=PlaybackHandoff(spotify),
                                      playable_releases=PlayableReleases(store, musicbrainz, spotify))
            self.contexts[identifier] = (account['password_hash'], context)
            return context

    def update(self, identifier, password=None, disabled=None, reset_two_factor=False):
        with self.lock:
            account = self.get(identifier)
            if not account:
                raise AppError('That account could not be found.', 404)
            if identifier == 'owner':
                raise AppError('The owner account is managed through Home Assistant configuration.')
            if password is not None:
                self.validate_password(password)
            if disabled is not None and type(disabled) is not bool:
                raise AppError('Choose whether the account is enabled.')
            if type(reset_two_factor) is not bool:
                raise AppError('Choose whether to reset two-factor authentication.')
            # Revoke existing access before modifying credentials or account status.
            if not account['disabled']:
                context = self.context(identifier)
                context.handoff.cancel_all()
                if reset_two_factor:
                    context.security.disable_totp()
                else:
                    context.security.invalidate()
            elif reset_two_factor:
                # Disabled accounts keep their private data; permit explicit recovery.
                private = self.private / 'users' / identifier
                if (private / 'security.db').exists():
                    Security(private, '', password_hash=account['password_hash']).disable_totp()
            with self.connect() as db:
                if password is not None:
                    db.execute('UPDATE accounts SET password_hash=? WHERE id=?', (generate_password_hash(password), identifier))
                if disabled is not None:
                    db.execute('UPDATE accounts SET disabled=? WHERE id=?', (int(disabled), identifier))
                db.execute('DELETE FROM oauth WHERE account_id=?', (identifier,))
            self.contexts.pop(identifier, None)
            return self.get(identifier)

    def bind_oauth(self, url, identifier):
        from urllib.parse import parse_qs, urlsplit
        state = parse_qs(urlsplit(url).query)['state'][0]
        with self.connect() as db:
            db.execute('DELETE FROM oauth WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO oauth VALUES (?,?,?)', (hashlib.sha256(state.encode()).hexdigest(), identifier, time.time()+600))

    def oauth_account(self, state):
        with self.connect() as db:
            row = db.execute('DELETE FROM oauth WHERE state=? RETURNING *', (hashlib.sha256(state.encode()).hexdigest(),)).fetchone()
        if not row or row['expires'] <= time.time():
            raise AppError('Spotify connection expired or was already used. Start Connect Spotify again.')
        return row['account_id']
