import time
import asyncio
import inspect
import requests
import urllib.parse
import pylast
from shazamio import Shazam
from config import *


# --- LAST.FM SETUP ---
lastfm_network = None
if not CALIBRATION_MODE and LFM_USER and LFM_PASS and LFM_KEY and LFM_SECRET:
    try:
        lastfm_network = pylast.LastFMNetwork(
            api_key=LFM_KEY,
            api_secret=LFM_SECRET,
            username=LFM_USER,
            password_hash=pylast.md5(LFM_PASS)
        )
        log("✅ Last.fm integration initialized.")
    except Exception as e:
        log(f"🚨 Last.fm initialization failed: {e}")

def scrobble_to_lastfm(artist, title, start_timestamp, album=None):
    if not lastfm_network:
        return
    try:
        kwargs = {"artist": artist, "title": title, "timestamp": start_timestamp}
        if album and album != "Unknown":
            kwargs["album"] = album
        lastfm_network.scrobble(**kwargs)
        log(f"🎵 Successfully scrobbled to Last.fm: {title} by {artist}")
    except Exception as e:
        log(f"🚨 Last.fm Scrobble Failed: {e}")

# --- HELPER: GET TRACK DURATION ---
def get_track_duration(title, artist, adamid=None):
    for attempt in range(2):
        try:
            if adamid:
                url = f"https://itunes.apple.com/lookup?id={adamid}"
            else:
                query = urllib.parse.quote(f"{title} {artist}")
                url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=1"
            res = requests.get(url, timeout=10)
            data = res.json()
            if data.get('resultCount', 0) > 0:
                return data['results'][0].get('trackTimeMillis', 0) / 1000.0
        except Exception:
            time.sleep(1)
    return 0

def _catalogue_identity(row):
    if not isinstance(row, dict):
        return ("", "")
    return (
        str(row.get("artistName") or "").strip().lower(),
        str(row.get("trackName") or "").strip().lower(),
    )


def _next_album_track_from_rows(rows, current_adamid=None, title="", artist=""):
    tracks = [
        row for row in (rows or [])
        if isinstance(row, dict)
        and row.get("wrapperType") == "track"
        and row.get("trackName")
    ]
    tracks.sort(key=lambda row: (
        int(row.get("discNumber") or 1),
        int(row.get("trackNumber") or 0),
    ))
    target_index = None
    if current_adamid not in (None, ""):
        current = str(current_adamid)
        for index, row in enumerate(tracks):
            if str(row.get("trackId") or "") == current:
                target_index = index
                break
    if target_index is None:
        wanted = (str(artist or "").strip().lower(), str(title or "").strip().lower())
        for index, row in enumerate(tracks):
            if _catalogue_identity(row) == wanted:
                target_index = index
                break
    if target_index is None or target_index + 1 >= len(tracks):
        return None
    row = tracks[target_index + 1]
    return {
        "title": row.get("trackName", ""),
        "artist": row.get("artistName", ""),
        "album": row.get("collectionName", ""),
        "adamid": row.get("trackId"),
        "duration": float(row.get("trackTimeMillis") or 0) / 1000.0,
        "track_number": row.get("trackNumber"),
        "disc_number": row.get("discNumber"),
    }


def get_expected_next_track(album_adamid, current_adamid=None, title="", artist=""):
    """Best-effort album-order hint; failure never affects live recognition."""
    if album_adamid in (None, ""):
        return None
    try:
        url = f"https://itunes.apple.com/lookup?id={album_adamid}&entity=song"
        res = requests.get(url, timeout=10)
        data = res.json()
        return _next_album_track_from_rows(
            data.get("results", []),
            current_adamid=current_adamid,
            title=title,
            artist=artist,
        )
    except Exception:
        return None


# --- RECOGNITION ENGINE (SHAZAM) ---
def recognize_shazam(wav_path):
    if DEBUG: log("Uploading to Shazam...")
    try:
        async def _recognize():
            # Each concurrent request owns its connection pool and event loop.
            recognizer = Shazam()
            try:
                return await asyncio.wait_for(recognizer.recognize(wav_path), timeout=45)
            finally:
                # Older deployed ShazamIO releases have no async context manager
                # or close method. Newer clients expose explicit pool cleanup.
                close = getattr(recognizer, 'close', None)
                if callable(close):
                    try:
                        result = close()
                        if inspect.isawaitable(result):
                            await result
                    except Exception as error:
                        log(f"⚠️ Shazam connection cleanup failed: {error}")
        res_json = asyncio.run(_recognize())
       
        if isinstance(res_json, dict) and 'track' in res_json and isinstance(res_json.get('matches'), list) and len(res_json['matches']) > 0:
            track = res_json['track']
            if not isinstance(track, dict): return None
            title = track.get('title', 'Unknown')
            artist = track.get('subtitle', 'Unknown')
            album = "Unknown"
            duration = 0
            release_year = "Unknown"
            adamid = track.get('trackadamid')
            album_adamid = track.get('albumadamid')
            shazam_key = track.get('key')
            image_url = track.get('images', {}).get('coverart', '')
           
            for section in track.get('sections', []):
                if isinstance(section, dict) and section.get('type') == 'SONG':
                    for meta in section.get('metadata', []):
                        if isinstance(meta, dict):
                            if meta.get('title') == 'Album':
                                album = meta.get('text')
                            elif meta.get('title') == 'Length':
                                p = meta.get('text', '').split(':')
                                if len(p) == 2:
                                    duration = int(p[0]) * 60 + int(p[1])
                                elif len(p) == 3:
                                    duration = int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2])
                            elif meta.get('title') == 'Released':
                                release_year = meta.get('text')
            
            return {
                "title": title, 
                "artist": artist, 
                "album": album, 
                "release_year": release_year, 
                "offset_seconds": res_json['matches'][0].get('offset', 0) if isinstance(res_json['matches'][0], dict) else 0, 
                "duration": duration, 
                "adamid": adamid,
                "album_adamid": album_adamid,
                "shazam_key": shazam_key,
                "image": image_url
            }
        return None
    except Exception as e:
        log(f"🚨 Shazam Error: {e}")
        return None