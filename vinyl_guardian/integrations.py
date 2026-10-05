import os
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
from source_reporting import SourceReporter, submit_scrobble


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

source_reporter = None
if config.get("listening_analytics_url") and config.get("listening_analytics_token"):
    try:
        source_reporter = SourceReporter(
            "/data/listening-source-pending.sqlite3",
            config["listening_analytics_url"], config["listening_analytics_token"],
            LFM_USER, log)
        source_reporter.start()
    except Exception as exc:
        log(f"Listening Analytics connection unavailable ({type(exc).__name__}). Check its URL and token settings.")



def lastfm_enabled():
    return lastfm_network is not None


def scrobble_to_lastfm(artist, title, start_timestamp, album=None):
    """Attempt one Last.fm delivery and report success to the retry queue."""
    if not lastfm_network:
        return False
    try:
        report = submit_scrobble(lastfm_network, artist, title, start_timestamp, album)
        if report is None:
            log(f"Last.fm ignored the scrobble: {title} by {artist}")
            return False
    except Exception as exc:
        log(f"Last.fm scrobble failed ({type(exc).__name__}).")
        return False
    log(f"🎵 Successfully scrobbled to Last.fm: {title} by {artist}")
    if source_reporter:
        try:
            source_reporter.enqueue(report)
        except Exception as exc:
            # Last.fm already accepted it. Never resubmit because reporting failed.
            log(f"Could not queue vinyl attribution ({type(exc).__name__}).")
    return True

# --- HELPER: GET TRACK DURATION ---
_duration_cache = {}
_musicbrainz_lock = threading.Lock()
_musicbrainz_next_call = 0.0
_musicbrainz_backoff_until = 0.0


def _duration_from_rows(rows, title, artist, album=None, provider=None):
    wanted = identity_key({"title": title, "artist": artist})
    matches = []
    rejected_identity = missing_length = invalid_rows = 0
    if not isinstance(rows, list):
        if provider:
            log(f"🔎 Duration {provider}: invalid or missing results list for {title} - {artist}.")
        return 0.0
    for row in rows:
        if not isinstance(row, dict) or row.get("kind", "song") != "song":
            invalid_rows += 1
            continue
        actual = identity_key({"title": row.get("trackName"), "artist": row.get("artistName")})
        if actual != wanted:
            rejected_identity += 1
            continue
        try:
            seconds = float(row.get("trackTimeMillis") or 0) / 1000.0
        except (TypeError, ValueError):
            missing_length += 1
            continue
        if math.isfinite(seconds) and seconds > 0:
            matches.append((row, seconds))
        else:
            missing_length += 1
    if album and album != "Unknown":
        album_key = identity_key({"title": album, "artist": "album"})
        preferred = [item for item in matches if identity_key(
            {"title": item[0].get("collectionName"), "artist": "album"}) == album_key]
        if preferred:
            matches = preferred
    if not matches:
        if provider:
            reason = ("no results returned" if not rows else
                      f"{len(rows)} results: {rejected_identity} artist/title mismatches, "
                      f"{missing_length} matching songs with missing, zero or invalid length, "
                      f"{invalid_rows} invalid or non-song results")
            log(f"🔎 Duration {provider}: {reason} for {title} - {artist}.")
        return 0.0
    durations = [seconds for row, seconds in matches]
    # Do not guess between substantially different recordings of one song.
    if max(durations) - min(durations) > 5.0:
        if provider:
            log(f"🔎 Duration {provider}: ambiguous matching recordings, lengths "
                f"{min(durations):.1f}s to {max(durations):.1f}s for {title} - {artist}; refusing to guess.")
        return 0.0
    return durations[0]


def _lastfm_track_duration(title, artist, album=None):
    if not LFM_KEY:
        log(f"🔎 Duration Last.fm skipped for {title} - {artist}: no API key configured.")
        return 0.0
    response = None
    try:
        response = requests.get("https://ws.audioscrobbler.com/2.0/", params={
            "method": "track.getInfo", "api_key": LFM_KEY, "artist": artist,
            "track": title, "autocorrect": 0, "format": "json",
        }, timeout=5)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            log(f"🔎 Duration Last.fm: invalid response for {title} - {artist}.")
            return 0.0
        if payload.get("error"):
            code = payload.get("error")
            code = code if isinstance(code, int) else "unspecified"
            log(f"🔎 Duration Last.fm: API error code {code} for {title} - {artist}.")
            return 0.0
        track = payload.get("track")
        if not isinstance(track, dict):
            log(f"🔎 Duration Last.fm: no track metadata returned for {title} - {artist}.")
            return 0.0
        credit = track.get("artist") or {}
        name = credit.get("name") if isinstance(credit, dict) else credit
        release = track.get("album") or {}
        release_name = release.get("title") if isinstance(release, dict) else ""
        return _duration_from_rows([{
            "trackName": track.get("name"), "artistName": name,
            "trackTimeMillis": track.get("duration"), "collectionName": release_name,
        }], title, artist, album, provider="Last.fm")
    except (requests.RequestException, ValueError, TypeError) as error:
        # Never print request URLs or exceptions containing the API key.
        status = getattr(response, "status_code", None)
        reason = f"HTTP {status}" if isinstance(status, int) and status >= 400 else type(error).__name__
        log(f"⚠️ Duration Last.fm failed for {title} - {artist}: {reason}.")
        return 0.0


