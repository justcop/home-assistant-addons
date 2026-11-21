"""Audio-reactive graphics only. No microphone, SSH or network dependencies.

Renderer choice and particle rules are supplied at runtime as JSON data.
No executable code is accepted from an API caller.
"""
import math
import random

WIDTH, HEIGHT, FRAME_BYTES = 52, 32, 52 * 32 * 3

class Psychedelic:
    """Legacy demo, retained as a reference renderer."""
    def render(self, features, t):
        out = bytearray(FRAME_BYTES)
        volume = features["volume"]
        bass = features["bass"]
        treble = features["treble"]
        pulse = 1.5 if features["beat"] else 1.0
        for y in range(HEIGHT):
            for x in range(WIDTH):
                dx, dy = x - 25.5, (y - 15.5) * 1.6
                radius, angle = math.hypot(dx, dy), math.atan2(dy, dx)
                wave = math.sin(radius * (.35 + bass*.2) - t*(2+volume*3) + math.sin(angle*5+t*.9))
                hue = angle * 2 + t*.7 + math.sin(radius*.3-t)*.65
                bright = min(1., max(0., .24+.35*wave+.23*volume)*pulse)
                i = (y*WIDTH+x)*3
                out[i] = int((.5+.5*math.sin(hue))*255*bright)
                out[i+1] = int((.5+.5*math.sin(hue+2.1))*255*bright)
                out[i+2] = int((.5+.5*math.sin(hue+4.2))*255*bright*(.7+.3*treble))
        return bytes(out)


def number(value, minimum, maximum, name):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(name + " must be numeric")
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(name + " outside allowed range")
    return value


def validate_scene(scene):
    """Small, safe particle-scene language. No expressions or Python evaluation."""
    if not isinstance(scene, dict):
        raise ValueError("scene must be an object")
    if scene.get("type") != "particles":
        raise ValueError("scene type must be particles")
    raw = scene.get("emitters", [])
    if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
        raise ValueError("scene requires 1–8 emitters")
    emitters = []
    for index, cfg in enumerate(raw):
        if not isinstance(cfg, dict):
            raise ValueError("emitter must be an object")
        def opt(name, default, lo, hi):
            return number(cfg.get(name, default), lo, hi, name)
        trigger = cfg.get("trigger", "periodic")
        if trigger not in ("periodic", "beat", "both"):
            raise ValueError("invalid trigger")
        palette = cfg.get("palette", [[250, 78, 105], [74, 194, 255], [255, 201, 83]])
        if not isinstance(palette, list) or not 1 <= len(palette) <= 16:
            raise ValueError("palette must contain 1–16 colours")
        colors = []
        for color in palette:
            if not isinstance(color, list) or len(color) != 3:
                raise ValueError("colours must be RGB triplets")
            colors.append(tuple(int(number(ch, 0, 255, "color")) for ch in color))
        emitters.append({
            "trigger": trigger,
            "interval": opt("interval", .48, .05, 5),
            "beat_boost": opt("beat_boost", 1.5, 0, 8),
            "count": int(opt("count", 2, 1, 20)),
            "origin_x": opt("origin_x", .5, 0, 1),
            "origin_y": opt("origin_y", 1, 0, 1),
            "spread_x": opt("spread_x", .8, 0, 1),
            "spread_y": opt("spread_y", 0, 0, 1),
            "velocity_x": opt("velocity_x", 0, -10, 10),
            "velocity_y": opt("velocity_y", -18, -18, 18),
            "velocity_variance": opt("velocity_variance", 4, 0, 18),
            "gravity": opt("gravity", 3, -15, 15),
            "lifetime": opt("lifetime", 1.1, .1, 8),
            "children": int(opt("children", 26, 0, 90)),
            "child_speed": opt("child_speed", 14, 0, 50),
            "child_gravity": opt("child_gravity", 9, -15, 35),
            "child_lifetime": opt("child_lifetime", 1.1, .1, 5),
            "energy": cfg.get("energy", "bass"),
            "palette": colors,
        })
        if emitters[-1]["energy"] not in ("volume", "bass", "mid", "treble"):
            raise ValueError("energy must be volume, bass, mid or treble")
    return emitters


