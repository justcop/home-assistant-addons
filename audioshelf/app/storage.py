import contextlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path


class Store:
    """Short-lived connections, transactional updates and explicit schema versions."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'audioshelf.db'
        self.catalogue_lock = threading.RLock()
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise RuntimeError('This database needs a newer AudioShelf version.')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS artists (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, sort_name TEXT NOT NULL,
                    disambiguation TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS albums (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, release_date TEXT NOT NULL,
                    release_id TEXT, release_label TEXT, canonical_reviewed INTEGER DEFAULT 0,
                    spotify_album_id TEXT, spotify_album_name TEXT);
                CREATE TABLE IF NOT EXISTS album_artists (
                    album_id TEXT REFERENCES albums(id), artist_id TEXT REFERENCES artists(id),
                    credit_order INTEGER, PRIMARY KEY(album_id, artist_id));
                CREATE TABLE IF NOT EXISTS tracks (
                    album_id TEXT REFERENCES albums(id), position INTEGER,
                    title TEXT NOT NULL, disc_number INTEGER, track_number INTEGER,
                    duration_ms INTEGER, recording_id TEXT, isrcs TEXT NOT NULL DEFAULT '[]',
                    spotify_id TEXT, spotify_album_id TEXT, score REAL,
                    method TEXT, verified INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(album_id, position));
                CREATE TABLE IF NOT EXISTS shelf (
                    album_id TEXT PRIMARY KEY REFERENCES albums(id), added_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state TEXT PRIMARY KEY, verifier TEXT NOT NULL, expires REAL NOT NULL);
                PRAGMA user_version=1;
            ''')
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=30000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def cache_get(self, key):
        with self.connect() as db:
            row = db.execute('SELECT value FROM cache WHERE key=? AND expires>?', (key, time.time())).fetchone()
        return json.loads(row[0]) if row else None

    def cache_put(self, key, value, ttl=86400):
        with self.connect() as db:
            db.execute('DELETE FROM cache WHERE expires<?', (time.time(),))
            db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)', (key, json.dumps(value), time.time()+ttl))

    def catalogue(self, groups):
        with self.connect() as db:
            for group in groups:
                db.execute('INSERT INTO albums(id,title,release_date) VALUES (?,?,?) '
                           'ON CONFLICT(id) DO UPDATE SET title=excluded.title,release_date=excluded.release_date',
                           (group['id'], group['title'], group.get('first-release-date', '')))
                for order, credit in enumerate(group.get('artist-credit', [])):
                    if not isinstance(credit, dict) or 'artist' not in credit:
                        continue
                    artist = credit['artist']
                    db.execute('INSERT INTO artists VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                               'name=excluded.name,sort_name=excluded.sort_name,disambiguation=excluded.disambiguation',
                               (artist['id'], artist['name'], artist.get('sort-name', artist['name']), artist.get('disambiguation','')))
                    db.execute('INSERT OR REPLACE INTO album_artists VALUES (?,?,?)', (group['id'], artist['id'], order))

    def artists(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('''SELECT a.*,count(DISTINCT s.album_id) AS album_count
                FROM artists a JOIN album_artists aa ON aa.artist_id=a.id JOIN shelf s ON s.album_id=aa.album_id
                GROUP BY a.id ORDER BY a.sort_name COLLATE NOCASE''')]

    def albums(self, artist_id=None, owned=False):
        query = 'SELECT a.*,s.added_at IS NOT NULL AS on_shelf FROM albums a LEFT JOIN shelf s ON s.album_id=a.id'
        clauses, args = [], []
        if artist_id:
            clauses.append('EXISTS(SELECT 1 FROM album_artists aa WHERE aa.album_id=a.id AND aa.artist_id=?)')
            args.append(artist_id)
        if owned:
            clauses.append('s.added_at IS NOT NULL')
        if clauses:
            query += ' WHERE ' + ' AND '.join(clauses)
        query += " ORDER BY CASE WHEN a.release_date='' THEN 1 ELSE 0 END,a.release_date,a.title"
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(query, args)]
            for row in rows:
                row['artists'] = self._credits(db, row['id'])
        return rows

    @staticmethod
    def _credits(db, album_id):
        return [dict(row) for row in db.execute('SELECT a.* FROM artists a JOIN album_artists aa '
            'ON aa.artist_id=a.id WHERE aa.album_id=? ORDER BY aa.credit_order', (album_id,))]

    def album(self, album_id):
        with self.connect() as db:
            row = db.execute('SELECT a.*,s.added_at IS NOT NULL AS on_shelf FROM albums a '
                             'LEFT JOIN shelf s ON s.album_id=a.id WHERE a.id=?', (album_id,)).fetchone()
            if row is None:
                raise KeyError(album_id)
            result = dict(row)
            result['artists'] = self._credits(db, album_id)
            result['tracks'] = [dict(row) for row in db.execute('SELECT * FROM tracks WHERE album_id=? ORDER BY position', (album_id,))]
            for track in result['tracks']:
                track['isrcs'] = json.loads(track['isrcs'])
        result['playable'] = bool(result['tracks']) and all(t['spotify_id'] and t['verified'] for t in result['tracks'])
        return result

    def set_tracks(self, album_id, release, tracks, reviewed=False):
        with self.connect() as db:
            db.execute('DELETE FROM tracks WHERE album_id=?', (album_id,))
            for position, track in enumerate(tracks, 1):
                db.execute('INSERT INTO tracks(album_id,position,title,disc_number,track_number,duration_ms,recording_id,isrcs) '
                           'VALUES (?,?,?,?,?,?,?,?)', (album_id, position, track['title'], track['disc_number'],
                           track['track_number'], track.get('duration_ms'), track.get('recording_id'), json.dumps(track.get('isrcs', []))))
            db.execute('UPDATE albums SET release_id=?,release_label=?,canonical_reviewed=?,spotify_album_id=NULL,spotify_album_name=NULL WHERE id=?',
                       (release['id'], release.get('label', release.get('title','')), int(reviewed), album_id))

    def mapping(self, album_id, candidate, manual=False):
        """Replace an edition atomically; automatic resolution preserves hand corrections."""
        with self.connect() as db:
            for mapping in candidate['mappings']:
                db.execute("UPDATE tracks SET spotify_id=?,spotify_album_id=?,score=?,method=?,verified=? "
                           "WHERE album_id=? AND position=?" + ('' if manual else " AND NOT (coalesce(method,'')='manual' AND verified=1 AND spotify_id IS NOT NULL)"),
                           (mapping.get('spotify_id'), candidate['id'], mapping['score'],
                            'manual' if manual else 'automatic', int(mapping['verified']), album_id, mapping['position']))
            db.execute('UPDATE albums SET spotify_album_id=?,spotify_album_name=? WHERE id=?',
                       (candidate['id'], candidate['name'], album_id))

    def shelf(self, album_id, add):
        with self.connect() as db:
            if add:
                db.execute('INSERT OR IGNORE INTO shelf VALUES (?,?)', (album_id, time.time()))
            else:
                db.execute('DELETE FROM shelf WHERE album_id=?', (album_id,))

    def backup(self):
        folder = self.directory / 'backups'
        folder.mkdir(exist_ok=True)
        path = folder / time.strftime('audioshelf-%Y%m%d-%H%M%S.db')
        with self.connect() as source:
            with sqlite3.connect(path) as destination:
                source.backup(destination)
                destination.execute('DELETE FROM oauth_states')
        os.chmod(path, 0o600)
        for old in sorted(folder.glob('audioshelf-*.db'))[:-7]:
            old.unlink()
        return path
