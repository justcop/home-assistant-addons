"""Reasoning helpers for track identity, boundaries and cautious scrobbling.

Shazam answers are observations about an audio window, not authoritative track
boundaries.  This module keeps those concepts separate so the live engine can
combine fingerprint agreement, physical pauses and expected track timing.
"""

import re
import threading
from copy import deepcopy

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
BOUNDARY_STAGES = (3, 5, 10)
VERIFICATION_WINDOWS = (
    ("verify_20", 10.0, 20.0),
    ("verify_30", 20.0, 30.0),
)
EXPECTED_END_TOLERANCE = 10.0
PENDING_SCROBBLE_TTL = 180.0
UNKNOWN_PROBE_WINDOW = 10.0
UNKNOWN_PROBE_INTERVAL = 10.0
UNKNOWN_FIRST_PROBE_END = 20.0
LASTFM_MIN_TRACK_SECONDS = 30.0


def _normalise(value):
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def identity_key(value):
    """Stable-enough identity for comparing Shazam results.

    Apple/Shazam ids are preferred when present.  Falling back to artist/title
    intentionally ignores album: the same recording can be returned with
    different catalogue releases and should not become a false boundary.
    """
    if not isinstance(value, dict):
        return None
    adamid = value.get("adamid")
    if adamid not in (None, ""):
        return f"adamid:{adamid}"
    shazam_key = value.get("shazam_key")
    if shazam_key not in (None, ""):
        return f"shazam:{shazam_key}"
    title = _normalise(value.get("title"))
    artist = _normalise(value.get("artist"))
    if not title and not artist:
        return None
    return f"text:{artist}|{title}"


def confidence_at_least(value, minimum):
    return CONFIDENCE_RANK.get(str(value), -1) >= CONFIDENCE_RANK.get(str(minimum), 99)


def scrobble_threshold_seconds(track):
    if not isinstance(track, dict):
        return 240.0
    duration = float(track.get("duration") or 0.0)
    if track.get("duration_known") and duration > 0:
        return min(duration / 2.0, 240.0)
    return 240.0


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
    return bool(
        confidence_at_least(track.get("recognition_confidence"), "high")
        or track.get("recognition_verified")
        or track.get("boundary_confirmed")
    )


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
        inferred_track_start = float(
            track.get("start_timestamp")
            or session_start
        )
        current_segment = max(0.0, float(physical_now) - session_start)
        previously_played = float(track.get("previously_played") or 0.0)
        played = current_segment + previously_played

        # Shazam's offset lets us estimate how far into the song the needle
        # was dropped even when catalogue duration is unavailable. Reaching the
        # end is therefore not treated as "100% played" if playback began in
        # the middle of the track.
        starting_offset = max(0.0, session_start - inferred_track_start)
        estimated_duration = max(
            played,
            starting_offset + current_segment,
        )
        if estimated_duration <= LASTFM_MIN_TRACK_SECONDS:
            return False
        required = min(estimated_duration / 2.0, 240.0)
        return played >= required

    trigger = track.get("scrobble_trigger_time")
    return trigger is not None and float(physical_now) >= float(trigger)


