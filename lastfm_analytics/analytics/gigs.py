"""Personal gig attendance and bounded, expiring setlist.fm API cache."""
import calendar
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from .grouping import normalise

SCHEMA = """
CREATE TABLE IF NOT EXISTS gigs (
 id INTEGER PRIMARY KEY, artist TEXT NOT NULL, date TEXT NOT NULL,
 venue TEXT NOT NULL DEFAULT '', festival TEXT NOT NULL DEFAULT '',
 setlist_id TEXT, setlist_url TEXT NOT NULL DEFAULT '',
 last_checked TEXT, version_id TEXT, updated_at TEXT,
 next_retry TEXT, UNIQUE(artist,date,venue));
CREATE INDEX IF NOT EXISTS gig_refresh ON gigs(date,last_checked,next_retry);
CREATE TABLE IF NOT EXISTS gig_cache (
 gig_id INTEGER PRIMARY KEY REFERENCES gigs(id) ON DELETE CASCADE,
 payload TEXT NOT NULL, fetched_at INTEGER NOT NULL);
"""

def init(db):
    with db.connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA foreign_keys=ON")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(gigs)")}
        if "show_type" not in columns:
            conn.execute("ALTER TABLE gigs ADD COLUMN show_type TEXT NOT NULL DEFAULT 'unknown'")
            conn.execute("UPDATE gigs SET show_type='festival' WHERE festival!=''")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(gigs)")}
        if "type_override" not in columns:
            conn.execute("ALTER TABLE gigs ADD COLUMN type_override INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE gigs SET type_override=1 WHERE festival!=''")

def anniversary(d, year):
    return date(year, d.month, min(d.day, calendar.monthrange(year, d.month)[1]))

def due(gig, today):
    """Calendar-based checks; never slide anniversaries after a late check."""
    d = date.fromisoformat(gig["date"])
    if d > today:
        return False
    last = date.fromisoformat(gig["last_checked"]) if gig["last_checked"] else None
    if gig["next_retry"] and date.fromisoformat(gig["next_retry"]) > today:
        return False
    if last is None:
        return True
    age = (today - d).days
    if age <= 7:
        return last < today
    if age <= 30:
        # Fixed seven-day boundaries relative to the original performance.
        return (last-d).days // 7 < age // 7
    if age <= 365 and today < anniversary(d, d.year+1):
        # Fixed month boundaries relative to gig date, clamped for short months.
        month_index = lambda day: (day.year-d.year)*12 + day.month-d.month - (day.day < min(d.day, calendar.monthrange(day.year, day.month)[1]))
        return month_index(last) < month_index(today)
    # After the first year, anniversary is the ONLY yearly due date.
    latest = anniversary(d, today.year)
    if latest > today:
        latest = anniversary(d, today.year-1)
    return latest > d and (last is None or last < latest)

