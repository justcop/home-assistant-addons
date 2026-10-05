"""Reasoning helpers for track identity, boundaries and cautious scrobbling.

Shazam answers are observations about an audio window, not authoritative track
boundaries.  This module keeps those concepts separate so the live engine can
combine fingerprint agreement, physical pauses and expected track timing.
"""

import re
import threading
from copy import deepcopy
from threading import RLock

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
BOUNDARY_STAGES = (3, 5, 10)
VERIFICATION_WINDOWS = (
    ("verify_20", 10.0, 20.0),
    ("verify_30", 20.0, 30.0),
)
EXPECTED_END_TOLERANCE = 10.0
PENDING_SCROBBLE_TTL = 180.0
UNKNOWN_DURATION_RECHECK_SECONDS = 30.0
UNKNOWN_DURATION_SCROBBLE_SECONDS = 120.0
LASTFM_MIN_TRACK_SECONDS = 30.0


def _normalise(value):
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def identity_key(value):
    """Stable-enough identity for comparing Shazam results.

    Compare artist/title across catalogue releases. Strip only remaster
    suffixes; live, remix and medley titles remain distinct. Catalogue ids
    are a fallback when usable text is absent, not evidence of a boundary.
    """
    if not isinstance(value, dict):
        return None
    title = str(value.get("title") or "").strip()
    title = re.sub(
        r"\s*(?:\((?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?\)"
        r"|\[(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?\]"
        r"|[-–:]\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?)\s*$",
        "", title, flags=re.IGNORECASE,
    )
    title = _normalise(title)
    artist = _normalise(value.get("artist"))
    if title and artist:
        return f"text:{artist}|{title}"
    adamid = value.get("adamid")
    if adamid not in (None, ""):
        return f"adamid:{adamid}"
    shazam_key = value.get("shazam_key")
    if shazam_key not in (None, ""):
        return f"shazam:{shazam_key}"
    if not title and not artist:
        return None
    return f"text:{artist}|{title}"


def confidence_at_least(value, minimum):
    return CONFIDENCE_RANK.get(str(value), -1) >= CONFIDENCE_RANK.get(str(minimum), 99)


def scrobble_threshold_seconds(track):
    if not isinstance(track, dict):
        return UNKNOWN_DURATION_SCROBBLE_SECONDS
    duration = float(track.get("duration") or 0.0)
    if track.get("duration_known") and duration > 0:
        return min(duration / 2.0, 240.0)
    return UNKNOWN_DURATION_SCROBBLE_SECONDS


def expected_end(track):
    if not isinstance(track, dict):
        return None
    value = track.get("expected_end_time")
    if value is not None:
        return float(value)
    if not track.get("duration_known"):
        return None
    duration = float(track.get("duration") or 0.0)
    start = track.get("start_timestamp")
    if start is None or duration <= 0:
        return None
    return float(start) + duration


def scrobble_identity_confident(track):
    if not isinstance(track, dict):
        return False
    if track.get("identity_context_conflict"):
        return False
    return bool(
        confidence_at_least(track.get("recognition_confidence"), "high")
        or track.get("recognition_verified")
        or track.get("boundary_confirmed")
    )


