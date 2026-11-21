"""Proof-of-concept: microphone on Govee via SSH -> audio features -> RGB renderer.

No raw microphone samples are stored. This module deliberately keeps the
audio reader separate from the rendering function.
"""
import array
import math
import os
from pathlib import Path
import random
from renderers import make_renderer
import select
import subprocess
import threading
import time

SIZE = 52 * 32 * 3
SAMPLE_RATE = 16000
BLOCK_SAMPLES = 2048

def ensure_ssh_key(key_file):
    key_path = Path(key_file)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if not key_path.is_file():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)], check=True)
        key_path.chmod(0o600)
    return key_path.with_suffix(key_path.suffix + ".pub").read_text(encoding="utf-8").strip()


class MicStream:
    def __init__(self, host, key_file, user="root"):
        self.host = host
        self.key_file = key_file
        self.user = user
        self.proc = None

    def __enter__(self):
        if not os.path.isfile(self.key_file):
            raise FileNotFoundError("SSH key not found at configured ssh_key_file")
        # Reuse proven pixeld capture command from src/features.c.
        command = "exec arecord -q -D hw:0,0 -f S16_LE -r 16000 -c 1 -t raw"
        self.proc = subprocess.Popen(
            ["ssh", "-T", "-i", self.key_file, "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new",
             "-o", "UserKnownHostsFile=/data/known_hosts",
             f"{self.user}@{self.host}", command],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0)
        return self

    def read(self, deadline=3):
        """Collect a full PCM block despite partial SSH reads."""
        buf = bytearray()
        until = time.monotonic() + deadline
        while len(buf) < BLOCK_SAMPLES * 2:
            wait = until - time.monotonic()
            if wait <= 0:
                raise TimeoutError("No microphone audio from Govee within 3 seconds")
            readable, _, _ = select.select([self.proc.stdout], [], [], wait)
            if not readable:
                continue
            chunk = os.read(self.proc.stdout.fileno(), BLOCK_SAMPLES * 2 - len(buf))
            if not chunk:
                err = self.proc.stderr.read(400).decode("utf-8", errors="replace")
                raise RuntimeError("Microphone SSH stream ended: " + err)
            buf.extend(chunk)
        samples = array.array("h")
        samples.frombytes(buf)
        return samples

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


def power_at(samples, freq):
    # Goertzel tone detector, used as a lightweight frequency-band proxy.
    step = max(1, len(samples)//512)
    reduced = samples[::step]
    n = len(reduced)
    omega = 2 * math.pi * freq * step / SAMPLE_RATE
    coeff = 2 * math.cos(omega)
    q1 = q2 = 0.
    for val in reduced:
        q0 = val / 32768 + coeff * q1 - q2
        q2, q1 = q1, q0
    return min(1., math.sqrt(max(0., q1*q1 + q2*q2 - coeff*q1*q2)) / n * 8)


class Analyser:
    def __init__(self):
        self.avg = .02
        self.last_beat = 0

    def features(self, samples):
        level = math.sqrt(sum((v/32768)**2 for v in samples) / len(samples))
        self.avg = .92*self.avg + .08*level
        now = time.monotonic()
        beat = level > max(.014, self.avg*1.5) and now-self.last_beat > .20
        if beat:
            self.last_beat = now
        return {
            "volume": min(1., level*9),
            "bass": power_at(samples, 110),
            "mid": power_at(samples, 650),
            "treble": power_at(samples, 2600),
            "beat": beat,
            "beat_strength": min(1., level/(self.avg+.001)/3) if beat else 0,
        }


class ReactiveController:
    def __init__(self, options, lock, state):
        self.options, self.lock, self.state = options, lock, state
        self.generation = 0
        self.thread = None
        self.metrics = {"running": False, "error": None, "audio": None, "frames": 0}

    def stop(self):
        with self.lock:
            self.generation += 1
            self.metrics["running"] = False

    def start(self, timeout, renderer_spec='psychedelic'):
        renderer = make_renderer(renderer_spec)
        with self.lock:
            self.generation += 1
            generation = self.generation
            self.metrics = {"running": True, "error": None, "audio": None, "frames": 0,
                            "renderer": "psychedelic" if isinstance(renderer_spec, str) else "scene"}
            self.state.update(frames=(bytes(SIZE),), loop=True, fps=12.,
                              start=time.monotonic(), expires=time.monotonic()+timeout)
        self.thread = threading.Thread(target=self.run, args=(generation, renderer),
                                       name="reactive-mic", daemon=True)
        self.thread.start()

    def run(self, generation, renderer):
        try:
            with MicStream(self.options["host"],
                           self.options.get("ssh_key_file", "/data/ssh/id_ed25519"),
                           self.options.get("ssh_user", "root")) as mic:
                analyzer = Analyser()
                started = time.monotonic()
                while True:
                    with self.lock:
                        if generation != self.generation or time.monotonic() >= self.state["expires"]:
                            return
                    audio = analyzer.features(mic.read())
                    frame = renderer.render(audio, time.monotonic()-started)
                    with self.lock:
                        if generation != self.generation:
                            return
                        self.state["frames"] = (frame,)
                        self.state["start"] = time.monotonic()
                        self.metrics["audio"] = audio
                        self.metrics["frames"] += 1
        except Exception as exc:
            with self.lock:
                if generation == self.generation:
                    self.metrics["error"] = str(exc)
                    self.state.update(frames=(), expires=0)
        finally:
            with self.lock:
                if generation == self.generation:
                    self.metrics["running"] = False
