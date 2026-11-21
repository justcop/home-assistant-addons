"""Govee pixeld 52x32 live UDP controller. No saved animation files."""
import base64
import binascii
import json
import math
import random
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WIDTH, HEIGHT, FRAME_BYTES = 52, 32, 52 * 32 * 3
with open("/data/options.json", encoding="utf-8") as f:
    opts = json.load(f)
HOST = opts["host"]
DEFAULT_TIMEOUT = int(opts.get("default_timeout", 30))
MAX_TIMEOUT = int(opts.get("max_timeout", 3600))
state = {"frame": None, "expires": 0.0, "last_error": None, "fireworks": False}
lock = threading.RLock()
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def render_fireworks(now, rockets, sparks):
    frame = bytearray(FRAME_BYTES)
    def put(x, y, color):
        x, y = int(x), int(y)
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            i = (y * WIDTH + x) * 3
            frame[i:i+3] = bytes(color)
    if random.random() < 0.13:
        rockets.append([random.randint(7, 45), 31.0, random.uniform(0.9, 1.8), random.randint(5, 18), random.choice([(255, 75, 96), (255, 190, 52), (69, 202, 255), (157, 91, 255), (87, 255, 139)])])
    for r in rockets[:]:
        r[1] -= r[2]
        put(r[0], r[1], r[4])
        put(r[0], r[1]+1, (85, 85, 85))
        if r[1] <= r[3]:
            for k in range(22):
                a = k * math.tau / 22
                speed = random.uniform(.45, 1.5)
                sparks.append([r[0], r[1], math.cos(a)*speed, math.sin(a)*speed, 1.0, r[4]])
            rockets.remove(r)
    for p in sparks[:]:
        p[0] += p[2]
        p[1] += p[3]
        p[3] += .035
        p[4] -= .035
        if p[4] <= 0:
            sparks.remove(p)
        else:
            put(p[0], p[1], tuple(int(c*p[4]) for c in p[5]))
    return bytes(frame)

def sender():
    rockets, sparks = [], []
    while True:
        with lock:
            now = time.monotonic()
            if state["frame"] is not None and now >= state["expires"]:
                state["frame"] = None
            if state["fireworks"] and state["frame"] is not None:
                state["frame"] = render_fireworks(now, rockets, sparks)
            elif not state["fireworks"]:
                rockets.clear()
                sparks.clear()
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
        if self.path in ("/", "/index.html"):
            html = Path("/index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif self.path == "/api/status":
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
                    "fireworks": state["fireworks"] and active,
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
                    state["fireworks"] = False
                return self.reply(200, {"ok": True, "active": False})
            timeout = int(body.get("timeout", DEFAULT_TIMEOUT))
            if not 1 <= timeout <= MAX_TIMEOUT:
                raise ValueError("timeout out of range")
            if self.path == "/api/fireworks":
                with lock:
                    state["frame"] = bytes(FRAME_BYTES)
                    state["expires"] = time.monotonic() + timeout
                    state["fireworks"] = True
                return self.reply(200, {"ok": True, "effect": "fireworks", "timeout": timeout})
            if self.path == "/api/show":
                frame = base64.b64decode(body["rgb_base64"], validate=True)
                if len(frame) != FRAME_BYTES:
                    raise ValueError("expected exactly 4992 RGB bytes")
                with lock:
                    state["frame"] = frame
                    state["fireworks"] = False
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
