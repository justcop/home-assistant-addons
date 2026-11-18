"""Personal gig attendance and short-lived setlist.fm cache.

Attendance is user-supplied and permanent. Third-party API responses are cached
for a configurable number of days, then deleted. All setlists stay unverified;
refresh intervals are anchored to the performance date, not to job execution.
"""
import calendar
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from .grouping import normalise

SETLIST_ID = re.compile(r"^[0-9a-f]{8}$", re.I)
SETLIST_URL = re.compile(r"^https://(?:www\.)?setlist\.fm/setlist/[^?#]+-([0-9a-f]{8})\.html(?:[?#].*)?$", re.I)
SCHEMA = """
CREATE TABLE IF NOT EXISTS gigs (
 id INTEGER PRIMARY KEY,
 artist TEXT NOT NULL, event_date TEXT NOT NULL,
 venue TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '',
 event_type TEXT NOT NULL CHECK(event_type IN ('festival','concert')),
 festival TEXT NOT NULL DEFAULT '', setlist_id TEXT,
 source_url TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
 last_checked INTEGER, last_attempt INTEGER, retry_after INTEGER,
 last_error TEXT NOT NULL DEFAULT '',
 UNIQUE(artist,event_date,venue,event_type,festival)
);
CREATE INDEX IF NOT EXISTS gig_date ON gigs(event_date);
CREATE INDEX IF NOT EXISTS gig_setlist_id ON gigs(setlist_id);
CREATE TABLE IF NOT EXISTS gig_setlist_cache (
 gig_id INTEGER PRIMARY KEY REFERENCES gigs(id) ON DELETE CASCADE,
 fetched_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
 payload TEXT NOT NULL, version_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS gig_api_usage (
 request_day TEXT PRIMARY KEY, used INTEGER NOT NULL
);
"""


def anniversary(day, year):
    return date(year, day.month, min(day.day, calendar.monthrange(year, day.month)[1]))


