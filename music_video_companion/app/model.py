"""Conservative song matching and persistence."""
import json
import math
import re
import unicodedata
import threading
import os
from pathlib import Path

def norm(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", value))

def song_key(artist, title):
    title = re.sub(r"\s*[(\[]\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?\s*[)\]]\s*$", "", str(title or ""), flags=re.I)
    return norm(artist) + "|" + norm(title)

EXCLUDED = ("lyric video", "lyrics video", "official audio", "audio only",
            "visualizer", "visualiser", "fan made", "fan edit", "reaction",
            "karaoke", "cover version", "full album", "behind the scenes",
            "live performance", "live at", "live from", "concert", "tutorial",
            "sped up", "slowed")

def strict_candidate(candidate, artist, title, trusted_channels=()):
    if not candidate.get("channel_id") or candidate["channel_id"] not in trusted_channels:
        return False
    text = norm(candidate.get("title"))
    if not text or any(word in text for word in EXCLUDED):
        return False
    requested = norm(title).split()
    words = text.split()
    if not requested or not any(words[i:i + len(requested)] == requested
                                 for i in range(len(words) - len(requested) + 1)):
        return False
    official = any(x in text for x in ("official video", "official music video", "music video"))
    artist_channel = norm(artist) in norm(candidate.get("channel_title"))
    return official or artist_channel

def video_id(value):
    v = str(value or "").strip()
    return v if re.fullmatch(r"[a-zA-Z0-9_-]{11}", v) else ""

class Library:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = {"settings": {"spotify_entity": "", "cast_entity": "", "auto": False, "source": "spotify"},
                     "matches": {}, "trusted_channels": {}}
        if self.path.is_file():
            loaded = json.loads(self.path.read_text(encoding="utf8"))
            for key in self.data:
                if isinstance(loaded.get(key), dict):
                    self.data[key].update(loaded[key])

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(self.data, indent=2), encoding="utf8")
        os.replace(temp, self.path)

    def settings(self):
        with self.lock:
            return dict(self.data["settings"])

    def configure(self, changes):
        with self.lock:
            for key in ("spotify_entity", "cast_entity"):
                if key in changes and not re.fullmatch(r"media_player\.[a-z0-9_]+|", str(changes[key])):
                    raise ValueError("Invalid media_player entity")
            if "source" in changes and changes["source"] not in ("spotify", "external"):
                raise ValueError("Invalid input source")
            if "auto" in changes and not isinstance(changes["auto"], bool):
                raise ValueError("Auto must be true or false")
            for key in ("spotify_entity", "cast_entity", "auto", "source"):
                if key in changes:
                    self.data["settings"][key] = changes[key]
            self.save()
            return self.settings()

    def match(self, artist, title):
        with self.lock:
            v = self.data["matches"].get(song_key(artist, title))
            return dict(v) if v else None

    def approve(self, artist, title, candidate, trust=False, automatic=False):
        vid = video_id(candidate.get("video_id"))
        if not vid:
            raise ValueError("Invalid YouTube ID")
        with self.lock:
            if automatic and not strict_candidate(candidate, artist, title, self.trusted(artist)):
                raise ValueError("Candidate is not from a trusted artist channel")
            previous = self.data["matches"].get(song_key(artist, title), {})
            retained = previous.get("offset_seconds", 0) if previous.get("video_id") == vid else 0
            record = {"video_id": vid, "offset_seconds": retained,
                      "title": str(candidate.get("title", ""))[:200],
                      "channel_id": str(candidate.get("channel_id", ""))[:100],
                      "channel_title": str(candidate.get("channel_title", ""))[:200],
                      "artist": artist, "song": title, "manually_approved": not automatic}
            self.data["matches"][song_key(artist, title)] = record
            if trust and record["channel_id"].startswith("UC"):
                channels = self.data["trusted_channels"].setdefault(norm(artist), [])
                if record["channel_id"] not in channels:
                    channels.append(record["channel_id"])
            self.save()
            return record

    def trusted(self, artist):
        with self.lock:
            return list(self.data["trusted_channels"].get(norm(artist), []))

    def forget(self, artist, title):
        with self.lock:
            self.data["matches"].pop(song_key(artist, title), None)
            self.save()

    def set_offset(self, artist, title, offset):
        try:
            offset = float(offset)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("Offset must be a number")
        if not math.isfinite(offset) or offset < -120 or offset > 120:
            raise ValueError("Offset must be between -120 and +120 seconds")
        with self.lock:
            key = song_key(artist, title)
            if key not in self.data["matches"]:
                raise ValueError("No approved video to adjust")
            self.data["matches"][key]["offset_seconds"] = round(offset, 2)
            self.save()
            return dict(self.data["matches"][key])