class TrackMonitor:
    """Schedule and combine post-identification recognition evidence."""

    def __init__(self):
        self.generation = 0
        self.track_key = None
        self.track_origin = None
        self.expected_next_key = None
        self.verification_requested = set()
        self.verification_results = {}
        self.unknown_probe_index = 0
        self.unknown_candidate_key = None
        self.unknown_candidate_match = None
        self.unknown_candidate_count = 0
        self.unknown_candidate_anchor = None
        self.boundary = None
        self.request_sequence = 0
        self.requests = {}

    def begin_track(self, track):
        self.generation += 1
        self.track_key = identity_key(track)
        self.track_origin = float(track.get("session_start_time") or track.get("start_timestamp") or 0.0)
        self.expected_next_key = identity_key(track.get("expected_next"))
        self.verification_requested.clear()
        self.verification_results.clear()
        self.unknown_probe_index = 0
        self.unknown_candidate_key = None
        self.unknown_candidate_match = None
        self.unknown_candidate_count = 0
        self.unknown_candidate_anchor = None
        self.boundary = None
        self.requests.clear()

    def clear(self):
        self.generation += 1
        self.track_key = None
        self.track_origin = None
        self.expected_next_key = None
        self.verification_requested.clear()
        self.verification_results.clear()
        self.unknown_probe_index = 0
        self.unknown_candidate_key = None
        self.unknown_candidate_match = None
        self.unknown_candidate_count = 0
        self.unknown_candidate_anchor = None
        self.boundary = None
        self.requests.clear()

    def boundary_active(self):
        return self.boundary is not None

    def update_track_metadata(self, track):
        """Refresh optional catalogue hints without resetting active evidence."""
        self.expected_next_key = identity_key((track or {}).get("expected_next"))

    def start_boundary(self, anchor, reason, strength="medium", previous_end=None):
        anchor = float(anchor)
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
                    anchor,
                    anchor + stage,
                    stage=stage,
                    anchor=anchor,
                    reason=boundary["reason"],
                    strength=boundary["strength"],
                    previous_end=boundary["previous_end"],
                ))
            return due

        origin = float(track.get("session_start_time") or self.track_origin or now)
        end_hint = expected_end(track)

        if not confidence_at_least(track.get("recognition_confidence"), "high"):
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

        # If metadata cannot tell us when a track ends, use sparse rolling
        # fingerprint probes. Two consecutive matching alternate identities are
        # required before a gapless successor is declared, so one odd Shazam
        # result (including mashup material) cannot rewrite a confirmed track.
        if not track.get("duration_known"):
            outstanding_probe = any(
                request.get("kind") == "unknown_probe"
                for request in self.requests.values()
            )
            if outstanding_probe:
                return due

            first_end = origin + UNKNOWN_FIRST_PROBE_END
            if not confidence_at_least(track.get("recognition_confidence"), "high"):
                first_end = max(first_end, origin + 40.0)
            next_end = first_end + (self.unknown_probe_index * UNKNOWN_PROBE_INTERVAL)
            if now >= next_end:
                self.unknown_probe_index += 1
                due.append(self._request(
                    "unknown_probe",
                    f"unknown_probe_{self.unknown_probe_index}",
                    next_end - UNKNOWN_PROBE_WINDOW,
                    next_end,
                    stage=10,
                    anchor=next_end - UNKNOWN_PROBE_WINDOW,
                ))
        return due

    def record_result(self, request_id, match):
        spec = self.requests.pop(request_id, None)
        if spec is None or spec.get("generation") != self.generation:
            return {"accepted": False, "action": None}
        key = identity_key(match)

        if spec["kind"] == "unknown_probe":
            if not key:
                return {"accepted": True, "action": None, "request": spec}
            if key == self.track_key:
                self.unknown_candidate_key = None
                self.unknown_candidate_match = None
                self.unknown_candidate_count = 0
                self.unknown_candidate_anchor = None
                return {"accepted": True, "action": None, "request": spec}

            if key == self.unknown_candidate_key:
                self.unknown_candidate_count += 1
            else:
                self.unknown_candidate_key = key
                self.unknown_candidate_match = deepcopy(match)
                self.unknown_candidate_count = 1
                self.unknown_candidate_anchor = float(spec["start"])

            if self.unknown_candidate_count >= 2:
                action = {
                    "accepted": True,
                    "action": "successor",
                    "confidence": "high",
                    "match": deepcopy(self.unknown_candidate_match),
                    "anchor": float(self.unknown_candidate_anchor),
                    "previous_end": float(self.unknown_candidate_anchor),
                    "reason": "unknown_duration_consensus",
                    "strength": "medium",
                    "request": spec,
                }
                self.unknown_candidate_key = None
                self.unknown_candidate_match = None
                self.unknown_candidate_count = 0
                self.unknown_candidate_anchor = None
                return action

            return {
                "accepted": True,
                "action": "unknown_candidate",
                "match": deepcopy(match),
                "request": spec,
            }

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

        if winner_key and len(alternatives[winner_key]) >= 2:
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
