import contextlib
import hashlib
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

from .grouping import auto_key, canonical_title, normalise, review_title

SCHEMA = """
CREATE TABLE IF NOT EXISTS artwork_urls (
 artist_key TEXT NOT NULL, album_key TEXT NOT NULL, url TEXT, expires INTEGER NOT NULL,
 PRIMARY KEY(artist_key,album_key));
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
 artist_group_key TEXT,
 song_id INTEGER NOT NULL REFERENCES variants(id), album_id INTEGER REFERENCES variants(id),
 active INTEGER NOT NULL DEFAULT 1, UNIQUE(fingerprint, occurrence));
CREATE INDEX IF NOT EXISTS play_time ON scrobbles(active, ts);
CREATE INDEX IF NOT EXISTS play_artist ON scrobbles(artist_key, active, ts);
CREATE INDEX IF NOT EXISTS play_song ON scrobbles(song_id, active, ts);
CREATE INDEX IF NOT EXISTS play_album ON scrobbles(album_id, active, ts);
CREATE TABLE IF NOT EXISTS artist_aliases (
 artist_key TEXT PRIMARY KEY, canonical_key TEXT NOT NULL, display_name TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS artist_canonical ON artist_aliases(canonical_key);
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


def compact_raw(raw):
    """Retain music identifiers and cover metadata without whole Last.fm payloads."""
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key in ("image", "mbid"):
        if raw.get(key):
            result[key] = raw[key]
    for key in ("artist", "album"):
        item = raw.get(key)
        if isinstance(item, dict) and item.get("mbid"):
            result[key] = {"mbid": item["mbid"]}
    return result


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists() and self.path.stat().st_size > 0
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 3:
                raise RuntimeError("Database was created by a newer Listening Analytics version")
            if existed and version < 3 and db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scrobbles'"
            ).fetchone():
                backup = self.path.with_name(self.path.stem + ".before-v3.sqlite3")
                if not backup.exists():
                    with sqlite3.connect(backup) as destination:
                        db.backup(destination)
            db.executescript(SCHEMA)
            columns = {r[1] for r in db.execute("PRAGMA table_info(scrobbles)")}
            if "artist_group_key" not in columns:
                db.execute("ALTER TABLE scrobbles ADD COLUMN artist_group_key TEXT")
            columns = {r[1] for r in db.execute("PRAGMA table_info(variants)")}
            if "exact_key" not in columns:
                db.execute("ALTER TABLE variants ADD COLUMN exact_key TEXT")
            db.execute(
                """INSERT OR IGNORE INTO artist_aliases(artist_key, canonical_key, display_name)
                SELECT artist_key, artist_key, MIN(artist) FROM scrobbles GROUP BY artist_key"""
            )
            db.execute(
                """UPDATE scrobbles SET artist_group_key=COALESCE(
                (SELECT a.canonical_key FROM artist_aliases a WHERE a.artist_key=scrobbles.artist_key),
                artist_key) WHERE artist_group_key IS NULL"""
            )
            for row in db.execute("SELECT id,name FROM variants WHERE exact_key IS NULL").fetchall():
                db.execute("UPDATE variants SET exact_key=? WHERE id=?",
                           (normalise(row["name"]), row["id"]))
            db.execute("CREATE INDEX IF NOT EXISTS play_artist_group ON scrobbles(artist_group_key, active, ts)")
            db.execute("CREATE INDEX IF NOT EXISTS play_analysis ON scrobbles(active, ts, artist_group_key, song_id, album_id)")
            db.execute("CREATE INDEX IF NOT EXISTS variant_exact ON variants(kind,exact_key)")
            if version < 3:
                last = 0
                while True:
                    batch = db.execute(
                        "SELECT id,raw_json FROM scrobbles WHERE id>? AND length(raw_json)>200 ORDER BY id LIMIT 500",
                        (last,),
                    ).fetchall()
                    if not batch:
                        break
                    for entry in batch:
                        try:
                            data = compact_raw(json.loads(entry["raw_json"]))
                            db.execute("UPDATE scrobbles SET raw_json=? WHERE id=?",
                                (json.dumps(data, ensure_ascii=False), entry["id"]))
                        except (ValueError, TypeError):
                            pass
                    last = batch[-1]["id"]
                db.execute("PRAGMA user_version=3")
            if self.get(db, "artwork_pipeline", 0) < 2:
                # Previous releases rejected AudioDB's CDN and cached misses.
                # Preserve usable URLs but retry those misses on first access.
                db.execute("DELETE FROM artwork_urls WHERE url IS NULL")
                self.put(db, "artwork_pipeline", 2)

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
            old = self.get(db, key)
            self.put(db, key, value)
            if key == "import" and bool((old or {}).get("complete")) != bool((value or {}).get("complete")):
                self.put(db, "analysis_revision", self.get(db, "analysis_revision", 0) + 1)

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
            # A newly imported title joins identical titles belonging to a
            # manually merged artist, but not previously separated versions.
            root = db.execute(
                "SELECT canonical_key FROM artist_aliases WHERE artist_key=?",
                (normalise(artist),),
            ).fetchone()
            shared = None
            if root:
                candidates = db.execute(
                    """SELECT v.name, v.override_group, rv.group_id, v.artist
                    FROM resolved_variants rv JOIN variants v ON v.id=rv.id
                    JOIN artist_aliases aa ON aa.artist_key=source_key(v.artist)
                    WHERE v.kind=? AND v.exact_key=? AND aa.canonical_key=?""",
                    (kind, normalise(name), root[0]),
                ).fetchall()
                shared = next((r["group_id"] for r in candidates
                               if r["override_group"] is None), None)
            group = shared or db.execute(
                "INSERT INTO groups(kind,artist,name) VALUES (?,?,?)",
                (kind, artist, canonical_title(name, kind)),
            ).lastrowid
            db.execute("INSERT INTO aliases VALUES (?,?,?)", (kind, key, group))
        vid = db.execute(
            "INSERT INTO variants(kind,artist,name,auto_key,exact_key) VALUES (?,?,?,?,?)",
            (kind, artist, name, key, normalise(name)),
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
            previous = (
                {(r["fingerprint"], r["occurrence"]) for r in db.execute(
                    "SELECT fingerprint,occurrence FROM scrobbles WHERE active=1 AND ts>=? AND ts<?",
                    (start, end))}
                if reconcile else None
            )
            before_changes = db.total_changes
            if reconcile:
                db.execute(
                    "UPDATE scrobbles SET active=0 WHERE active=1 AND ts>=? AND ts<?", (start, end)
                )
            artist_keys = {}
            for row in rows:
                if not start <= row["ts"] < end:
                    raise ValueError("Scrobble outside committed window")
                fingerprint = hashlib.sha256(identity(row).encode()).hexdigest()
                counts[fingerprint] += 1
                artist_key = normalise(row["artist"])
                if artist_key not in artist_keys:
                    db.execute(
                        "INSERT OR IGNORE INTO artist_aliases VALUES (?,?,?)",
                        (artist_key, artist_key, row["artist"]),
                    )
                    artist_keys[artist_key] = db.execute(
                        "SELECT canonical_key FROM artist_aliases WHERE artist_key=?",
                        (artist_key,),
                    ).fetchone()[0]
                song = self.variant(db, "song", row["artist"], row["title"])
                album = (
                    self.variant(db, "album", row["artist"], row["album"])
                    if row["album"]
                    else None
                )
                db.execute(
                    """INSERT INTO scrobbles(fingerprint,occurrence,ts,artist,artist_key,title,album,raw_json,artist_group_key,song_id,album_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fingerprint,occurrence)
                    DO UPDATE SET active=1 WHERE active<>1""",
                    (
                        fingerprint,
                        counts[fingerprint],
                        row["ts"],
                        row["artist"],
                        artist_key,
                        row["title"],
                        row["album"],
                        json.dumps(compact_raw(row.get("raw", {})), ensure_ascii=False),
                        artist_keys[artist_key],
                        song,
                        album,
                    ),
                )
            changed = (previous != set(counts.items()) if reconcile
                       else db.total_changes != before_changes)
            for key, value in (checkpoint or {}).items():
                if key == "import" and bool(self.get(db, key, {}).get("complete")) != bool(value.get("complete")):
                    changed = True
            if changed:
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
            before_changes = db.total_changes
            db.execute(
                """INSERT INTO source_reports VALUES (?,?,?,?,?,?)
                ON CONFLICT(username,ts,artist_key,title_key) DO NOTHING""",
                (username.casefold(), ts, *keys, "vinyl", int(time.time())),
            )
            if db.total_changes > before_changes:
                self.put(db, "analysis_revision", self.get(db, "analysis_revision", 0) + 1)

    def change_artists(self, keys, name=None):
        """Merge artist identities and exact song/album titles; undo is atomic."""
        if (not isinstance(keys, list) or not 2 <= len(keys) <= 100
                or any(not isinstance(k, str) or not k or len(k) > 512 for k in keys)):
            raise ValueError("Select at least two artists")
        keys = list(dict.fromkeys(keys))
        if len(keys) < 2:
            raise ValueError("Select different artists")
        if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 1000):
            raise ValueError("Choose an artist display name")
        marks = ",".join("?" for _ in keys)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            artists = db.execute(
                f"SELECT * FROM artist_aliases WHERE canonical_key IN ({marks})", keys
            ).fetchall()
            if {a["canonical_key"] for a in artists} != set(keys):
                raise ValueError("Unknown artist or already merged")
            before = {"aliases": [], "variants": [], "groups": [],
                      "artist_aliases": [dict(a) for a in artists]}
            target = keys[0]
            db.execute(
                f"UPDATE artist_aliases SET canonical_key=? WHERE canonical_key IN ({marks})",
                [target, *keys],
            )
            if name:
                db.execute("UPDATE artist_aliases SET display_name=? WHERE artist_key=?",
                           (name.strip(), target))
            db.execute(
                f"UPDATE scrobbles SET artist_group_key=? WHERE artist_group_key IN ({marks})",
                [target, *keys],
            )
            # Only identical titles are linked across the now-confirmed artist
            # identity. Explicit version overrides always take precedence.
            members = db.execute(
                """SELECT v.kind,v.exact_key,v.auto_key,v.override_group,
                          rv.group_id,source_key(v.artist) artist_key
                   FROM variants v JOIN resolved_variants rv ON rv.id=v.id
                   JOIN artist_aliases aa ON aa.artist_key=source_key(v.artist)
                   WHERE aa.canonical_key=? ORDER BY v.id""",
                (target,),
            ).fetchall()
            by_title = {}
            for member in members:
                if member["override_group"] is None:
                    by_title.setdefault((member["kind"], member["exact_key"]), []).append(member)
            touched = set()
            for group in by_title.values():
                if len({m["artist_key"] for m in group}) < 2:
                    continue
                preferred = next((m for m in group if m["artist_key"] == target), group[0])
                for member in group:
                    alias_key = (member["kind"], member["auto_key"])
                    if alias_key in touched:
                        continue
                    old = db.execute(
                        "SELECT * FROM aliases WHERE kind=? AND auto_key=?", alias_key
                    ).fetchone()
                    if old and old["group_id"] != preferred["group_id"]:
                        before["aliases"].append(dict(old))
                        db.execute(
                            "UPDATE aliases SET group_id=? WHERE kind=? AND auto_key=?",
                            (preferred["group_id"], *alias_key),
                        )
                    touched.add(alias_key)
            self.put(db, "analysis_revision", self.get(db, "analysis_revision", 0) + 1)
            db.execute(
                "INSERT INTO grouping_events(ts,description,before_json) VALUES (?,?,?)",
                (int(time.time()), f"Merged {len(keys)} artist identities",
                 json.dumps(before, ensure_ascii=False)),
            )

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
            for artist in before.get("artist_aliases", []):
                db.execute(
                    "UPDATE artist_aliases SET canonical_key=?,display_name=? WHERE artist_key=?",
                    (artist["canonical_key"], artist["display_name"], artist["artist_key"]),
                )
            if before.get("artist_aliases"):
                affected = [a["artist_key"] for a in before["artist_aliases"]]
                marks = ",".join("?" for _ in affected)
                db.execute(
                    f"""UPDATE scrobbles SET artist_group_key=(
                    SELECT canonical_key FROM artist_aliases WHERE artist_key=scrobbles.artist_key)
                    WHERE artist_key IN ({marks})""", affected,
                )
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
            if before.get("artist_aliases"):
                # Titles created while the artists were merged must separate on
                # undo, too. Existing aliases were restored above.
                original = {a["artist_key"] for a in before["artist_aliases"]}
                previous_aliases = {(a["kind"], a["auto_key"]) for a in before["aliases"]}
                rows = db.execute(
                    """SELECT DISTINCT v.kind,v.auto_key,v.name,v.artist,g.artist AS group_artist
                    FROM variants v JOIN resolved_variants rv ON rv.id=v.id
                    JOIN groups g ON g.id=rv.group_id
                    WHERE v.override_group IS NULL"""
                ).fetchall()
                for row in rows:
                    alias_key = (row["kind"], row["auto_key"])
                    if (normalise(row["artist"]) not in original or
                            normalise(row["group_artist"]) == normalise(row["artist"]) or
                            alias_key in previous_aliases):
                        continue
                    new_group = db.execute(
                        "INSERT INTO groups(kind,artist,name) VALUES (?,?,?)",
                        (row["kind"], row["artist"], canonical_title(row["name"], row["kind"])),
                    ).lastrowid
                    db.execute(
                        "UPDATE aliases SET group_id=? WHERE kind=? AND auto_key=?",
                        (new_group, *alias_key),
                    )
                    previous_aliases.add(alias_key)
            self.put(db, "analysis_revision", self.get(db, "analysis_revision", 0) + 1)
            db.execute("UPDATE grouping_events SET undone=1 WHERE id=?", (event["id"],))