class ParticleScene:
    """Generic audio-driven particle emitters with optional child bursts.

    Coordinates are pixels, speeds px/sec, and step uses measured wall time.
    """
    def __init__(self, scene):
        self.emitters = validate_scene(scene)
        self.parts = []
        self.last_t = None
        self.last_launch = [-999.] * len(self.emitters)
        self.rng = random.Random()
        self.last_beat = False

    def spawn(self, cfg, energy):
        rng = self.rng
        for _ in range(min(20, round(cfg["count"] * (1 + energy * cfg["beat_boost"])) )):
            x = (cfg["origin_x"] + (rng.random()-.5)*cfg["spread_x"])*(WIDTH-1)
            y = (cfg["origin_y"] + (rng.random()-.5)*cfg["spread_y"])*(HEIGHT-1)
            color = rng.choice(cfg["palette"])
            self.parts.append({
                "x":x, "y":y, "vx":cfg["velocity_x"]+(rng.random()-.5)*cfg["velocity_variance"],
                "vy":cfg["velocity_y"]+(rng.random()-.5)*cfg["velocity_variance"],
                "gravity":cfg["gravity"], "life":cfg["lifetime"], "max_life":cfg["lifetime"],
                "children":cfg["children"], "child_speed":cfg["child_speed"],
                "child_gravity":cfg["child_gravity"], "child_lifetime":cfg["child_lifetime"],
                "color":color,
            })

    def render(self, features, t):
        dt = min(.2, max(.001, t - self.last_t)) if self.last_t is not None else 1/12
        self.last_t = t
        beat = bool(features.get("beat", False))
        for i, cfg in enumerate(self.emitters):
            energy = max(0., min(1., features.get(cfg["energy"], 0.)))
            # Music modulates the launch rate even without a detected beat.
            due = t - self.last_launch[i] >= cfg["interval"] / (1 + energy * 2)
            triggered = (cfg["trigger"] in ("periodic", "both") and due) or (
                cfg["trigger"] in ("beat", "both") and beat and not self.last_beat and t - self.last_launch[i] > .13)
            if triggered:
                self.spawn(cfg, energy)
                self.last_launch[i] = t
        self.last_beat = beat
        out = bytearray(FRAME_BYTES)
        survivors = []
        for p in self.parts:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["vy"] += p["gravity"] * dt
            p["life"] -= dt
            if p["life"] <= 0:
                if p["children"]:
                    for j in range(p["children"]):
                        a = 2 * math.pi * (j+self.rng.uniform(-.2,.2))/p["children"]
                        speed = p["child_speed"] * self.rng.uniform(.45, 1.05)
                        survivors.append({
                            "x":p["x"], "y":p["y"],
                            "vx":math.cos(a)*speed, "vy":math.sin(a)*speed,
                            "gravity":p["child_gravity"],
                            "life":p["child_lifetime"]*self.rng.uniform(.5,1),
                            "max_life":p["child_lifetime"], "children":0,
                            "color":p["color"]
                        })
                continue
            if not -2 <= p["x"] < WIDTH+2 or not -2 <= p["y"] < HEIGHT+2:
                continue
            survivors.append(p)
            x,y = round(p["x"]),round(p["y"])
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                k=(y*WIDTH+x)*3
                strength=min(1, p["life"]/min(.3,p["max_life"]))
                for c in range(3):
                    out[k+c] = min(255, out[k+c]+round(p["color"][c]*strength))
        self.parts = survivors[:1600]
        return bytes(out)


def make_renderer(spec):
    if spec == "psychedelic":
        return Psychedelic()
    if isinstance(spec, dict):
        return ParticleScene(spec)
    raise ValueError("renderer must be 'psychedelic' or a validated scene object")