def month_anniversary(day, months):
    number = day.year * 12 + day.month - 1 + months
    year, month = divmod(number, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def schedule_dates(played, through):
    """The requested daily/weekly/monthly schedule, then yearly anniversaries.

    29 February is handled as 28 February in non-leap years. The historical
    schedule starts with the anniversary at one year; later years never drift.
    """
    for n in range(1, 8):
        candidate = played + timedelta(days=n)
        if candidate <= through:
            yield candidate
    for n in (14, 21, 28):
        candidate = played + timedelta(days=n)
        if candidate <= through:
            yield candidate
    for n in range(1, 13):
        candidate = month_anniversary(played, n)
        if candidate <= through and candidate > played + timedelta(days=28):
            yield candidate
    for year in range(played.year + 1, through.year + 2):
        candidate = anniversary(played, year)
        if candidate <= through and candidate > month_anniversary(played, 12):
            yield candidate


def next_due(played, last_checked, today):
    """Return the earliest outstanding scheduled check, or today for a new gig.

    Missed intervals collapse to a single request. A delayed annual refresh
    doesn't shift the following year's anniversary.
    """
    played = date.fromisoformat(played)
    if not last_checked:
        return today
    checked = datetime.fromtimestamp(last_checked, timezone.utc).date()
    for candidate in sorted(set(schedule_dates(played, today + timedelta(days=400)))):
        if candidate > checked:
            return candidate
    return anniversary(played, max(today.year + 1, played.year + 1))


def extract_id(value):
    value = str(value or "").strip()
    if SETLIST_ID.fullmatch(value):
        return value.lower()
    match = SETLIST_URL.fullmatch(value)
    return match.group(1).lower() if match else None


def clean_date(value):
    value = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Each gig needs a valid date (YYYY-MM-DD or DD-MM-YYYY).")


def import_row(row):
    if not isinstance(row, dict):
        raise ValueError("Each gig must be an object.")
    def first(*names):
        for name in names:
            value = row.get(name)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""
    artist = first("artist", "Artist", "artist_name", "Artist Name", "performer")[:200]
    if not artist:
        raise ValueError("Each gig needs an artist.")
    event_date = clean_date(first("date", "Date", "event_date", "eventDate"))
    venue = first("venue", "Venue", "venue_name")[:200]
    event_type = first("type", "event_type", "gig_type", "Type").casefold()
    festival = first("festival", "Festival", "festival_name", "festivalName")[:200]
    if event_type not in ("concert", "festival"):
        event_type = "festival" if festival or "festival" in first("event", "Event").casefold() else "concert"
    if event_type == "festival" and not festival:
        festival = first("event", "Event")[:200]
    url = first("setlist_url", "Setlist URL", "Setlist URL (if known)", "url", "source_url", "setlistfm_url")
    id_value = first("setlist_id", "setlistId") or url
    setlist_id = extract_id(id_value)
    if url and not extract_id(url):
        raise ValueError("Setlist URLs must be genuine setlist.fm concert URLs.")
    return dict(artist=artist, event_date=event_date, venue=venue,
                location=first("location", "city", "City", "location_name")[:200],
                event_type=event_type, festival=festival,
                setlist_id=setlist_id,
                source_url=url if url else
                    (f"https://www.setlist.fm/setlist/-{setlist_id}.html" if False else ""),
                notes=first("notes", "Notes")[:1000])


def flatten_songs(payload):
    songs = []
    for part in (payload.get("sets") or {}).get("set", []):
        for song in part.get("song", []):
            name = song.get("name") if isinstance(song, dict) else None
            if name:
                songs.append({"name": name, "cover": (song.get("cover") or {}).get("name"),
                              "encore": part.get("encore")})
    return songs


class GigService:
    def __init__(self, db, config, enabled=True, opener=None):
        self.db = db
        self.config = config
        self.opener = opener or urllib.request.urlopen
        self.stop = threading.Event()
        self.thread = None
        with self.db.connect() as conn:
            conn.executescript(SCHEMA)
        self.purge()
        if enabled:
            self.thread = threading.Thread(target=self._loop, daemon=True, name="setlist-refresh")
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)

    def purge(self, now=None):
        now = int(now or time.time())
        # Also honour retention reductions made in add-on options.
        cutoff = now - self.config["setlist_cache_retention_days"] * 86400
        with self.db.connect() as conn:
            conn.execute("DELETE FROM gig_setlist_cache WHERE expires_at<=? OR fetched_at<=?", (now, cutoff))
            conn.execute("DELETE FROM gig_api_usage WHERE request_day<?",
                         ((datetime.fromtimestamp(now, timezone.utc).date() - timedelta(days=14)).isoformat(),))

    def import_records(self, rows):
        if not isinstance(rows, list) or len(rows) > 3000:
            raise ValueError("Provide an array of up to 3,000 gigs.")
        parsed = [import_row(row) for row in rows]
        with self.db.connect() as conn:
            for gig in parsed:
                existing = None
                if gig["setlist_id"]:
                    existing = conn.execute("SELECT id FROM gigs WHERE setlist_id=?",
                                            (gig["setlist_id"],)).fetchone()
                if not existing:
                    existing = conn.execute("""SELECT id FROM gigs
                      WHERE lower(artist)=lower(?) AND event_date=? AND lower(venue)=lower(?)
                      AND event_type=? AND lower(festival)=lower(?)""",
                      (gig["artist"], gig["event_date"], gig["venue"],
                       gig["event_type"], gig["festival"])).fetchone()
                if existing:
                    conn.execute("""UPDATE gigs SET artist=?,event_date=?,venue=?,location=?,
                       event_type=?,festival=?,setlist_id=COALESCE(?,setlist_id),
                       source_url=CASE WHEN ?<>'' THEN ? ELSE source_url END, notes=?
                       WHERE id=?""",
                       (gig["artist"], gig["event_date"], gig["venue"], gig["location"],
                        gig["event_type"], gig["festival"], gig["setlist_id"],
                        gig["source_url"], gig["source_url"], gig["notes"], existing["id"]))
                else:
                    conn.execute("""INSERT INTO gigs
                      (artist,event_date,venue,location,event_type,festival,setlist_id,source_url,notes)
                      VALUES (:artist,:event_date,:venue,:location,:event_type,:festival,
                      :setlist_id,:source_url,:notes)""", gig)
        return {"imported": len(parsed)}

    def list_gigs(self, limit=1500):
        self.purge()
        with self.db.connect() as conn:
            rows = [dict(r) for r in conn.execute("""
                SELECT g.*, c.version_id, c.expires_at, c.fetched_at, c.payload
                FROM gigs g LEFT JOIN gig_setlist_cache c ON c.gig_id=g.id
                ORDER BY event_date DESC, artist COLLATE NOCASE LIMIT ?""", (limit,))]
        now = datetime.now(timezone.utc).date()
        for row in rows:
            row["next_due"] = next_due(row["event_date"], row["last_checked"], now).isoformat()
            cached = row.pop("payload", None)
            if cached:
                data = json.loads(cached)
                row["songs"] = flatten_songs(data)
                row["attribution_url"] = data.get("url") or row["source_url"]
            else:
                row["songs"] = []
                row["attribution_url"] = row["source_url"]
            row["status"] = "unverified"
        return rows

    def events_for_artist(self, artist_key, start=None, end=None):
        with self.db.connect() as conn:
            names = [r[0] for r in conn.execute("""
                SELECT display_name FROM artist_aliases WHERE canonical_key=?""",
                (artist_key,))]
            names += [r[0] for r in conn.execute("""
                SELECT DISTINCT artist FROM scrobbles WHERE artist_group_key=?""",
                (artist_key,))]
            names.append(artist_key)
            valid = {normalise(n) for n in names}
            rows = conn.execute("""SELECT id, artist, event_date, venue, location,
                event_type, festival, source_url, setlist_id FROM gigs
                ORDER BY event_date""").fetchall()
        selected = [dict(r) for r in rows if normalise(r["artist"]) in valid
                    and (not start or r["event_date"] >= start)
                    and (not end or r["event_date"] <= end)]
        # Compare equal-length 30-day windows, excluding incomplete historical
        # coverage and recent gigs without a full post-gig observation period.
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(self.config.get("timezone", "Europe/London"))
        with self.db.connect() as conn:
            earliest = conn.execute("SELECT MIN(ts) FROM scrobbles WHERE active=1").fetchone()[0]
            now = int(time.time())
            for gig in selected:
                day = date.fromisoformat(gig["event_date"])
                before = int(datetime.combine(day - timedelta(days=30),
                                              datetime.min.time(), tzinfo=tz).timestamp())
                middle = int(datetime.combine(day, datetime.min.time(), tzinfo=tz).timestamp())
                after = int(datetime.combine(day + timedelta(days=30),
                                             datetime.min.time(), tzinfo=tz).timestamp())
                gig["impact"] = None
                if earliest is None or earliest > before or after > now:
                    continue
                counts = conn.execute("""SELECT
                  SUM(CASE WHEN ts>=? AND ts<? THEN 1 ELSE 0 END) AS before_plays,
                  SUM(CASE WHEN ts>=? AND ts<? THEN 1 ELSE 0 END) AS after_plays
                  FROM scrobbles WHERE active=1 AND artist_group_key=?
                  AND ts>=? AND ts<?""",
                  (before, middle, middle, after, artist_key, before, after)).fetchone()
                before_plays, after_plays = counts["before_plays"] or 0, counts["after_plays"] or 0
                gig["impact"] = {
                    "before": before_plays, "after": after_plays,
                    "change_pct": round((after_plays - before_plays) / before_plays * 100)
                                  if before_plays else None,
                }
        return selected

    def status(self):
        today = datetime.now(timezone.utc).date()
        with self.db.connect() as conn:
            rows = conn.execute("SELECT event_date,last_checked,retry_after,setlist_id FROM gigs").fetchall()
            used = conn.execute("SELECT used FROM gig_api_usage WHERE request_day=?", (today.isoformat(),)).fetchone()
            cached = conn.execute("SELECT COUNT(*) FROM gig_setlist_cache").fetchone()[0]
        due = sum(next_due(r["event_date"], r["last_checked"], today) <= today
                  and (not r["retry_after"] or r["retry_after"] <= time.time()) for r in rows)
        return dict(total=len(rows), cached=cached, due=due,
                    unmatched=sum(not r["setlist_id"] for r in rows),
                    used=used[0] if used else 0,
                    budget=self.config["setlist_daily_request_budget"],
                    retention_days=self.config["setlist_cache_retention_days"],
                    enabled=bool(self.config.get("setlist_fm_api_key")),
                    rate_limited_until=self.db.meta("setlist_backoff_until", 0))

    def _request(self, path):
        url = "https://api.setlist.fm/rest/1.0/" + path
        req = urllib.request.Request(url, headers={
            "x-api-key": self.config["setlist_fm_api_key"],
            "Accept": "application/json",
            "User-Agent": "ListeningAnalytics/0.2 (personal, non-commercial)"
        })
        with self.opener(req, timeout=20) as response:
            result = response.read(1024 * 1024)
        data = json.loads(result)
        if not isinstance(data, dict):
            raise ValueError("Invalid setlist.fm response")
        return data

    def _loop(self):
        while not self.stop.is_set():
            try:
                self.purge()
                # Spread requests out, even if an API key has a generous quota.
                self.run_one()
            except Exception:
                pass  # Per-gig errors are persisted by run_one; never kill the worker.
            self.stop.wait(180 if self.config.get("setlist_fm_api_key") else 3600)

    def run_one(self, now=None):
        """Do at most one network request; safe to call repeatedly or after restarts."""
        if not self.config.get("setlist_fm_api_key"):
            return False
        stamp = int(now or time.time())
        today = datetime.fromtimestamp(stamp, timezone.utc).date()
        self.purge(stamp)
        if self.db.meta("setlist_backoff_until", 0) > stamp:
            return False
        with self.db.connect() as conn:
            used = conn.execute("SELECT used FROM gig_api_usage WHERE request_day=?", (today.isoformat(),)).fetchone()
            if used and used[0] >= self.config["setlist_daily_request_budget"]:
                return False
            rows = conn.execute("""SELECT * FROM gigs
                WHERE event_date<=? AND (retry_after IS NULL OR retry_after<=?)
                ORDER BY event_date DESC""", (today.isoformat(), stamp)).fetchall()
            due = [r for r in rows if next_due(r["event_date"], r["last_checked"], today) <= today]
            if not due:
                return False
            # Most urgent recent performances first; oldest overdue afterwards.
            due.sort(key=lambda r: (min((today - date.fromisoformat(r["event_date"])).days, 366),
                                    next_due(r["event_date"], r["last_checked"], today),
                                    r["id"]))
            gig = dict(due[0])
            conn.execute("""INSERT INTO gig_api_usage(request_day,used) VALUES (?,1)
                ON CONFLICT(request_day) DO UPDATE SET used=used+1""", (today.isoformat(),))
        try:
            if gig["setlist_id"]:
                data = self._request("setlist/" + urllib.parse.quote(gig["setlist_id"]))
            else:
                query = urllib.parse.urlencode({"artistName": gig["artist"],
                                                "date": date.fromisoformat(gig["event_date"]).strftime("%d-%m-%Y")})
                result = self._request("search/setlists?" + query)
                matches = [x for x in result.get("setlist", []) if
                           normalise((x.get("artist") or {}).get("name", "")) == normalise(gig["artist"])
                           and clean_date(x.get("eventDate", "")) == gig["event_date"]]
                # Never guess which stage/venue when multiple exact matches exist.
                if len(matches) != 1:
                    raise ValueError("No unique exact artist/date match; add a setlist URL manually")
                data = matches[0]
            if not isinstance(data.get("sets"), dict) or not extract_id(data.get("id")):
                raise ValueError("Setlist.fm returned an invalid setlist")
            if clean_date(data.get("eventDate", "")) != gig["event_date"] or normalise(
                (data.get("artist") or {}).get("name", "")) != normalise(gig["artist"]):
                raise ValueError("Setlist.fm returned a different performance")
            expiry = stamp + self.config["setlist_cache_retention_days"] * 86400
            with self.db.connect() as conn:
                conn.execute("""INSERT INTO gig_setlist_cache(gig_id,fetched_at,expires_at,payload,version_id)
                    VALUES (?,?,?,?,?) ON CONFLICT(gig_id) DO UPDATE SET
                    fetched_at=excluded.fetched_at, expires_at=excluded.expires_at,
                    payload=excluded.payload, version_id=excluded.version_id""",
                    (gig["id"], stamp, expiry, json.dumps(data),
                     str(data.get("versionId", ""))[:80]))
                conn.execute("""UPDATE gigs SET setlist_id=?,last_checked=?,last_attempt=?,
                    retry_after=NULL,last_error='' WHERE id=?""",
                    (data["id"], stamp, stamp, gig["id"]))
            return True
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError,
                KeyError, TypeError, json.JSONDecodeError) as exc:
            status = getattr(exc, "code", None)
            delay = 86400 if status == 429 else 21600 if status and status >= 500 else 604800
            if status == 429:
                try:
                    delay = max(delay, min(604800, int(exc.headers.get("Retry-After", 0))))
                except (ValueError, AttributeError, TypeError):
                    pass
            with self.db.connect() as conn:
                if status == 429:
                    # Provider's quota is global; do not try another gig
                    # until the backoff ends, regardless of per-gig priority.
                    self.db.put(conn, "setlist_backoff_until", stamp + delay)
                conn.execute("""UPDATE gigs SET last_attempt=?,retry_after=?,last_error=?
                    WHERE id=?""", (stamp, stamp + delay,
                    "Rate limited by setlist.fm" if status == 429 else
                    "Setlist refresh failed or was ambiguous", gig["id"]))
            return False