class AlbumIdentityGuard:
    """Hold suspicious artist substitutions, without rewriting Shazam metadata.

    Two repeated fingerprints can repeat the same catalogue error. Recent
    album evidence is a veto for an unsupported substitution, not permission
    to invent a corrected artist. Context expires and catalogue-backed artist
    changes remain allowed (including compilation albums).
    """

    def __init__(self, max_age=720.0):
        self.max_age = float(max_age)
        self.tracks = []
        self.lock = RLock()

    def conflict(self, candidate, now):
        artist = _normalise(candidate.get("artist"))
        if not artist:
            return None
        with self.lock:
            recent = [row for row in self.tracks
                      if 0 <= float(now) - row["observed_at"] <= self.max_age]
            if not recent:
                return None
            previous = recent[-1]["track"]
            expected = previous.get("expected_next") or {}
            candidate_title = identity_key({**candidate, "artist": "context"})
            expected_title = identity_key({**expected, "artist": "context"})
            if (expected.get("title") and expected.get("artist")
                    and candidate_title == expected_title
                    and artist != _normalise(expected["artist"])):
                return {"reason": "Artist conflicts with the expected album track",
                        "expected_artist": expected["artist"],
                        "expected_title": expected["title"]}
            album = _normalise(candidate.get("album"))
            lacks_catalogue = (album in ("", "unknown")
                               and not candidate.get("album_adamid"))
            if len(recent) >= 2 and lacks_catalogue:
                first, last = recent[-2]["track"], previous
                if (_normalise(first.get("artist")) == _normalise(last.get("artist"))
                        and _normalise(first.get("album")) == _normalise(last.get("album"))
                        and artist != _normalise(last.get("artist"))):
                    return {"reason": "Unsupported artist change during established album playback",
                            "expected_artist": last["artist"],
                            "album": last["album"]}
        return None

    def observe(self, track):
        if (not isinstance(track, dict) or not scrobble_identity_confident(track)
                or track.get("recognition_conflicts", 0)
                or _normalise(track.get("artist")) in ("", "unknown")
                or _normalise(track.get("album")) in ("", "unknown")):
            return
        stamp = float(track.get("session_start_time") or track.get("start_timestamp") or 0)
        key = (identity_key(track), stamp)
        with self.lock:
            # Metadata updates refresh the same observation, never count as a
            # second independently recognised song or revive expired context.
            for row in self.tracks:
                if row["key"] == key:
                    row["track"] = deepcopy(track)
                    return
            if self.tracks and stamp < self.tracks[-1]["observed_at"]:
                return
            self.tracks.append({"key": key, "observed_at": stamp, "track": deepcopy(track)})
            self.tracks = self.tracks[-2:]


def scrobble_is_eligible(track, physical_now, completed=False):
    if not isinstance(track, dict):
        return False
    duration = float(track.get("duration") or 0.0)
    if track.get("duration_known"):
        if duration <= LASTFM_MIN_TRACK_SECONDS:
            return False
        trigger = track.get("scrobble_trigger_time")
        return trigger is not None and float(physical_now) >= float(trigger)

    if completed:
        session_start = float(
            track.get("session_start_time")
            or track.get("start_timestamp")
            or physical_now
        )
        inferred_track_start = float(track.get("start_timestamp") or session_start)
        current_segment = max(0.0, float(physical_now) - session_start)
        previously_played = float(track.get("previously_played") or 0.0)
        played = current_segment + previously_played
        starting_offset = max(0.0, session_start - inferred_track_start)
        estimated_duration = max(played, starting_offset + current_segment)
        if estimated_duration <= LASTFM_MIN_TRACK_SECONDS:
            return False
        required = min(estimated_duration / 2.0, 240.0)
        return played >= required

    trigger = track.get("scrobble_trigger_time")
    if trigger is None or float(physical_now) < float(trigger):
        return False
    confirmed_at = track.get("duration_recheck_confirmed_at")
    return bool(
        confirmed_at is not None
        and float(confirmed_at) >= float(trigger)
        and not track.get("duration_recheck_pending")
    )


