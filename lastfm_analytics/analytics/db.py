import contextlib
import hashlib
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

from .grouping import auto_key, canonical_title, normalise

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS groups (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, artist TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS aliases (
 kind TEXT NOT NULL, auto_key TEXT NOT NULL, group_id INTEGER NOT NULL REFERENCES groups(id),
 PRIMARY KEY(kind, auto_key));
CREATE TABLE IF NOT EXISTS variants (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, artist TEXT NOT NULL, name TEXT NOT NULL,
 auto_key TEXT NOT NULL, override_group INTEGER REFERENCES groups(id),
 UNIQUE(kind, artist, name));
CREATE INDEX IF NOT EXISTS variant_alias ON variants(kind, auto_key);
CREATE TABLE IF NOT EXISTS scrobbles (
 id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL, occurrence INTEGER NOT NULL,
 ts INTEGER NOT NULL, artist TEXT NOT NULL, artist_key TEXT NOT NULL,
 title TEXT NOT NULL, album TEXT NOT NULL, raw_json TEXT NOT NULL,
 song_id INTEGER NOT NULL REFERENCES variants(id), album_id INTEGER REFERENCES variants(id),
 active INTEGER NOT NULL DEFAULT 1, UNIQUE(fingerprint, occurrence));
CREATE INDEX IF NOT EXISTS play_time ON scrobbles(active, ts);
CREATE INDEX IF NOT EXISTS play_artist ON scrobbles(artist_key, active, ts);
CREATE INDEX IF NOT EXISTS play_song ON scrobbles(song_id, active, ts);
CREATE INDEX IF NOT EXISTS play_album ON scrobbles(album_id, active, ts);
CREATE TABLE IF NOT EXISTS grouping_events (
 id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, description TEXT NOT NULL,
 before_json TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0);
CREATE VIEW IF NOT EXISTS resolved_variants AS
 SELECT v.*, COALESCE(v.override_group,a.group_id) AS group_id
 FROM variants v JOIN aliases a ON a.kind=v.kind AND a.auto_key=v.auto_key;
"""


def identity(row):
    return json.dumps(
        [row["ts"], row["artist"], row["title"], row["album"]],
        ensure_ascii=False,
        separators=(",", ":"),
    )


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute("PRAGMA user_version=1")

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def get(db, key, default=None):
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def put(db, key, value):
        db.execute(
            "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def meta(self, key, default=None):
        with self.connect() as db:
            return self.get(db, key, default)

    def set_meta(self, key, value):
        with self.connect() as db:
            self.put(db, key, value)

    def variant(self, db, kind, artist, name):
        existing = db.execute(
            "SELECT id FROM variants WHERE kind=? AND artist=? AND name=?",
            (kind, artist, name),
        ).fetchone()
        if existing:
            return existing[0]
        key = auto_key(artist, name, kind)
        alias = db.execute(
            "SELECT group_id FROM aliases WHERE kind=? AND auto_key=?", (kind, key)
        ).fetchone()
        if not alias:
            group = db.execute(
                "INSERT INTO groups(kind,artist,name) VALUES (?,?,?)",
                (kind, artist, canonical_title(name, kind)),
            ).lastrowid
            db.execute("INSERT INTO aliases VALUES (?,?,?)", (kind, key, group))
        return db.execute(
            "INSERT INTO variants(kind,artist,name,auto_key) VALUES (?,?,?,?)",
            (kind, artist, name, key),
        ).lastrowid

    def apply_window(self, start, end, rows, reconcile=False, checkpoint=None):
        """One transaction: a complete window, its multiplicities, and its cursor.

        Remotely removed entries are hidden, never destroyed. Reimports reactivate
        them. A repeated fingerprint at the same second retains its occurrence.
        """
        counts = Counter()
        with self.connect() as db:
            if reconcile:
                db.execute(
                    "UPDATE scrobbles SET active=0 WHERE ts>=? AND ts<?", (start, end)
                )
            for row in rows:
                if not start <= row["ts"] < end:
                    raise ValueError("Scrobble outside committed window")
                fingerprint = hashlib.sha256(identity(row).encode()).hexdigest()
                counts[fingerprint] += 1
                song = self.variant(db, "song", row["artist"], row["title"])
                album = (
                    self.variant(db, "album", row["artist"], row["album"])
                    if row["album"]
                    else None
                )
                db.execute(
                    """INSERT INTO scrobbles(fingerprint,occurrence,ts,artist,artist_key,title,album,raw_json,song_id,album_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fingerprint,occurrence)
                    DO UPDATE SET active=1""",
                    (
                        fingerprint,
                        counts[fingerprint],
                        row["ts"],
                        row["artist"],
                        normalise(row["artist"]),
                        row["title"],
                        row["album"],
                        json.dumps(row.get("raw", {}), ensure_ascii=False),
                        song,
                        album,
                    ),
                )
            for key, value in (checkpoint or {}).items():
                self.put(db, key, value)

    def change_groups(self, action, ids):
        try:
            if any(isinstance(i, bool) or not isinstance(i, (int, str)) for i in ids):
                raise ValueError
            ids = list(dict.fromkeys(int(i) for i in ids))
        except (TypeError, ValueError):
            raise ValueError("Use valid group or version identifiers") from None
        if not ids or len(ids) > 100:
            raise ValueError("Select between 1 and 100 entries")
        marks = ",".join("?" for _ in ids)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            snapshot = {"aliases": [], "variants": []}
            if action == "merge":
                if len(ids) < 2:
                    raise ValueError("Select at least two groups")
                groups = db.execute(
                    f"SELECT * FROM groups WHERE id IN ({marks})", ids
                ).fetchall()
                if len(groups) != len(ids) or len({g["kind"] for g in groups}) != 1:
                    raise ValueError("Merge groups of the same type")
                if len({normalise(g["artist"]) for g in groups}) != 1:
                    raise ValueError("Merge versions by the same artist")
                variants = db.execute(
                    f"SELECT id,override_group FROM resolved_variants WHERE group_id IN ({marks})",
                    ids,
                ).fetchall()
                aliases = db.execute(
                    f"SELECT * FROM aliases WHERE group_id IN ({marks})", ids
                ).fetchall()
                snapshot["variants"] = [dict(v) for v in variants]
                snapshot["aliases"] = [dict(a) for a in aliases]
                for v in variants:
                    db.execute(
                        "UPDATE variants SET override_group=? WHERE id=?",
                        (ids[0], v["id"]),
                    )
                db.execute(
                    f"UPDATE aliases SET group_id=? WHERE group_id IN ({marks})",
                    [ids[0], *ids],
                )
                description = f"Merged {len(ids)} {groups[0]['kind']} groups"
            elif action == "separate":
                variants = db.execute(
                    f"SELECT * FROM variants WHERE id IN ({marks})", ids
                ).fetchall()
                if len(variants) != len(ids):
                    raise ValueError("Unknown version")
                snapshot["variants"] = [
                    {"id": v["id"], "override_group": v["override_group"]}
                    for v in variants
                ]
                for v in variants:
                    gid = db.execute(
                        "INSERT INTO groups(kind,artist,name) VALUES (?,?,?)",
                        (v["kind"], v["artist"], v["name"]),
                    ).lastrowid
                    db.execute(
                        "UPDATE variants SET override_group=? WHERE id=?",
                        (gid, v["id"]),
                    )
                description = f"Separated {len(ids)} versions"
            else:
                raise ValueError("Unknown grouping action")
            db.execute(
                "INSERT INTO grouping_events(ts,description,before_json) VALUES (?,?,?)",
                (int(time.time()), description, json.dumps(snapshot)),
            )

    def undo_grouping(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            event = db.execute(
                "SELECT * FROM grouping_events WHERE undone=0 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not event:
                raise ValueError("No grouping change to undo")
            before = json.loads(event["before_json"])
            for a in before["aliases"]:
                db.execute(
                    "UPDATE aliases SET group_id=? WHERE kind=? AND auto_key=?",
                    (a["group_id"], a["kind"], a["auto_key"]),
                )
            for v in before["variants"]:
                db.execute(
                    "UPDATE variants SET override_group=? WHERE id=?",
                    (v["override_group"], v["id"]),
                )
            db.execute("UPDATE grouping_events SET undone=1 WHERE id=?", (event["id"],))
