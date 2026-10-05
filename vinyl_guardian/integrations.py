import time
import asyncio
import inspect
import math
import json
import threading
import re
import requests
import urllib.parse
import pylast
from shazamio import Shazam
from config import *
from track_reasoning import identity_key


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
_duration_cache = {}
_musicbrainz_lock = threading.Lock()
_musicbrainz_next_call = 0.0
_musicbrainz_backoff_until = 0.0


def _duration_from_rows(rows, title, artist, album=None):
    wanted = identity_key({"title": title, "artist": artist})
    matches = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("kind", "song") != "song":
            continue
        actual = identity_key({"title": row.get("trackName"), "artist": row.get("artistName")})
        if actual != wanted:
            continue
        try:
            seconds = float(row.get("trackTimeMillis") or 0) / 1000.0
        except (TypeError, ValueError):
            continue
        if math.isfinite(seconds) and seconds > 0:
            matches.append((row, seconds))
    if album and album != "Unknown":
        album_key = identity_key({"title": album, "artist": "album"})
        preferred = [item for item in matches if identity_key(
            {"title": item[0].get("collectionName"), "artist": "album"}) == album_key]
        if preferred:
            matches = preferred
    if not matches:
        return 0.0
    durations = [seconds for row, seconds in matches]
    # Do not guess between substantially different recordings of one song.
    if max(durations) - min(durations) > 5.0:
        return 0.0
    return durations[0]


def _lastfm_track_duration(title, artist, album=None):
    if not LFM_KEY:
        return 0.0
    try:
        response = requests.get("https://ws.audioscrobbler.com/2.0/", params={
            "method": "track.getInfo", "api_key": LFM_KEY, "artist": artist,
            "track": title, "autocorrect": 0, "format": "json",
        }, timeout=5)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("error"):
            log("🔎 Duration Last.fm: no usable track metadata.")
            return 0.0
        track = payload.get("track")
        if not isinstance(track, dict):
            return 0.0
        credit = track.get("artist") or {}
        name = credit.get("name") if isinstance(credit, dict) else credit
        release = track.get("album") or {}
        release_name = release.get("title") if isinstance(release, dict) else ""
        return _duration_from_rows([{
            "trackName": track.get("name"), "artistName": name,
            "trackTimeMillis": track.get("duration"), "collectionName": release_name,
        }], title, artist, album)
    except (requests.RequestException, ValueError, TypeError) as error:
        # Never print request URLs or exceptions containing the API key.
        log(f"⚠️ Duration Last.fm failed: {type(error).__name__}.")
        return 0.0


def _musicbrainz_duration_rows(payload):
    rows = []
    if not isinstance(payload, dict):
        return rows
    for recording in payload.get("recordings") or []:
        if not isinstance(recording, dict):
            continue
        try:
            if int(recording.get("score") or 0) < 95:
                continue
        except (TypeError, ValueError):
            continue
        credits = recording.get("artist-credit") or []
        artist = "".join(str(credit.get("name") or (credit.get("artist") if isinstance(credit.get("artist"), dict) else {}).get("name") or "")
                         + str(credit.get("joinphrase") or "")
                         for credit in credits if isinstance(credit, dict))
        title = str(recording.get("title") or "")
        description = str(recording.get("disambiguation") or "").lower()
        descriptors = set(re.findall(r"\b(?:live|remix|demo|karaoke|instrumental)\b", description))
        if any(word not in title.lower() for word in descriptors):
            # Descriptors often live outside the title. Do not silently use a
            # live recording's length for an otherwise identical studio title.
            continue
        base = {"trackName": title, "artistName": artist,
                "trackTimeMillis": recording.get("length")}
        releases = [item for item in recording.get("releases") or [] if isinstance(item, dict)]
        if not releases:
            rows.append(base)
        for release in releases:
            release_row = dict(base, collectionName=release.get("title"))
            tracks = [track for medium in release.get("media") or [] if isinstance(medium, dict)
                      for track in medium.get("track") or [] if isinstance(track, dict)]
            if not tracks:
                rows.append(release_row)
            for track in tracks:
                rows.append(dict(release_row, trackName=track.get("title") or title,
                                 trackTimeMillis=track.get("length") or recording.get("length")))
    return rows


