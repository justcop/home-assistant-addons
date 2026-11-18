"""Rate-limited, resumable import of a user's attended setlist.fm concerts."""
import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request

SCHEMA = """
CREATE TABLE IF NOT EXISTS gig_attendance_sync(
 id INTEGER PRIMARY KEY CHECK(id=1),
 user_id TEXT NOT NULL, next_page INTEGER NOT NULL DEFAULT 1,
 total INTEGER NOT NULL DEFAULT 0, last_completed TEXT, last_error TEXT);
"""

class AttendanceImporter:
    """One paginated attendance scan per week; three pages per worker pass."""

    def __init__(self, store, opener=None):
        self.store = store
        self.db = store.db
        self.config = store.config
        self.opener = opener or urllib.request.urlopen
        with self.db.connect() as conn:
            conn.executescript(SCHEMA)

    def status(self):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM gig_attendance_sync WHERE id=1").fetchone()
            count = conn.execute("SELECT COUNT(*) FROM gigs").fetchone()[0]
            day = self.store.today().isoformat()
            budget_row = conn.execute("SELECT used FROM gig_requests WHERE day=?", (day,)).fetchone()
        user = self.config.get("setlistfm_user_id", "")
        result = dict(row) if row and row["user_id"] == user else {}
        return {
            "configured": bool(user and self.config.get("setlistfm_api_key")),
            "user_id": user, "imported": count,
            "next_page": result.get("next_page", 1),
            "total": result.get("total", 0),
            "last_completed": result.get("last_completed"),
            "last_error": result.get("last_error"),
            "requests_today": budget_row[0] if budget_row else 0,
            "daily_budget": self.config["setlist_daily_request_budget"],
        }

    def _reserve(self):
        day = self.store.today().isoformat()
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT used FROM gig_requests WHERE day=?", (day,)).fetchone()
            if row and row[0] >= self.config["setlist_daily_request_budget"]:
                return False
            conn.execute("""INSERT INTO gig_requests(day,used) VALUES(?,1)
                ON CONFLICT(day) DO UPDATE SET used=used+1""", (day,))
            return True

    @staticmethod
    def _as_record(item):
        if not isinstance(item, dict):
            raise ValueError("Invalid attendance entry")
        sid = str(item.get("id") or "")
        if not (sid and len(sid) <= 32 and sid.replace("-", "").isalnum()):
            raise ValueError("Invalid setlist.fm setlist ID")
        artist = str((item.get("artist") or {}).get("name") or "").strip()[:256]
        venue = str((item.get("venue") or {}).get("name") or "").strip()[:256]
        if not artist:
            raise ValueError("Missing gig artist")
        day = dt.datetime.strptime(item["eventDate"], "%d-%m-%Y").date().isoformat()
        url = str(item.get("url") or "")
        if not url.startswith(("https://www.setlist.fm/", "https://setlist.fm/")):
            url = ""
        return (sid, artist, day, venue, url[:500])

    def _apply_page(self, items, page, total, per_page, today, previous_complete):
        # Validate every item before modifying the local history.
        records = [(self._as_record(item), item) for item in items]
        done = page * per_page >= total or not items
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for (sid, artist, day, venue, url), item in records:
                row = conn.execute("SELECT id,last_checked FROM gigs WHERE setlist_id=?", (sid,)).fetchone()
                if not row:
                    row = conn.execute(
                        "SELECT id,last_checked FROM gigs WHERE artist=? AND date=? AND venue=?",
                        (artist, day, venue)).fetchone()
                fresh = not row or not row["last_checked"]
                if row:
                    gid = row[0]
                    conn.execute(
                        "UPDATE gigs SET setlist_id=?,setlist_url=? WHERE id=?",
                        (sid, url, gid))
                else:
                    cursor = conn.execute(
                        """INSERT INTO gigs(artist,date,venue,festival,setlist_id,setlist_url,show_type)
                           VALUES(?,?,?,'',?,?,'unknown')""",
                        (artist, day, venue, sid, url))
                    gid = cursor.lastrowid
                # Weekly attendance discovery must NOT reset the separate
                # per-gig anniversary schedule. Cache the embedded setlist
                # only when this is a new/unfetched attendance record.
                if fresh:
                    conn.execute(
                        """UPDATE gigs SET last_checked=?, next_retry=NULL,
                           version_id=?,updated_at=? WHERE id=?""",
                        (today.isoformat(), str(item.get("versionId") or ""),
                         str(item.get("lastUpdated") or ""), gid))
                    conn.execute(
                        """INSERT INTO gig_cache(gig_id,payload,fetched_at) VALUES(?,?,?)
                        ON CONFLICT(gig_id) DO UPDATE SET
                        payload=excluded.payload, fetched_at=excluded.fetched_at""",
                        (gid, json.dumps(item), int(time.time())))
            conn.execute(
                """UPDATE gig_attendance_sync SET next_page=?,total=?,
                   last_completed=?,last_error=NULL WHERE id=1""",
                (1 if done else page+1, total,
                 today.isoformat() if done else previous_complete))
        return done

    def run(self, maximum=3):
        user = self.config.get("setlistfm_user_id") or ""
        if not user or not self.config.get("setlistfm_api_key"):
            return 0
        today = self.store.today()
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM gig_attendance_sync WHERE id=1").fetchone()
            if not row or row["user_id"] != user:
                conn.execute(
                    """INSERT INTO gig_attendance_sync(id,user_id,next_page,total,last_completed,last_error)
                       VALUES(1,?,1,0,NULL,NULL)
                       ON CONFLICT(id) DO UPDATE SET user_id=excluded.user_id,
                       next_page=1,total=0,last_completed=NULL,last_error=NULL""",
                    (user,))
                page, completed = 1, None
            else:
                page, completed = row["next_page"], row["last_completed"]
        if completed and page == 1 and (today-dt.date.fromisoformat(completed)).days < 7:
            return 0
        processed = 0
        for _ in range(maximum):
            if not self._reserve():
                break
            url = ("https://api.setlist.fm/rest/1.0/user/" +
                   urllib.parse.quote(user, safe="") + "/attended?p=" + str(page))
            req = urllib.request.Request(
                url, headers={"x-api-key": self.config["setlistfm_api_key"],
                              "Accept":"application/json",
                              "User-Agent":"ListeningAnalytics/0.2 (personal gig tracker)"})
            try:
                with self.opener(req, timeout=15) as response:
                    raw = response.read(1024*1024+1)
                if len(raw) > 1024*1024:
                    raise ValueError("Attendance page too large")
                payload = json.loads(raw)
                items = payload["setlist"]
                total = int(payload["total"])
                per_page = int(payload.get("itemsPerPage", 20))
                if (not isinstance(items,list) or not 1<=per_page<=100 or
                        total<0 or page>1000):
                    raise ValueError("Invalid attendance pagination")
                done = self._apply_page(items,page,total,per_page,today,completed)
                self.store.infer_festivals()
                processed += 1
                if done:
                    break
                page += 1
            except (urllib.error.HTTPError,urllib.error.URLError,ValueError,TypeError,
                    KeyError,TimeoutError,OSError,json.JSONDecodeError) as exc:
                message = ("setlist.fm rate limit reached" if isinstance(exc,urllib.error.HTTPError)
                           and exc.code==429 else "Attendance sync failed; will retry")
                with self.db.connect() as conn:
                    conn.execute("UPDATE gig_attendance_sync SET last_error=? WHERE id=1",
                                 (message,))
                break
        return processed
