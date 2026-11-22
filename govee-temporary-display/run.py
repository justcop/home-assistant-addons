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
from reactive import ReactiveController, ensure_ssh_key
from ha_countdown import CountdownBinding, render as render_countdown, selected_artwork_mode
from artwork_modes import get_artwork_rgb, list_artwork_modes

WIDTH, HEIGHT, FRAME_BYTES = 52, 32, 4992
MAX_FRAMES = 2400
with open("/data/options.json", encoding="utf-8") as file:
    opts = json.load(file)
HOST = opts["host"]
DEFAULT_TIMEOUT = int(opts.get("default_timeout", 30))
MAX_TIMEOUT = int(opts.get("max_timeout", 3600))
state = {"frames": (), "fps": 12., "start": 0., "expires": 0., "loop": False, "last_error": None, "owner": "idle"}
lock = threading.RLock()
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
reactive = ReactiveController(opts, lock, state)
ssh_key_path = opts.get('ssh_key_file', '/data/ssh/id_ed25519')
public_key = ensure_ssh_key(ssh_key_path)
countdown = CountdownBinding(lock, state)

def clear():
    state.update(frames=(), start=0., expires=0., loop=False, owner="idle")

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
        elif self.path == "/api/audio/ssh-public-key":
            self.reply(200, {"public_key": public_key,
                             "username": opts.get("ssh_user", "root"),
                             "host": HOST})
        elif self.path == "/api/artwork/modes":
            with lock:
                cfg=countdown.config
                self.reply(200, {"modes":list_artwork_modes(),
                                 "selection":cfg.get("artwork_mode") if cfg else None,
                                 "selected_artwork_mode":countdown.status.get("selected_artwork_mode"),
                                 "game_entity":cfg.get("game_entity") if cfg else None,
                                 "game_title":countdown.status.get("game_title")})
        elif self.path == "/api/countdown/status":
            with lock:
                self.reply(200, dict(countdown.status, owner=state.get("owner","idle")))
        elif self.path == "/api/audio/status":
            with lock:
                self.reply(200, dict(reactive.metrics))
        elif self.path == "/api/status":
            with lock:
                frame = snapshot()
                self.reply(200, {"active": frame is not None, "seconds_remaining":
                    max(0, round(state["expires"] - time.monotonic(), 1)) if frame is not None else 0,
                    "frame_count": len(state["frames"]), "fps": state["fps"], "loop": state["loop"],
                    "host": HOST, "width": WIDTH, "height": HEIGHT, "last_error": state["last_error"],
                    "owner": state.get("owner", "idle")})
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
                    reactive.stop()
                    clear()
                return self.reply(200, {"ok": True, "active": False})
            if self.path == "/api/artwork/show":
                mode=body.get("mode")
                timeout=int(body.get("timeout", DEFAULT_TIMEOUT))
                if not 1 <= timeout <= MAX_TIMEOUT:
                    raise ValueError("timeout out of range")
                frame=get_artwork_rgb(mode)
                with lock:
                    reactive.stop()
                    state.update(frames=(frame,), fps=12., loop=True,
                                 start=time.monotonic(),
                                 expires=time.monotonic()+timeout, owner="manual")
                return self.reply(200, {"ok":True,"mode":mode,"timeout":timeout})
            if self.path == "/api/countdown/preview":
                seconds = int(body.get("remaining", 3600))
                timeout = int(body.get("timeout", 10))
                if not 0 <= seconds <= 359999 or not 1 <= timeout <= 30:
                    raise ValueError("preview duration or time remaining outside range")
                with lock:
                    if countdown.config is None:
                        return self.reply(409, {"error": "configure countdown first"})
                    # Optional simulated title allows testing game selection
                    # without starting a PS5 session or modifying its timer.
                    title=body.get("game_title",countdown.status.get("game_title"))
                    if title is not None and (not isinstance(title,str) or len(title)>160):
                        raise ValueError("invalid game_title")
                    activity=body.get("activity","playing" if title else countdown.status.get("ps5_activity","none"))
                    if not isinstance(activity,str) or len(activity)>50:
                        raise ValueError("invalid activity")
                    mode=selected_artwork_mode(countdown.config,title,activity)
                    reactive.stop()
                    state.update(frames=(render_countdown(countdown.config,seconds,title,activity),),
                                 start=time.monotonic(),fps=12.,loop=True,
                                 expires=time.monotonic()+timeout, owner="manual")
                return self.reply(200, {"ok":True,"preview_seconds":timeout,"artwork_mode":mode})
            if self.path == "/api/countdown/configure":
                current = countdown.configure(body)
                return self.reply(200, {"ok": True, "binding": current})
            if self.path == "/api/reactive/start":
                timeout = int(body.get("timeout", DEFAULT_TIMEOUT))
                if not 1 <= timeout <= MAX_TIMEOUT:
                    raise ValueError("timeout out of range")
                key_path = ssh_key_path
                if not Path(key_path).is_file():
                    return self.reply(503, {"error": "SSH key missing from " + key_path})
                reactive.start(timeout, body.get("renderer", "psychedelic"))
                with lock:
                    state["owner"] = "reactive"
                return self.reply(200, {"ok": True, "timeout": timeout,
                                        "audio_source": "Govee built-in microphone",
                                        "renderer": reactive.metrics.get("renderer")})
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
                reactive.stop()
                state.update(frames=frames, fps=fps, loop=looping, owner="manual",
                             start=time.monotonic(), expires=time.monotonic()+timeout)
            return self.reply(200, {"ok": True, "frames": len(frames), "fps": fps, "loop": looping,
                                    "timeout": timeout})
        except (KeyError, ValueError, TypeError, binascii.Error, json.JSONDecodeError, zlib.error) as exc:
            return self.reply(400, {"error": str(exc)})

threading.Thread(target=sender, name="udp-sender", daemon=True).start()
print(f"Govee pixel transport :8098 -> {HOST}:7777", flush=True)
ThreadingHTTPServer(("0.0.0.0", 8098), Handler).serve_forever()
