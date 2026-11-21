"""Generic volatile pixel stream controller for Govee pixeld (52×32 RGB)."""
import base64
import binascii
import json
import socket
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WIDTH, HEIGHT, FRAME_BYTES = 52, 32, 4992
MAX_FRAMES = 2400
with open("/data/options.json", encoding="utf-8") as file:
    opts = json.load(file)
HOST = opts["host"]
DEFAULT_TIMEOUT = int(opts.get("default_timeout", 30))
MAX_TIMEOUT = int(opts.get("max_timeout", 3600))
state = {"frames": (), "fps": 12., "start": 0., "expires": 0., "loop": False, "last_error": None}
lock = threading.RLock()
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def clear():
    state.update(frames=(), start=0., expires=0., loop=False)

def snapshot():
    now = time.monotonic()
    frames = state["frames"]
    if not frames or now >= state["expires"]:
        return None
    index = int((now - state["start"]) * state["fps"])
    if not state["loop"] and index >= len(frames):
        return None
    return frames[index % len(frames)]

def sender():
    """Stream even static frames for pixeld live-mode heartbeat; never transmit at idle."""
    while True:
        with lock:
            frame = snapshot()
            if frame is None and state["frames"]:
                clear()
        if frame is not None:
            try:
                udp.sendto(frame, (HOST, 7777))
                with lock:
                    state["last_error"] = None
            except OSError as exc:
                with lock:
                    state["last_error"] = str(exc)
        time.sleep(1/12)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def reply(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
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
                frame = snapshot()
                self.reply(200, {"active": frame is not None, "seconds_remaining":
                    max(0, round(state["expires"] - time.monotonic(), 1)) if frame is not None else 0,
                    "frame_count": len(state["frames"]), "fps": state["fps"], "loop": state["loop"],
                    "host": HOST, "width": WIDTH, "height": HEIGHT, "last_error": state["last_error"]})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 2 <= n <= 4_000_000:
                return self.reply(413, {"error": "request exceeds 4 MB"})
            body = json.loads(self.rfile.read(n))
            if not isinstance(body, dict):
                raise ValueError("expected JSON object")
            if self.path == "/api/clear":
                with lock:
                    clear()
                return self.reply(200, {"ok": True, "active": False})
            if self.path == "/api/heartbeat":
                timeout = int(body.get("timeout", DEFAULT_TIMEOUT))
                if not 1 <= timeout <= MAX_TIMEOUT:
                    raise ValueError("timeout out of range")
                with lock:
                    if snapshot() is None:
                        return self.reply(409, {"error": "nothing playing"})
                    state["expires"] = time.monotonic() + timeout
                return self.reply(200, {"ok": True, "timeout": timeout})
            if self.path == "/api/show":
                frame = base64.b64decode(body["rgb_base64"], validate=True)
                if len(frame) != FRAME_BYTES:
                    raise ValueError("expected exactly 4992 RGB bytes")
                fps, frames, looping = 12., (frame,), True
                timeout = int(body.get("timeout", DEFAULT_TIMEOUT))
            elif self.path == "/api/play":
                fps = float(body.get("fps", 12))
                if not 1 <= fps <= 12:
                    raise ValueError("fps must be 1–12")
                raw = base64.b64decode(body["frames_zlib_base64"], validate=True)
                decoder = zlib.decompressobj()
                decoded = decoder.decompress(raw, FRAME_BYTES * MAX_FRAMES + 1)
                if decoder.unconsumed_tail or len(decoded) > FRAME_BYTES * MAX_FRAMES or not decoder.eof:
                    raise ValueError("animation exceeds limits or invalid zlib")
                decoded += decoder.flush()
                if len(decoded) == 0 or len(decoded) % FRAME_BYTES:
                    raise ValueError("decompressed payload must contain whole 4992-byte RGB frames")
                frames = tuple(decoded[i:i+FRAME_BYTES] for i in range(0, len(decoded), FRAME_BYTES))
                looping = bool(body.get("loop", False))
                duration = len(frames) / fps
                timeout = int(body.get("timeout", max(1, int(duration + .999))))
            else:
                return self.reply(404, {"error": "not found"})
            if not 1 <= timeout <= MAX_TIMEOUT:
                raise ValueError("timeout out of range")
            with lock:
                state.update(frames=frames, fps=fps, loop=looping,
                             start=time.monotonic(), expires=time.monotonic()+timeout)
            return self.reply(200, {"ok": True, "frames": len(frames), "fps": fps, "loop": looping,
                                    "timeout": timeout})
        except (KeyError, ValueError, TypeError, binascii.Error, json.JSONDecodeError, zlib.error) as exc:
            return self.reply(400, {"error": str(exc)})

threading.Thread(target=sender, name="udp-sender", daemon=True).start()
print(f"Govee pixel transport :8098 -> {HOST}:7777", flush=True)
ThreadingHTTPServer(("0.0.0.0", 8098), Handler).serve_forever()