def _musicbrainz_track_duration(title, artist, album=None):
    global _musicbrainz_next_call, _musicbrainz_backoff_until
    with _musicbrainz_lock:
        now = time.monotonic()
        if now < _musicbrainz_backoff_until:
            return 0.0
        wait = max(0.0, _musicbrainz_next_call - now)
        if wait:
            time.sleep(wait)
        _musicbrainz_next_call = time.monotonic() + 1.1
    try:
        # Escape the two literal phrases before passing them to Lucene.
        def phrase(value):
            return json.dumps(str(value), ensure_ascii=False)
        query = f"recording:{phrase(title)} AND artist:{phrase(artist)}"
        response = requests.get("https://musicbrainz.org/ws/2/recording/", params={
            "query": query, "fmt": "json", "limit": 25,
        }, headers={"User-Agent": "VinylGuardian/1.0 (https://github.com/justcop/home-assistant-addons)"}, timeout=5)
        if response.status_code == 429:
            with _musicbrainz_lock:
                _musicbrainz_backoff_until = time.monotonic() + 60.0
            log("🔎 Duration MusicBrainz: rate limited; pausing catalogue requests for one minute.")
            return 0.0
        response.raise_for_status()
        return _duration_from_rows(_musicbrainz_duration_rows(response.json()), title, artist, album)
    except (requests.RequestException, ValueError, TypeError) as error:
        log(f"⚠️ Duration MusicBrainz failed: {type(error).__name__}.")
        return 0.0


def get_track_duration(title, artist, adamid=None, album=None):
    cache_key = (identity_key({"title": title, "artist": artist}), str(adamid or ""), str(album or ""))
    if cache_key in _duration_cache:
        return _duration_cache[cache_key]
    lookups = []
    if adamid:
        lookups.extend(("lookup", {"id": adamid, "country": country}) for country in ("GB", "US"))
    lookups.extend(("search", {"term": f"{title} {artist}", "entity": "song",
                              "limit": 25, "country": country}) for country in ("GB", "US"))
    for endpoint, params in lookups:
        label = f"{endpoint} {params['country']}"
        try:
            response = requests.get(f"https://itunes.apple.com/{endpoint}", params=params, timeout=5)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Invalid catalogue response")
            duration = _duration_from_rows(data.get("results"), title, artist, album)
            if duration > 0:
                if len(_duration_cache) >= 256:
                    _duration_cache.pop(next(iter(_duration_cache)), None)
                _duration_cache[cache_key] = duration
                log(f"⏱️ Duration: {title} - {artist}: {duration:.1f}s ({label}).")
                return duration
            log(f"🔎 Duration {label}: no unambiguous matching song with a usable length for {title} - {artist}.")
        except (requests.RequestException, ValueError, TypeError) as error:
            log(f"⚠️ Duration {label} failed for {title} - {artist}: {type(error).__name__}.")
    for provider, lookup in (("Last.fm", _lastfm_track_duration), ("MusicBrainz", _musicbrainz_track_duration)):
        duration = lookup(title, artist, album)
        if duration > 0:
            if len(_duration_cache) >= 256:
                _duration_cache.pop(next(iter(_duration_cache)), None)
            _duration_cache[cache_key] = duration
            log(f"⏱️ Duration: {title} - {artist}: {duration:.1f}s ({provider}).")
            return duration
        log(f"🔎 Duration {provider}: no unambiguous matching song with a usable length for {title} - {artist}.")
    log(f"⏱️ Duration unavailable for {title} - {artist}; using periodic Shazam checks.")
    return 0.0

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
