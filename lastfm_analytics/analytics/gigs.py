"""Personal gig attendance and bounded, expiring setlist.fm API cache."""
import calendar
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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
CREATE TABLE IF NOT EXISTS gig_requests (
 day TEXT PRIMARY KEY, used INTEGER NOT NULL DEFAULT 0);
"""

def init(db):
    with db.connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA foreign_keys=ON")

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
                conn.execute("""INSERT INTO gigs(artist,date,venue,festival,setlist_id,setlist_url)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(artist,date,venue) DO UPDATE SET
                   festival=excluded.festival,
                   setlist_id=COALESCE(excluded.setlist_id,gigs.setlist_id),
                   setlist_url=CASE WHEN excluded.setlist_url!='' THEN excluded.setlist_url ELSE gigs.setlist_url END""",
                   (artist,day,venue,festival,sid,url))
        return len(prepared)

    def list(self, artist=None):
        now=int(time.time())
        with self.db.connect() as conn:
            conn.execute("DELETE FROM gig_cache WHERE fetched_at < ?", (now-int(self.config["setlist_cache_retention_days"])*86400,))
            rows=[dict(r) for r in conn.execute("""SELECT g.*,c.payload,c.fetched_at FROM gigs g
                LEFT JOIN gig_cache c ON c.gig_id=g.id
                WHERE (? IS NULL OR lower(g.artist)=lower(?))
                ORDER BY g.date DESC,g.artist""",(artist,artist))]
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
            rows=[dict(r) for r in conn.execute(f"""SELECT {expression} period,COUNT(*) listens
                FROM scrobbles s WHERE s.active=1 AND lower(s.artist)=lower(?)
                GROUP BY period ORDER BY period""",(artist,))]
        return {"listens":rows,"gigs":self.list(artist),"granularity":granularity}

    def refresh(self, maximum=None):
        if not self.config.get("setlistfm_api_key"):
            return {"processed":0,"reason":"No setlist.fm API key configured"}
        today=self.today()
        limit=max(0,int(self.config["setlist_daily_request_budget"]))
        if maximum is not None:
            limit=min(limit,int(maximum))
        with self.db.connect() as conn:
            used=conn.execute("SELECT used FROM gig_requests WHERE day=?",(today.isoformat(),)).fetchone()
            allowance=max(0,limit-(used[0] if used else 0))
            records=[dict(r) for r in conn.execute("SELECT * FROM gigs WHERE setlist_id IS NOT NULL ORDER BY date DESC")]
        pending=[r for r in records if due(r,today)]
        completed=0
        for gig in pending[:allowance]:
            # Reserve the request BEFORE using the network; limits survive crashes.
            with self.db.connect() as conn:
                conn.execute("""INSERT INTO gig_requests(day,used) VALUES(?,1)
                    ON CONFLICT(day) DO UPDATE SET used=used+1""",(today.isoformat(),))
            url="https://api.setlist.fm/rest/1.0/setlist/"+urllib.parse.quote(gig["setlist_id"],safe="")
            request=urllib.request.Request(url,headers={
                "x-api-key":self.config["setlistfm_api_key"],
                "Accept":"application/json",
                "User-Agent":"ListeningAnalytics/0.2 (personal gig tracker)"})
            try:
                with urllib.request.urlopen(request,timeout=15) as response:
                    blob=response.read(1024*1024+1)
                if len(blob)>1024*1024:
                    raise ValueError("Setlist response too large")
                data=json.loads(blob)
                if not isinstance(data,dict) or str(data.get("id"))!=gig["setlist_id"]:
                    raise ValueError("Unexpected setlist response")
            except (urllib.error.HTTPError,urllib.error.URLError,ValueError,TimeoutError,OSError) as exc:
                # Do not advance the gig's schedule on failure. A 429 should
                # stop this batch rather than consume the remaining daily quota.
                with self.db.connect() as conn:
                    conn.execute("UPDATE gigs SET next_retry=? WHERE id=?",((today+timedelta(days=1)).isoformat(),gig["id"]))
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                    break
                continue
            with self.db.connect() as conn:
                conn.execute("""UPDATE gigs SET last_checked=?,next_retry=NULL,version_id=?,updated_at=? WHERE id=?""",
                    (today.isoformat(),str(data.get("versionId") or ""),str(data.get("lastUpdated") or ""),gig["id"]))
                conn.execute("""INSERT INTO gig_cache(gig_id,payload,fetched_at) VALUES(?,?,?)
                    ON CONFLICT(gig_id) DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at""",
                    (gig["id"],json.dumps(data),int(time.time())))
            completed+=1
        with self.db.connect() as conn:
            conn.execute("DELETE FROM gig_cache WHERE fetched_at<?",
                (int(time.time())-int(self.config["setlist_cache_retention_days"])*86400,))
            conn.execute("DELETE FROM gig_requests WHERE day<?",((today-timedelta(days=45)).isoformat(),))
        return {"processed":completed,"due":len(pending),"budget_remaining":max(0,allowance-len(pending[:allowance]))}

class GigWorker:
    def __init__(self, store):
        self.store=store
        self.stop=threading.Event()
        self.thread=None
    def start(self):
        if self.thread is None:
            self.thread=threading.Thread(target=self.run,daemon=True,name="gig-sync")
            self.thread.start()
    def run(self):
        while not self.stop.is_set():
            try:
                self.store.refresh()
            except Exception:
                # Keep the add-on running; next scheduled pass retries.
                pass
            self.stop.wait(3600)
    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)
