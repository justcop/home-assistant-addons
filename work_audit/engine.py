"""Independent observation tracks. Never equate idle or a loaded record with a consultation."""
import collections
import datetime as dt
import json
import math
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

TRACKS = ("computer", "idle", "phone", "location", "work", "patient", "phone_overlap")
DEFAULTS = {
    "timezone": "Europe/London", "idle_threshold_seconds": 120,
    "heartbeat_timeout_seconds": 90, "retention_days": 90,
    "work_apps": ["EmisWeb.exe", "Docman10.Desktop.exe"],
    "useful_context_patterns": ["AccuRx", "Docman", "e-Referral", "DrIQ", "NICE", "BNF"],
    "distracting_context_patterns": [], "phone_screen_entity": "",
    "phone_screen_on_states": ["on", "true", "unlocked"], "location_entity": "",
    "phone_screen_off_states": ["off", "false", "locked"],
    "work_location_states": ["Work"],
}


class Audit:
    def __init__(self, db_path, options=None, clock=time.time):
        self.options = DEFAULTS | (options or {})
        self.zone = ZoneInfo(self.options["timezone"])
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS segments (
                id INTEGER PRIMARY KEY, track TEXT NOT NULL, start REAL NOT NULL,
                end REAL NOT NULL, value TEXT NOT NULL, detail TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS segments_range ON segments(track, start, end);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, ts REAL NOT NULL, source TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_time ON events(ts);
        """)
        # Deliberately do not resume an old segment after an add-on restart.
        self.active = {}
        self.status = None
        self.status_at = None
        self.idle = None
        self.idle_at = None
        self.mqtt_connected = False
        self.ha_connected = False
        self.entities = {}
        self.candidates = []
        self.health = {"mqtt": "Connecting", "home_assistant": "Connecting"}
        self.override = "auto"
        self.last_prune = 0
        self.tick()

    def _event(self, source, kind, payload, now):
        self.db.execute("INSERT INTO events(ts,source,kind,payload) VALUES(?,?,?,?)",
                        (now, source, kind, json.dumps(payload, ensure_ascii=False)))

    def connection(self, source, connected, message=None):
        with self.lock:
            now = self.clock()
            if source == "mqtt":
                self.mqtt_connected = connected
                if not connected:
                    self.status = self.status_at = self.idle = self.idle_at = None
            else:
                self.ha_connected = connected
                if not connected:
                    self.entities.clear()
            self.health[source if source == "mqtt" else "home_assistant"] = message or ("Connected" if connected else "Disconnected")
            self._event(source, "connection", {"connected": connected}, now)
            self.tick(now)

    def mqtt(self, kind, payload, retained=False):
        with self.lock:
            now = self.clock()
            # A retained status has no observation time and can be days old.
            if retained:
                return
            if kind == "status":
                data = json.loads(payload)
                if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in ("app", "context", "patient")):
                    raise ValueError("Status needs app, context and patient strings")
                data = {k: data[k][:1024] for k in ("app", "context", "patient")}
                changed = data != self.status
                self.status, self.status_at = data, now
            else:
                data = float(payload)
                if not math.isfinite(data) or not 0 <= data <= 86400 * 365:
                    raise ValueError("Invalid idle seconds")
                changed = True
                self.idle, self.idle_at = data, now
            if changed:
                self._event("mqtt", kind, data, now)
            self.tick(now)

    def entity(self, entity_id, state):
        if entity_id not in (self.options["phone_screen_entity"], self.options["location_entity"]):
            return
        with self.lock:
            now = self.clock()
            # Do not store coordinates or unrelated HA entity attributes.
            data = {"state": str(state.get("state", "unknown"))[:256],
                    "last_updated": state.get("last_updated")}
            changed = self.entities.get(entity_id, {}).get("state") != data["state"]
            self.entities[entity_id] = data
            if changed:
                self._event("home_assistant", entity_id, data, now)
            self.tick(now)

    def set_override(self, value):
        if value not in ("auto", "work", "away", "break"):
            raise ValueError("Invalid session mode")
        with self.lock:
            self.override = value
            self._event("manual", "session", {"mode": value}, self.clock())
            self.tick()

    def _entity_state(self, option):
        entity_id = self.options[option]
        if not entity_id:
            return "not_configured"
        if not self.ha_connected:
            return "unknown"
        state = self.entities.get(entity_id, {}).get("state", "unknown")
        return "unknown" if state.lower() in ("unknown", "unavailable") else state

    def observe(self, now):
        fresh_status = self.mqtt_connected and self.status_at is not None and now - self.status_at < self.options["heartbeat_timeout_seconds"]
        fresh_idle = self.mqtt_connected and self.idle_at is not None and now - self.idle_at < self.options["heartbeat_timeout_seconds"]
        context = self.status if fresh_status else {}
        if not fresh_status:
            computer = "unknown"
        elif context["app"].lower() == "offline":
            computer = "offline"
        elif context["app"].lower() == "emisweb.exe":
            computer = "notes_editor" if context["context"] == "EditConsultation" else "clinical"
        elif context["app"].lower() in [x.lower() for x in self.options["work_apps"]]:
            computer = "clinical"
        elif context["app"].lower() in ("chrome.exe", "msedge.exe", "firefox.exe"):
            text = context["context"].casefold()
            useful = any(x.casefold() in text for x in self.options["useful_context_patterns"] if x)
            distraction = any(x.casefold() in text for x in self.options["distracting_context_patterns"] if x)
            computer = "browser_ambiguous" if useful and distraction else "useful_browsing" if useful else "distracting_browsing" if distraction else "browser_unclassified"
        else:
            computer = "other_app"
        if computer == "offline" or not fresh_idle:
            idle = "unknown"
        else:
            # Estimate inactivity between the existing 30-second heartbeat samples.
            idle = "idle" if self.idle + now - self.idle_at >= self.options["idle_threshold_seconds"] else "recent_input"
        screen = self._entity_state("phone_screen_entity")
        phone = "unknown"
        if screen in ("unknown", "not_configured"):
            phone = screen
        elif screen.casefold() in [x.casefold() for x in self.options["phone_screen_on_states"]]:
            phone = "screen_on"
        elif screen.casefold() in [x.casefold() for x in self.options["phone_screen_off_states"]]:
            phone = "screen_off"
        location = self._entity_state("location_entity")
        work = ("at_work" if location.casefold() in [x.casefold() for x in self.options["work_location_states"]] else "away") if location not in ("unknown", "not_configured") else "unknown"
        if self.override != "auto":
            work = {"work": "at_work", "away": "away", "break": "break"}[self.override]
        patient = context.get("patient", "None") if computer not in ("unknown", "offline") else "unknown"
        if patient in ("", "None"):
            patient = "no_record"
        overlap = "possible_phone_use" if work == "at_work" and phone == "screen_on" and idle == "idle" else "unknown" if work == "unknown" or phone in ("unknown", "not_configured") or idle == "unknown" else "no_overlap"
        return {
            "computer": (computer, context), "idle": (idle, {}),
            "phone": (phone, {"reported_state": screen}), "location": (location, {}),
            "work": (work, {"mode": self.override}), "patient": (patient, {}),
            "phone_overlap": (overlap, {}),
        }

    def tick(self, now=None):
        with self.lock:
            now = self.clock() if now is None else now
            values = self.observe(now)
            for track, (value, detail) in values.items():
                detail_json = json.dumps(detail, sort_keys=True, ensure_ascii=False)
                previous = self.active.get(track)
                if previous:
                    # A long collector or system pause is missing coverage, not continued activity.
                    end = min(now, previous["end"] + 15)
                    self.db.execute("UPDATE segments SET end=? WHERE id=?", (end, previous["id"]))
                    if previous["value"] == value and previous["detail"] == detail_json and now - previous["end"] <= 15:
                        previous["end"] = now
                        continue
                row = self.db.execute("INSERT INTO segments(track,start,end,value,detail) VALUES(?,?,?,?,?)",
                                      (track, now, now, value, detail_json))
                self.active[track] = {"id": row.lastrowid, "value": value, "detail": detail_json, "end": now}
            if now - self.last_prune > 3600:
                cutoff = now - self.options["retention_days"] * 86400
                self.db.execute("DELETE FROM segments WHERE end<?", (cutoff,))
                self.db.execute("DELETE FROM events WHERE ts<?", (cutoff,))
                self.last_prune = now
            self.db.commit()

    def bounds(self, day):
        date = dt.date.fromisoformat(day)
        start = dt.datetime.combine(date, dt.time(), self.zone)
        end = dt.datetime.combine(date + dt.timedelta(days=1), dt.time(), self.zone)
        return start.timestamp(), end.timestamp()

    def day(self, day, work_only=False):
        start, midnight = self.bounds(day)
        end = min(midnight, self.clock())
        with self.lock:
            rows = self.db.execute("SELECT * FROM segments WHERE end>? AND start<? ORDER BY start,id", (start, end)).fetchall()
            tracks = {track: [] for track in TRACKS}
            aliases = {}
            for row in rows:
                a, b = max(start, row["start"]), min(end, row["end"])
                if b <= a:
                    continue
                value = row["value"]
                if row["track"] == "patient" and value not in ("no_record", "unknown"):
                    aliases.setdefault(value, "Record " + str(len(aliases) + 1))
                    value = aliases[value]
                detail = json.loads(row["detail"])
                detail.pop("patient", None)
                tracks[row["track"]].append({"start": a, "end": b, "value": value, "detail": detail})
            for track in TRACKS:
                filled, cursor = [], start
                for segment in tracks[track]:
                    if segment["start"] > cursor:
                        filled.append({"start": cursor, "end": segment["start"], "value": "unknown", "detail": {}})
                    filled.append(segment)
                    cursor = segment["end"]
                if end > cursor:
                    filled.append({"start": cursor, "end": end, "value": "unknown", "detail": {}})
                tracks[track] = filled
            windows = [(x["start"], x["end"]) for x in tracks["work"] if x["value"] == "at_work"] if work_only else [(start, end)]
            totals = {}
            for track, segments in tracks.items():
                sums = collections.defaultdict(float)
                for s in segments:
                    sums[s["value"]] += sum(max(0, min(s["end"], b) - max(s["start"], a)) for a, b in windows)
                totals[track] = dict(sums)
            episodes = []
            for s in tracks["patient"]:
                if not s["value"].startswith("Record "):
                    continue
                for a, b in windows:
                    left, right = max(a, s["start"]), min(b, s["end"])
                    if right > left:
                        episodes.append({"record": s["value"], "start": left, "end": right, "seconds": right-left})
            return {"date": day, "timezone": str(self.zone), "start": start, "end": max(start, end),
                    "day_end": midnight, "tracks": tracks, "totals": totals, "episodes": episodes,
                    "work_only": work_only, "covered_window_seconds": sum(max(0,b-a) for a,b in windows),
                    "health": dict(self.health), "live": {k: v[0] for k,v in self.observe(self.clock()).items() if k != "patient"},
                    "mode": self.override, "entities": dict(self.entities),
                    "config": {k: self.options[k] for k in ("phone_screen_entity", "location_entity", "work_location_states", "idle_threshold_seconds")}}
