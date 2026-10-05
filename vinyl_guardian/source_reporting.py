"""Deliver attribution to Listening Analytics. Only undelivered notifications live here."""
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests


def submit_scrobble(network, artist, title, timestamp, album=None, post=requests.post):
    """Unlike pylast.scrobble(), inspect ignoredMessage and preserve corrections."""
    params = {"method": "track.scrobble", "api_key": network.api_key,
              "sk": network.session_key, "artist": artist, "track": title,
              "timestamp": str(int(timestamp))}
    if album and album != "Unknown":
        params["album"] = album
    signature = "".join(key + str(params[key]) for key in sorted(params)) + network.api_secret
    params["api_sig"] = hashlib.md5(signature.encode("utf-8")).hexdigest()
    params["format"] = "json"
    response = post("https://ws.audioscrobbler.com/2.0/", data=params, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("Last.fm did not accept the submission")
    scrobbles = payload.get("scrobbles", {})
    row = scrobbles.get("scrobble", {})
    if isinstance(row, list):
        row = row[0] if len(row) == 1 else {}
    if (int(scrobbles.get("@attr", {}).get("accepted", 0)) != 1
            or str(row.get("ignoredMessage", {}).get("code", "")) != "0"):
        return None
    def text(field, fallback):
        value = row.get(field)
        return (value.get("#text") if isinstance(value, dict) else value) or fallback
    return {"source": "vinyl", "timestamp": int(timestamp),
            "artist": text("artist", artist), "title": text("track", title)}


class SourceReporter:
    def __init__(self, path, url, token, username, log=print):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Use the analyser's HTTP or HTTPS base URL without credentials")
        if not token or not username:
            raise ValueError("Source reporting needs a token and Last.fm username")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.url = url.rstrip("/") + "/api/source-reports"
        self.token, self.username, self.log = token, username, log
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS pending (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, created INTEGER NOT NULL)""")

    def connect(self):
        # contextlib.closing plus transaction ensures connections are closed.
        from contextlib import contextmanager
        @contextmanager
        def connection():
            db = sqlite3.connect(self.path, timeout=10)
            try:
                with db:
                    yield db
            finally:
                db.close()
        return connection()

    def enqueue(self, report):
        payload = json.dumps(dict(report, username=self.username), sort_keys=True)
        key = hashlib.sha256(payload.encode()).hexdigest()
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO pending VALUES (?,?,?)",
                       (key, payload, int(time.time())))

    def flush(self, post=requests.post):
        with self.connect() as db:
            rows = db.execute("SELECT id,payload FROM pending ORDER BY created,id LIMIT 20").fetchall()
        for key, payload in rows:
            try:
                response = post(self.url, json=json.loads(payload),
                                headers={"Authorization": "Bearer " + self.token},
                                timeout=10, allow_redirects=False)
                if response.status_code != 200 or response.json().get("ok") is not True:
                    raise ValueError("Notification was not acknowledged")
                with self.connect() as db:
                    db.execute("DELETE FROM pending WHERE id=?", (key,))
            except Exception as exc:
                # Exceptions/URLs/headers can contain credentials; log only type.
                self.log(f"Listening Analytics notification pending ({type(exc).__name__}); will retry.")
                return False
        return True

    def start(self):
        def run():
            delay = 30
            while True:
                try:
                    delivered = self.flush()
                except Exception as exc:
                    self.log(f"Listening Analytics queue unavailable ({type(exc).__name__}); will retry.")
                    delivered = False
                delay = 30 if delivered else min(delay * 2, 300)
                time.sleep(delay)
        threading.Thread(target=run, name="listening-source-reports", daemon=True).start()
