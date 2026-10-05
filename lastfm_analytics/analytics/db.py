import contextlib
import hashlib
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

from .grouping import auto_key, canonical_title, normalise, review_title

SCHEMA = """
CREATE TABLE IF NOT EXISTS view_cache (
 cache_key TEXT PRIMARY KEY, revision INTEGER NOT NULL, generated REAL NOT NULL,
 accessed REAL NOT NULL, payload TEXT NOT NULL);
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
CREATE TABLE IF NOT EXISTS source_reports (
 username TEXT NOT NULL, ts INTEGER NOT NULL, artist_key TEXT NOT NULL,
 title_key TEXT NOT NULL, source TEXT NOT NULL, received_at INTEGER NOT NULL,
 PRIMARY KEY(username,ts,artist_key,title_key));
CREATE INDEX IF NOT EXISTS source_match ON source_reports(ts,artist_key,title_key,source);
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
            db.execute("PRAGMA user_version=2")

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.create_function("source_key", 1, normalise, deterministic=True)
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
            if key == "import":
                self.put(
                    db, "analysis_revision", self.get(db, "analysis_revision", 0) + 1
                )

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
        vid = db.execute(
            "INSERT INTO variants(kind,artist,name,auto_key) VALUES (?,?,?,?)",
            (kind, artist, name, key),
        ).lastrowid
        rules = self.get(db, "learned_rules", [])
        changed = False
        for rule in rules:
            if rule["kind"] != kind or rule["artist_key"] != normalise(artist):
                continue
            # A base title may arrive after its suffixed version in the import.
            for variant in db.execute(
                "SELECT id,name FROM variants WHERE kind=? AND artist=? AND override_group IS NULL",
                (kind, artist),
            ).fetchall():
                base, suffix, protected = review_title(variant["name"])
                if protected or normalise(suffix) != normalise(rule["suffix"]):
                    continue
                target = db.execute(
                    "SELECT group_id FROM aliases WHERE kind=? AND auto_key=?",
                    (kind, auto_key(artist, base, kind)),
                ).fetchone()
                if target:
                    db.execute(
                        "UPDATE variants SET override_group=? WHERE id=?",
                        (target[0], variant["id"]),
                    )
                    rule["applied"].append(variant["id"])
                    changed = True
        if changed:
            self.put(db, "learned_rules", rules)
        return vid

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
            self.put(db, "analysis_revision", self.get(db, "analysis_revision", 0) + 1)
            for key, value in (checkpoint or {}).items():
                self.put(db, key, value)

    def record_source(self, username, report):
        # Keep attribution independent of imported rows: notifications can precede
        # sync, and reconciliation/Last.fm album edits must not erase the source.
        if not isinstance(report, dict):
            raise ValueError("Invalid source report")
        if str(report.get("username", "")).strip().casefold() != username.casefold():
            raise ValueError("Source report belongs to another Last.fm account")
        if report.get("source") != "vinyl":
            raise ValueError("Unsupported listening source")
        ts = report.get("timestamp")
        if (
            isinstance(ts, bool)
            or not isinstance(ts, int)
            or not 0 < ts <= time.time() + 300
        ):
            raise ValueError("Invalid listening timestamp")
        keys = []
        for field in ("artist", "title"):
            value = report.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 1000:
                raise ValueError("Invalid track identity")
            keys.append(normalise(value))
        with self.connect() as db:
            db.execute(
                """INSERT INTO source_reports VALUES (?,?,?,?,?,?)
                ON CONFLICT(username,ts,artist_key,title_key) DO NOTHING""",
                (username.casefold(), ts, *keys, "vinyl", int(time.time())),
            )
            self.put(db, "analysis_revision", self.get(db, "analysis_revision", 0) + 1)

    def change_groups(self, action, ids, name=None):
        if name is not None:
            if not isinstance(name, str) or not name.strip() or len(name) > 1000:
                raise ValueError("Choose a combined name between 1 and 1000 characters")
            name = name.strip()
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
            snapshot = {"aliases": [], "variants": [], "groups": []}
            if action in ("merge", "merge_learn"):
                if action == "merge_learn":
                    source = db.execute(
                        f"SELECT * FROM resolved_variants WHERE group_id IN ({marks})",
                        ids,
                    ).fetchall()
                    parts = [review_title(v["name"]) for v in source]
                    suffixes = {normalise(suffix) for _, suffix, _ in parts if suffix}
                    if (
                        any(protected for _, _, protected in parts)
                        or len(suffixes) != 1
                        or not any(not suffix for _, suffix, _ in parts)
                    ):
                        raise ValueError(
                            "This candidate cannot safely teach a suffix rule"
                        )
                    if len({normalise(base) for base, _, _ in parts}) != 1:
                        raise ValueError(
                            "A learned rule requires exactly matching base titles"
                        )
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
                if name is not None:
                    target = next(g for g in groups if g["id"] == ids[0])
                    snapshot["groups"].append({"id": target["id"], "name": target["name"]})
                    db.execute("UPDATE groups SET name=? WHERE id=?", (name, ids[0]))
                description = f"Merged {len(ids)} {groups[0]['kind']} groups"
                if action == "merge_learn":
                    rules = self.get(db, "learned_rules", [])
                    snapshot["learned_rules"] = rules
                    rule = {
                        "id": time.time_ns(),
                        "kind": groups[0]["kind"],
                        "artist": groups[0]["artist"],
                        "artist_key": normalise(groups[0]["artist"]),
                        "suffix": next(suffix for _, suffix, _ in parts if suffix),
                        "applied": [],
                    }
                    self.put(db, "learned_rules", [*rules, rule])
                    description += f" and learned {rule['suffix']} for {rule['artist']}"
            elif action == "merge_versions":
                if len(ids) < 2:
                    raise ValueError("Select at least two versions")
                variants = db.execute(
                    f"SELECT * FROM resolved_variants WHERE id IN ({marks})", ids
                ).fetchall()
                if len(variants) != len(ids) or len({v["kind"] for v in variants}) != 1:
                    raise ValueError("Merge versions of the same type")
                if len({normalise(v["artist"]) for v in variants}) != 1:
                    raise ValueError("Merge versions by the same artist")
                first = next(v for v in variants if v["id"] == ids[0])
                gid = db.execute(
                    "INSERT INTO groups(kind,artist,name) VALUES (?,?,?)",
                    (first["kind"], first["artist"], name or canonical_title(first["name"], first["kind"]))
                ).lastrowid
                snapshot["variants"] = [
                    {"id": v["id"], "override_group": v["override_group"]} for v in variants
                ]
                # Move automatic aliases only when every member was selected.
                # A partial selection must not bring unselected versions along.
                for source_gid in {v["group_id"] for v in variants}:
                    members = db.execute(
                        "SELECT id FROM resolved_variants WHERE group_id=?", (source_gid,)
                    ).fetchall()
                    if all(v["id"] in ids for v in members):
                        aliases = db.execute("SELECT * FROM aliases WHERE group_id=?", (source_gid,)).fetchall()
                        snapshot["aliases"].extend(dict(a) for a in aliases)
                        db.execute("UPDATE aliases SET group_id=? WHERE group_id=?", (gid, source_gid))
                db.execute(f"UPDATE variants SET override_group=? WHERE id IN ({marks})", [gid, *ids])
                description = f"Merged {len(ids)} selected {first['kind']} versions"
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
            self.put(db, "analysis_revision", self.get(db, "analysis_revision", 0) + 1)
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
            if "learned_rules" in before:
                previous_ids = {r["id"] for r in before["learned_rules"]}
                for rule in self.get(db, "learned_rules", []):
                    if rule["id"] not in previous_ids:
                        for vid in rule["applied"]:
                            db.execute(
                                "UPDATE variants SET override_group=NULL WHERE id=?",
                                (vid,),
                            )
                self.put(db, "learned_rules", before["learned_rules"])
            if "dismissed_candidates" in before:
                self.put(db, "dismissed_candidates", before["dismissed_candidates"])
            for g in before.get("groups", []):
                db.execute("UPDATE groups SET name=? WHERE id=?", (g["name"], g["id"]))
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
            self.put(db, "analysis_revision", self.get(db, "analysis_revision", 0) + 1)
            db.execute("UPDATE grouping_events SET undone=1 WHERE id=?", (event["id"],))