class GigStore:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.zone = ZoneInfo(config["timezone"])
        init(db)

    def today(self):
        return datetime.fromtimestamp(time.time(), self.zone).date()

    def import_rows(self, rows):
        if not isinstance(rows, list) or len(rows)>1500:
            raise ValueError("Expected up to 1,500 gig records")
        prepared=[]
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid gig record")
            artist=str(row.get("artist") or "").strip()[:256]
            day=str(row.get("date") or "").strip()
            try:
                date.fromisoformat(day)
            except ValueError:
                raise ValueError("Dates must use YYYY-MM-DD") from None
            if not artist:
                raise ValueError("Every gig needs an artist")
            venue=str(row.get("venue") or "").strip()[:256]
            festival=str(row.get("festival") or row.get("festival_name") or "").strip()[:256]
            sid=str(row.get("setlist_id") or row.get("setlistfm_id") or "").strip()
            url=str(row.get("setlist_url") or "").strip()
            if url and not (url.startswith("https://www.setlist.fm/") or url.startswith("https://setlist.fm/")):
                raise ValueError("Setlist URL must use HTTPS on setlist.fm")
            if not sid and "/setlist/" in url:
                sid=url.split("?",1)[0].rstrip("/").rsplit("-",1)[-1].removesuffix(".html")
            if sid and (len(sid)>32 or not all(c.isalnum() or c=="-" for c in sid)):
                raise ValueError("Invalid setlist.fm identifier")
            prepared.append((artist,day,venue,festival,sid or None,url[:500]))
        with self.db.connect() as conn:
            for artist,day,venue,festival,sid,url in prepared:
                conn.execute("""INSERT INTO gigs
                   (artist,date,venue,festival,setlist_id,setlist_url,show_type,type_override)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(artist,date,venue) DO UPDATE SET
                   festival=CASE WHEN excluded.festival!='' THEN excluded.festival ELSE gigs.festival END,
                   show_type=CASE WHEN excluded.festival!='' THEN 'festival' ELSE gigs.show_type END,
                   type_override=CASE WHEN excluded.festival!='' THEN 1 ELSE gigs.type_override END,
                   setlist_id=COALESCE(excluded.setlist_id,gigs.setlist_id),
                   setlist_url=CASE WHEN excluded.setlist_url!='' THEN excluded.setlist_url ELSE gigs.setlist_url END""",
                   (artist,day,venue,festival,sid,url,
                    "festival" if festival else "unknown", 1 if festival else 0))
        self.infer_festivals()
        return len(prepared)

    def list(self, artist=None):
        now=int(time.time())
        with self.db.connect() as conn:
            conn.execute("DELETE FROM gig_cache WHERE fetched_at < ?", (now-int(self.config["setlist_cache_retention_days"])*86400,))
            rows=[dict(r) for r in conn.execute("""SELECT g.*,c.payload,c.fetched_at FROM gigs g
                LEFT JOIN gig_cache c ON c.gig_id=g.id
                ORDER BY g.date DESC,g.artist""")]
            if artist:
                # Setlist.fm spellings and user-merged artist aliases can differ.
                canonical = dict((r["artist_key"], r["canonical_key"]) for r in
                    conn.execute("SELECT artist_key,canonical_key FROM artist_aliases"))
                target = normalise(artist)
                target = canonical.get(target, target)
                rows = [r for r in rows if
                    canonical.get(normalise(r["artist"]), normalise(r["artist"])) == target]
        for row in rows:
            raw=row.pop("payload",None)
            if raw:
                data=json.loads(raw)
                row["sets"]=data.get("sets",{}).get("set",[])
            else:
                row["sets"]=None
            row["due"]=due(row,self.today())
        return rows

    def timeline(self, artist, granularity="month"):
        if granularity not in ("day","week","month"):
            raise ValueError("Invalid granularity")
        expression={"day":"strftime('%Y-%m-%d',s.ts,'unixepoch')",
                    "week":"strftime('%Y-%m-%d',s.ts,'unixepoch','weekday 0','-6 days')",
                    "month":"strftime('%Y-%m',s.ts,'unixepoch')"}[granularity]
        with self.db.connect() as conn:
            lookup = normalise(artist)
            alias = conn.execute("SELECT canonical_key FROM artist_aliases WHERE artist_key=?",
                                 (lookup,)).fetchone()
            canonical = alias[0] if alias else lookup
            rows=[dict(r) for r in conn.execute(f"""SELECT {expression} period,COUNT(*) listens
                FROM scrobbles s WHERE s.active=1 AND
                  (s.artist_group_key=? OR
                  (s.artist_group_key IS NULL AND source_key(s.artist)=?))
                GROUP BY period ORDER BY period""",(canonical,lookup))]
        return {"listens":rows,"gigs":self.list(artist),"granularity":granularity}

    def infer_festivals(self):
        """Infer festivals from >=4 performances at one venue over consecutive days.

        Only inferred labels are rewritten. Manual corrections remain authoritative.
        Three-act bills with support bands are ordinary concerts, not festivals.
        """
        from collections import defaultdict
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = [dict(row) for row in conn.execute(
                "SELECT id, date, venue FROM gigs WHERE venue != '' ORDER BY venue,date")]
            buckets = defaultdict(list)
            for row in rows:
                buckets[row["venue"].strip().casefold()].append(row)
            inferred = set()
            for entries in buckets.values():
                dates = sorted({date.fromisoformat(r["date"]) for r in entries})
                chunks = []
                current = []
                for day in dates:
                    if current and (day - current[-1]).days > 1:
                        chunks.append(current)
                        current = []
                    current.append(day)
                if current:
                    chunks.append(current)
                for days in chunks:
                    ids = [r["id"] for r in entries if date.fromisoformat(r["date"]) in days]
                    if len(ids) >= 4:
                        inferred.update(ids)
            conn.execute(
                "UPDATE gigs SET show_type='concert' WHERE type_override=0")
            if inferred:
                placeholders = ",".join("?" for _ in inferred)
                conn.execute(
                    "UPDATE gigs SET show_type='festival' WHERE type_override=0 AND id IN ("+
                    placeholders+")", tuple(inferred))
        return len(inferred)

    def classify(self, gig_id, kind, festival=""):
        if kind not in ("unknown", "concert", "festival"):
            raise ValueError("Unsupported performance type")
        festival = str(festival or "").strip()[:256] if kind == "festival" else ""
        with self.db.connect() as conn:
            cur = conn.execute("UPDATE gigs SET show_type=?,festival=?,type_override=1 WHERE id=?",
                               (kind,festival,gig_id))
            if cur.rowcount != 1:
                raise ValueError("Unknown gig")

    def api_backoff_remaining(self):
        """Respect provider HTTP 429 without maintaining a local daily quota."""
        until = self.db.meta("setlist_api_retry_after", 0)
        return max(0, int(until or 0) - int(time.time()))

    def note_rate_limit(self, exc):
        # Respect Retry-After where supplied; otherwise avoid hammering the
        # service until the next hourly worker pass.
        delay = 3600
        retry_header = (exc.headers.get("Retry-After") if exc.headers else None)
        if retry_header:
            try:
                delay = max(1, int(retry_header))
            except (ValueError, TypeError):
                try:
                    from email.utils import parsedate_to_datetime
                    when = parsedate_to_datetime(retry_header)
                    delay = max(1, int(when.timestamp() - time.time()))
                except (ValueError, TypeError, OverflowError, IndexError):
                    pass
        with self.db.connect() as conn:
            self.db.put(conn, "setlist_api_retry_after", int(time.time()) + delay)

    def refresh(self, maximum=None):
        if not self.config.get("setlistfm_api_key"):
            return {"processed": 0, "reason": "No setlist.fm API key configured"}
        if self.api_backoff_remaining():
            return {"processed": 0, "reason": "Waiting for setlist.fm rate limit to reset"}
        today = self.today()
        with self.db.connect() as conn:
            records = [dict(r) for r in conn.execute(
                "SELECT * FROM gigs WHERE setlist_id IS NOT NULL ORDER BY date DESC")]
        pending = [r for r in records if due(r, today)]
        completed = 0
        for gig in pending[:maximum] if maximum is not None else pending:
            if self.api_backoff_remaining():
                break
            url = ("https://api.setlist.fm/rest/1.0/setlist/" +
                   urllib.parse.quote(gig["setlist_id"], safe=""))
            request = urllib.request.Request(url, headers={
                "x-api-key": self.config["setlistfm_api_key"],
                "Accept": "application/json",
                "User-Agent": "ListeningAnalytics/0.2 (personal gig tracker)"})
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    blob = response.read(1024 * 1024 + 1)
                if len(blob) > 1024 * 1024:
                    raise ValueError("Setlist response too large")
                data = json.loads(blob)
                if not isinstance(data, dict) or str(data.get("id")) != gig["setlist_id"]:
                    raise ValueError("Unexpected setlist response")
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError,
                    TimeoutError, OSError) as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                    self.note_rate_limit(exc)
                    # Do not advance any gig's check or retry schedule.
                    break
                with self.db.connect() as conn:
                    conn.execute("UPDATE gigs SET next_retry=? WHERE id=?",
                                 ((today + timedelta(days=1)).isoformat(), gig["id"]))
                continue
            with self.db.connect() as conn:
                conn.execute("""UPDATE gigs SET last_checked=?,next_retry=NULL,
                    version_id=?,updated_at=? WHERE id=?""",
                    (today.isoformat(), str(data.get("versionId") or ""),
                     str(data.get("lastUpdated") or ""), gig["id"]))
                conn.execute("""INSERT INTO gig_cache(gig_id,payload,fetched_at) VALUES(?,?,?)
                    ON CONFLICT(gig_id) DO UPDATE SET
                    payload=excluded.payload,fetched_at=excluded.fetched_at""",
                    (gig["id"], json.dumps(data), int(time.time())))
            completed += 1
        with self.db.connect() as conn:
            conn.execute("DELETE FROM gig_cache WHERE fetched_at<?",
                         (int(time.time()) - int(self.config["setlist_cache_retention_days"]) * 86400,))
        return {"processed": completed, "due": len(pending)}

class GigWorker:
    def __init__(self, store):
        self.store=store
        self.stop=threading.Event()
        self.thread=None
        from .attendance import AttendanceImporter
        self.importer=AttendanceImporter(store)
    def start(self):
        if self.thread is None:
            self.thread=threading.Thread(target=self.run,daemon=True,name="gig-sync")
            self.thread.start()
    def run(self):
        while not self.stop.is_set():
            try:
                self.importer.run()
                self.store.refresh()
            except Exception:
                # Keep the add-on running; next scheduled pass retries.
                pass
            self.stop.wait(3600)
    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)
