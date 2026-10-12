"""Candidate discovery and explicit official-channel approval."""
from model import song_key, strict_candidate, vevo_candidate, official_channel_candidate
from providers import youtube_search

def search(self):
    with self.lock:
        track = dict(self.current)
        if not track["artist"]:
            raise ValueError("Play a Spotify song first")
        found = youtube_search(self.options.get("youtube_api_key", ""), track["artist"], track["title"])
        trusted = self.library.trusted(track["artist"])
        for item in found:
            item["trusted"] = item["channel_id"] in trusted
            item["strict_match"] = strict_candidate(item, track["artist"], track["title"], trusted)
            item["vevo_match"] = vevo_candidate(item, track["artist"], track["title"])
            item["official_match"] = official_channel_candidate(item, track["artist"], track["title"])
        self.candidates = found
        self.candidate_song = song_key(track["artist"], track["title"])
        self.info = f"Found {len(found)} candidates. Verify the official uploader before approval."
        return found

def approve(self, data):
    with self.lock:
        track = dict(self.current)
        if not track["artist"]:
            raise ValueError("No current song")
        vid = str(data.get("video_id") or "")
        if not data.get("verified"):
            raise ValueError("You must confirm this is an official upload")
        if data.get("manual"):
            candidate = {"video_id": vid, "title": track["title"],
                         "channel_id": "", "channel_title": "Manually verified"}
            trust = False
        else:
            if self.candidate_song != song_key(track["artist"], track["title"]):
                raise ValueError("Search results refer to a different song")
            candidate = next((x for x in self.candidates if x["video_id"] == vid), None)
            if candidate is None:
                raise ValueError("Only search results can be approved")
            trust = bool(data.get("trust_channel"))
        result = self.library.approve(track["artist"], track["title"], candidate, trust=trust)
        self.info = "Approved official video saved for this song."
        return result
