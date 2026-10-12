"""Zero-dependency Home Assistant Ingress web server."""
import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from runtime import Companion
from ha_events import run_event_feed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
APP = Companion()
ROOT = Path(__file__).parent

class Handler(BaseHTTPRequestHandler):
    def send(self, code, data, mime="application/json; charset=utf-8"):
        if isinstance(data, (dict, list)):
            raw = json.dumps(data, ensure_ascii=False).encode("utf8")
        elif isinstance(data, str):
            raw = data.encode("utf8")
        else:
            raw = data
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlsplit(self.path).path.rstrip("/") or "/"
        try:
            if path == "/health":
                return self.send(200, {"ok": True})
            if path == "/api/status":
                return self.send(200, APP.status())
            if path == "/api/entities":
                return self.send(200, APP.entities())
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in files:
                name, mime = files[path]
                return self.send(200, (ROOT / name).read_bytes(), mime)
            return self.send(404, {"error": "Not found"})
        except Exception as exc:
            logging.exception("GET %s failed", path)
            return self.send(503, {"error": str(exc)})

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            if int(self.headers.get("Content-Length", "0")) > 8192:
                return self.send(413, {"error": "Request too large"})
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            data = json.loads(body or b"{}")
            if not isinstance(data, dict):
                raise ValueError("Expected JSON object")
            if path == "/api/track":
                token = str(APP.options.get("source_api_token") or "")
                supplied = self.headers.get("Authorization", "")
                if not token or not hmac.compare_digest(supplied, "Bearer " + token):
                    return self.send(401, {"error": "Bad or missing source token"})
                return self.send(200, APP.external_update(data))
            if path == "/api/config":
                if APP.active:
                    APP.stop()
                settings = APP.library.configure(data)
                APP.last_key = ""
                APP.searched.clear()
                return self.send(200, {"settings": settings})
            if path == "/api/search":
                return self.send(200, {"candidates": APP.search()})
            if path == "/api/approve":
                return self.send(200, {"approved": APP.approve(data)})
            if path == "/api/cast":
                return self.send(200, {"approved": APP.play_current()})
            if path == "/api/offset":
                with APP.lock:
                    cur = APP.current
                    record = APP.library.set_offset(cur["artist"], cur["title"], data.get("seconds"))
                    if APP.active and APP.active["song_key"] == __import__("model").song_key(cur["artist"], cur["title"]):
                        APP.active["offset"] = record["offset_seconds"]
                    APP.info = "Offset saved. It applies on the next launch or Spotify resume."
                return self.send(200, {"approved": record})
            if path == "/api/sync-lead":
                with APP.lock:
                    settings = APP.library.configure({"sync_lead_seconds": data.get("seconds")})
                    if APP.active:
                        APP.active["sync_lead_seconds"] = settings["sync_lead_seconds"]
                    APP.info = "Fine-tune saved; next launch/resume or explicit Realign now applies it."
                return self.send(200, {"sync_lead_seconds": settings["sync_lead_seconds"]})
            if path == "/api/align":
                return self.send(200, APP.align_manually())
            if path == "/api/stop":
                APP.stop()
                return self.send(200, {"ok": True})
            if path == "/api/forget":
                cur = APP.status()["current"]
                if cur["artist"]:
                    APP.library.forget(cur["artist"], cur["title"])
                return self.send(200, {"ok": True})
            return self.send(404, {"error": "Not found"})
        except (ValueError, KeyError) as exc:
            return self.send(400, {"error": str(exc)})
        except Exception as exc:
            logging.exception("POST %s failed", path)
            return self.send(503, {"error": str(exc)})

if __name__ == "__main__":
    threading.Thread(target=APP.run, name="music-video-monitor", daemon=True).start()
    threading.Thread(target=run_event_feed, args=(APP,), name="music-video-ha-events", daemon=True).start()
    logging.info("Music Video Companion listening on 8099")
    ThreadingHTTPServer(("0.0.0.0", 8099), Handler).serve_forever()