class TrackMonitor:
    """Schedule and combine post-identification recognition evidence."""

    def __init__(self):
        self.generation = 0
        self.track_key = None
        self.track_origin = None
        self.expected_next_key = None
        self.verification_requested = set()
        self.verification_results = {}
        self.boundary = None
        self.request_sequence = 0
        self.requests = {}
        self.recovery_check_after = 0.0
        self.periodic_requested_at = None

    def begin_track(self, track):
        self.generation += 1
        self.track_key = identity_key(track)
        self.track_origin = float(track.get("session_start_time") or track.get("start_timestamp") or 0.0)
        self.expected_next_key = identity_key(track.get("expected_next"))
        self.verification_requested.clear()
        self.verification_results.clear()
        self.boundary = None
        self.requests.clear()
        self.recovery_check_after = 0.0
        self.periodic_requested_at = self.track_origin

    def clear(self):
        self.generation += 1
        self.track_key = None
        self.track_origin = None
        self.expected_next_key = None
        self.verification_requested.clear()
        self.verification_results.clear()
        self.boundary = None
        self.requests.clear()
        self.recovery_check_after = 0.0
        self.periodic_requested_at = None

    def boundary_active(self):
        return self.boundary is not None

    def update_track_metadata(self, track):
        """Refresh optional catalogue hints without resetting active evidence."""
        self.expected_next_key = identity_key((track or {}).get("expected_next"))

    def start_boundary(self, anchor, reason, strength="medium", previous_end=None):
        anchor = float(anchor)
        if reason == "music_recovery" and strength == "medium" and anchor < self.recovery_check_after:
            return False
        if self.boundary is not None:
            # Preserve the first active search unless the new evidence points to
            # a materially earlier boundary.
            if anchor >= float(self.boundary["anchor"]) - 1.0:
                return False
        self.boundary = {
            "anchor": anchor,
            "reason": str(reason),
            "strength": str(strength),
            "previous_end": float(previous_end) if previous_end is not None else anchor,
            "requested": set(),
            "results": {},
            "generation": self.generation,
        }
        return True

    def cancel_boundary(self):
        self.boundary = None

    def _request(self, kind, name, start, end, **extra):
        self.request_sequence += 1
        request_id = f"{self.generation}:{self.request_sequence}"
        spec = {
            "id": request_id,
            "generation": self.generation,
            "kind": kind,
            "name": name,
            "start": float(start),
            "end": float(end),
        }
        spec.update(extra)
        self.requests[request_id] = spec
        return spec

    def due_requests(self, now, track):
        """Return recognition windows due now.

        Weak initial identities get two non-overlapping ten-second verification
        windows (10-20 and 20-30 seconds).  Those windows are skipped if they
        would overlap an expected track end, where boundary reasoning is safer.
        """
        now = float(now)
        due = []

        if self.boundary is not None:
            boundary = self.boundary
            anchor = float(boundary["anchor"])
            for stage in BOUNDARY_STAGES:
                if stage in boundary["requested"] or now < anchor + stage:
                    continue
                boundary["requested"].add(stage)
                due.append(self._request(
                    "boundary",
                    f"boundary_{stage}",
                    anchor + 5.0 if stage == 10 and boundary["reason"] == "music_recovery" else anchor,
                    anchor + stage,
                    stage=stage,
                    anchor=anchor,
                    reason=boundary["reason"],
                    strength=boundary["strength"],
                    previous_end=boundary["previous_end"],
                ))
            return due

        if not track.get("duration_known", True):
            origin = self.track_origin if self.track_origin is not None else now
            last = self.periodic_requested_at if self.periodic_requested_at is not None else origin
            if now >= last + UNKNOWN_DURATION_RECHECK_SECONDS and not any(
                request["kind"] == "periodic" for request in self.requests.values()
            ):
                # Always sample the latest audio, never replay a backlog after
                # a delayed API call or an intervening physical boundary.
                self.periodic_requested_at = now
                due.append(self._request("periodic", "unknown_duration_recheck", now - 10.0, now))

        identity_held = bool(track.get("identity_context_conflict"))
        if (
            confidence_at_least(track.get("recognition_confidence"), "high")
            and not identity_held
        ):
            return due

        origin = float(track.get("session_start_time") or self.track_origin or now)
        end_hint = expected_end(track)

        if (
            not confidence_at_least(track.get("recognition_confidence"), "high")
            or identity_held
        ):
            for name, start_offset, end_offset in VERIFICATION_WINDOWS:
                if name in self.verification_requested:
                    continue
                start = origin + start_offset
                end = origin + end_offset
                # Never call a straddling window a correction sample when metadata
                # says a real boundary may occur inside it.
                if end_hint is not None and end_hint <= end + 3.0:
                    self.verification_requested.add(name)
                    continue
                if now < end:
                    continue
                self.verification_requested.add(name)
                due.append(self._request("verification", name, start, end))
            if due:
                return due

        return due

    def record_result(self, request_id, match):
        spec = self.requests.pop(request_id, None)
        if spec is None or spec.get("generation") != self.generation:
            return {"accepted": False, "action": None}
        key = identity_key(match)
        if spec["kind"] == "periodic":
            same = bool(key and key == self.track_key)
            if key and not same and self.boundary is None:
                # Confirm a changed identity with a separate future window.
                # Timing is approximate without a physical gap or duration.
                self.start_boundary(spec["end"], "periodic_identity", strength="medium")
                self.boundary["results"][0] = {"key": key, "match": deepcopy(match)}
            return {"accepted": True, "action": "duration_rechecked", "same": same,
                    "match": deepcopy(match) if match else None, "request": spec}

        if spec["kind"] == "verification":
            self.verification_results[spec["name"]] = {
                "key": key,
                "match": deepcopy(match) if match else None,
            }
            if key and key == self.track_key:
                return {
                    "accepted": True,
                    "action": "verified",
                    "confidence": "high",
                    "match": deepcopy(match),
                    "request": spec,
                }

            alternatives = {}
            for row in self.verification_results.values():
                alt_key = row.get("key")
                if alt_key and alt_key != self.track_key:
                    alternatives.setdefault(alt_key, []).append(row)
            winner_key = max(alternatives, key=lambda item: len(alternatives[item]), default=None)
            if winner_key and len(alternatives[winner_key]) >= 2:
                return {
                    "accepted": True,
                    "action": "correct_identity",
                    "confidence": "high",
                    "match": deepcopy(alternatives[winner_key][-1]["match"]),
                    "request": spec,
                }
            return {
                "accepted": True,
                "action": "hold_ambiguous" if key else None,
                "match": deepcopy(match) if match else None,
                "request": spec,
            }

        boundary = self.boundary
        if boundary is None or float(spec.get("anchor")) != float(boundary["anchor"]):
            return {"accepted": False, "action": None}
        stage = int(spec["stage"])
        boundary["results"][stage] = {
            "key": key,
            "match": deepcopy(match) if match else None,
        }
        if boundary["reason"] == "music_recovery":
            # Frequent brief rests should not start overlapping searches.
            # Expected-end checks and strong physical boundaries remain free
            # to bypass this short recovery-only cooldown.
            self.recovery_check_after = max(self.recovery_check_after, spec["end"] + 15.0)

        same = [row for row in boundary["results"].values() if row.get("key") == self.track_key]
        alternatives = {}
        for row in boundary["results"].values():
            alt_key = row.get("key")
            if alt_key and alt_key != self.track_key:
                alternatives.setdefault(alt_key, []).append(row)
        winner_key = max(alternatives, key=lambda item: len(alternatives[item]), default=None)

        expected_rows = alternatives.get(self.expected_next_key, []) if self.expected_next_key else []
        if (
            expected_rows
            and stage >= 5
            and boundary["strength"] in ("strong", "high")
            and (not same or len(expected_rows) >= 2)
        ):
            action = {
                "accepted": True,
                "action": "successor",
                "confidence": (
                    "high"
                    if len(expected_rows) >= 2 and not same
                    else "medium"
                ),
                "match": deepcopy(expected_rows[-1]["match"]),
                "anchor": boundary["anchor"],
                "reason": boundary["reason"],
                "strength": boundary["strength"],
                "previous_end": boundary["previous_end"],
                "expected_next_match": True,
                "request": spec,
            }
            self.boundary = None
            return action

        recovery_needs_fresh = (
            boundary["reason"] == "music_recovery"
            and boundary["strength"] == "medium"
        )
        fresh_winner = boundary["results"].get(10, {}).get("key") == winner_key
        if winner_key and len(alternatives[winner_key]) >= 2 and (not recovery_needs_fresh or fresh_winner):
            action = {
                "accepted": True,
                "action": "successor",
                "confidence": "high" if not same else "medium",
                "match": deepcopy(alternatives[winner_key][-1]["match"]),
                "anchor": boundary["anchor"],
                "reason": boundary["reason"],
                "strength": boundary["strength"],
                "previous_end": boundary["previous_end"],
                "request": spec,
            }
            self.boundary = None
            return action

        if len(same) >= 2:
            action = {
                "accepted": True,
                "action": "continuation",
                "confidence": "high",
                "anchor": boundary["anchor"],
                "reason": boundary["reason"],
                "request": spec,
            }
            self.boundary = None
            return action

        requested = boundary["requested"]
        if 10 in requested and {3, 5, 10}.issubset(boundary["results"].keys()):
            if same:
                action = {
                    "accepted": True,
                    "action": "continuation",
                    "confidence": "medium",
                    "anchor": boundary["anchor"],
                    "reason": boundary["reason"],
                    "request": spec,
                }
                self.boundary = None
                return action
            if (
                winner_key
                and len(alternatives) == 1
                and boundary["strength"] in ("strong", "high")
            ):
                action = {
                    "accepted": True,
                    "action": "successor",
                    "confidence": "medium",
                    "match": deepcopy(alternatives[winner_key][-1]["match"]),
                    "anchor": boundary["anchor"],
                    "reason": boundary["reason"],
                    "strength": boundary["strength"],
                    "previous_end": boundary["previous_end"],
                    "request": spec,
                }
                self.boundary = None
                return action

            action = {
                "accepted": True,
                "action": "boundary_unresolved",
                "anchor": boundary["anchor"],
                "reason": boundary["reason"],
                "request": spec,
            }
            self.boundary = None
            return action

        return {
            "accepted": True,
            "action": None,
            "match": deepcopy(match) if match else None,
            "request": spec,
        }