def _musicbrainz_duration_rows(payload, diagnostics=None):
    rows = []
    stats = diagnostics if diagnostics is not None else {}
    stats.update(total=0, low_score=0, alternate_version=0)
    if not isinstance(payload, dict):
        return rows
    for recording in payload.get("recordings") or []:
        stats["total"] += 1
        if not isinstance(recording, dict):
            continue
        try:
            if int(recording.get("score") or 0) < 95:
                stats["low_score"] += 1
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
            stats["alternate_version"] += 1
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
            log(f"🔎 Duration MusicBrainz skipped for {title} - {artist}: rate-limit cooldown, "
                f"{_musicbrainz_backoff_until - now:.0f}s remaining.")
            return 0.0
        wait = max(0.0, _musicbrainz_next_call - now)
        if wait:
            time.sleep(wait)
        _musicbrainz_next_call = time.monotonic() + 1.1
    response = None
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
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("recordings"), list):
            log(f"🔎 Duration MusicBrainz: invalid or missing recordings list for {title} - {artist}.")
            return 0.0
        stats = {}
        rows = _musicbrainz_duration_rows(payload, stats)
        if not rows and stats["total"]:
            log(f"🔎 Duration MusicBrainz: {stats['total']} recordings rejected, "
                f"{stats['low_score']} below 95% search score, "
                f"{stats['alternate_version']} live/remix/demo or other alternate versions for {title} - {artist}.")
            return 0.0
        return _duration_from_rows(rows, title, artist, album, provider="MusicBrainz")
    except (requests.RequestException, ValueError, TypeError) as error:
        status = getattr(response, "status_code", None)
        reason = f"HTTP {status}" if isinstance(status, int) and status >= 400 else type(error).__name__
        log(f"⚠️ Duration MusicBrainz failed for {title} - {artist}: {reason}.")
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
        response = None
        try:
            response = requests.get(f"https://itunes.apple.com/{endpoint}", params=params, timeout=5)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Invalid catalogue response")
            duration = _duration_from_rows(data.get("results"), title, artist, album, provider=f"Apple {label}")
            if duration > 0:
                if len(_duration_cache) >= 256:
                    _duration_cache.pop(next(iter(_duration_cache)), None)
                _duration_cache[cache_key] = duration
                log(f"⏱️ Duration: {title} - {artist}: {duration:.1f}s ({label}).")
                return duration
        except (requests.RequestException, ValueError, TypeError) as error:
            status = getattr(response, "status_code", None)
            reason = f"HTTP {status}" if isinstance(status, int) and status >= 400 else type(error).__name__
            log(f"⚠️ Duration Apple {label} failed for {title} - {artist}: {reason}.")
    for provider, lookup in (("Last.fm", _lastfm_track_duration), ("MusicBrainz", _musicbrainz_track_duration)):
        duration = lookup(title, artist, album)
        if duration > 0:
            if len(_duration_cache) >= 256:
                _duration_cache.pop(next(iter(_duration_cache)), None)
            _duration_cache[cache_key] = duration
            log(f"⏱️ Duration: {title} - {artist}: {duration:.1f}s ({provider}).")
            return duration
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
    started = time.monotonic()
    try:
        try:
            wav_bytes = os.path.getsize(wav_path)
        except OSError:
            wav_bytes = 0

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
                        log(f"⚠️ Shazam connection cleanup failed: {type(error).__name__}")

        res_json = asyncio.run(_recognize())
        elapsed = time.monotonic() - started

        if not isinstance(res_json, dict):
            log(
                f"🔎 Shazam returned {type(res_json).__name__}, not a response object "
                f"after {elapsed:.2f}s ({wav_bytes} byte WAV)."
            )
            return None

        matches = res_json.get("matches")
        match_count = len(matches) if isinstance(matches, list) else -1
        track_present = isinstance(res_json.get("track"), dict)
        if match_count <= 0 or not track_present:
            keys = ",".join(sorted(str(key) for key in res_json.keys())[:12])
            log(
                f"🔎 Shazam response: matches={max(match_count, 0)}, "
                f"track={'yes' if track_present else 'no'}, {elapsed:.2f}s, "
                f"{wav_bytes} byte WAV, keys=[{keys}]."
            )
            return None

        if 'track' in res_json and isinstance(matches, list) and len(matches) > 0:
            track = res_json['track']
            if not isinstance(track, dict):
                log(f"🔎 Shazam track payload was {type(track).__name__}, expected object.")
                return None
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
