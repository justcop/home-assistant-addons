"""Govee pixeld 52x32 live UDP controller. No saved animation files."""
import base64
import binascii
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WIDTH, HEIGHT, FRAME_BYTES = 52, 32, 52 * 32 * 3
with open("/data/options.json", encoding="utf-8") as f:
    opts = json.load(f)
HOST = opts["host"]
DEFAULT_TIMEOUT = int(opts.get("default_timeout", 30))
MAX_TIMEOUT = int(opts.get("max_timeout", 3600))
state = {"frame": None, "expires": 0.0, "last_error": None}
lock = threading.RLock()
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def sender():
    while True:
        with lock:
            now = time.monotonic()
            if state["frame"] is not None and now >= state["expires"]:
                state["frame"] = None
            frame = state["frame"]
        if frame is not None:
            try:
                udp.sendto(frame, (HOST, 7777))
                with lock:
                    state["last_error"] = None
            except OSError as exc:
                with lock:
                    state["last_error"] = str(exc)
        time.sleep(0.5)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def reply(self, code, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/api/status"):
            with lock:
                active = state["frame"] is not None and time.monotonic() < state["expires"]
                self.reply(200, {
                    "active": active,
                    "seconds_remaining": max(0, round(state["expires"] - time.monotonic(), 1)) if active else 0,
                    "host": HOST,
                    "width": WIDTH,
                    "height": HEIGHT,
                    "idle": "stop UDP; pixeld returns to Idle Black in about 3 seconds",
                    "last_error": state["last_error"],
                })
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 2 <= n <= 12000:
                return self.reply(413, {"error": "invalid request size"})
            body = json.loads(self.rfile.read(n))
            if not isinstance(body, dict):
                raise ValueError("expected JSON object")
            if self.path == "/api/clear":
                with lock:
                    state["frame"] = None
                    state["expires"] = 0
                return self.reply(200, {"ok": True, "active": False})
            timeout = int(body.get("timeout", DEFAULT_TIMEOUT))
            if not 1 <= timeout <= MAX_TIMEOUT:
                raise ValueError("timeout out of range")
            if self.path == "/api/show":
                frame = base64.b64decode(body["rgb_base64"], validate=True)
                if len(frame) != FRAME_BYTES:
                    raise ValueError("expected exactly 4992 RGB bytes")
                with lock:
                    state["frame"] = frame
                    state["expires"] = time.monotonic() + timeout
                return self.reply(200, {"ok": True, "timeout": timeout})
            if self.path == "/api/heartbeat":
                with lock:
                    if state["frame"] is None or time.monotonic() >= state["expires"]:
                        return self.reply(409, {"error": "no active image"})
                    state["expires"] = time.monotonic() + timeout
                return self.reply(200, {"ok": True, "timeout": timeout})
            return self.reply(404, {"error": "not found"})
        except (KeyError, ValueError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
            return self.reply(400, {"error": str(exc)})

threading.Thread(target=sender, name="udp-sender", daemon=True).start()
print(f"Govee UDP controller listening on :8098, sending to {HOST}:7777", flush=True)
ThreadingHTTPServer(("0.0.0.0", 8098), Handler).serve_forever()