class PendingScrobbleQueue:
    """Keep eligible-but-ambiguous completed tracks briefly for later evidence."""

    def __init__(self, ttl=PENDING_SCROBBLE_TTL):
        self.ttl = float(ttl)
        self.items = []
        self._lock = threading.RLock()

    def _expire_unlocked(self, now):
        now = float(now)
        self.items = [item for item in self.items if now <= item["expires"]]

    def expire(self, now):
        with self._lock:
            self._expire_unlocked(now)

    def hold(self, track, ended_at, physical_now, reason, completed=False):
        with self._lock:
            self._expire_unlocked(ended_at)
            if not track or track.get("scrobble_fired"):
                return False
            if not scrobble_is_eligible(track, physical_now, completed=completed):
                return False
            key = identity_key(track)
            self.items = [item for item in self.items if item["key"] != key]
            self.items.append({
                "key": key,
                "track": deepcopy(track),
                "ended_at": float(ended_at),
                "expires": float(ended_at) + self.ttl,
                "reason": str(reason),
            })
            return True

    def resolve_with_successor(self, successor, boundary_time, strong_boundary=False):
        with self._lock:
            self._expire_unlocked(boundary_time)
            successor_key = identity_key(successor)
            resolved = []
            kept = []
            for item in self.items:
                track = item["track"]
                if item["key"] == successor_key:
                    # Same identity after a pause is a resume, not a completed song.
                    continue
                end_hint = expected_end(track)
                timing_fit = (
                    end_hint is not None
                    and abs(float(boundary_time) - float(end_hint)) <= EXPECTED_END_TOLERANCE
                )
                confidence_ok = confidence_at_least(track.get("recognition_confidence"), "medium")
                if strong_boundary and (confidence_ok or timing_fit):
                    track["boundary_confirmed"] = True
                    resolved.append(track)
                elif timing_fit:
                    track["boundary_confirmed"] = True
                    resolved.append(track)
                else:
                    kept.append(item)
            self.items = kept
            return resolved
