"""Human review queue for automatically captured Vinyl Guardian audio."""
import json
import os
import re
import time
from pathlib import Path

VALID_REVIEW_LABELS = (
    "actually_off",
    "motor_on_needle_up",
    "playing",
    "between_tracks",
    "runout",
    "needle_lifted",
    "wrong_state",
    "ambiguous",
    "ignore",
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def _atomic_json(path, payload):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2))
    os.replace(temp, path)


def _root(recording_directory):
    return (Path(recording_directory) / "experiments" / "event_audio").resolve()


def _sidecar(recording_directory, sample_id):
    sample_id = str(sample_id or "")
    if not _SAFE_ID.fullmatch(sample_id):
        raise ValueError("Invalid sample id")
    root = _root(recording_directory)
    path = (root / (sample_id + ".json")).resolve()
    if path.parent != root:
        raise ValueError("Invalid sample path")
    return path


def _normalise_review(metadata):
    review = metadata.get("review")
    if not isinstance(review, dict):
        review = {}
    status = review.get("status")
    if status not in ("pending", "reviewed"):
        # Old one-shot manual labels are explicit human confirmation. Old
        # automatic labels, including known-off mode captures, are NOT trusted.
        if metadata.get("event") == "manual_label" and metadata.get("label"):
            review = {
                "status": "reviewed",
                "reviewed_label": metadata.get("label"),
                "reviewed_unix": metadata.get("trigger_time"),
                "source": "legacy_manual_mark",
            }
        else:
            review = {
                "status": "pending",
                "suggested_label": (
                    (metadata.get("details") or {}).get("suggested_label")
                    or metadata.get("label")
                ),
            }
    return review


def list_samples(recording_directory, limit=100):
    root = _root(recording_directory)
    if not root.exists():
        return {"pending": 0, "reviewed": 0, "samples": []}
    rows = []
    for path in root.glob("*.json"):
        if path.is_symlink():
            continue
        try:
            metadata = json.loads(path.read_text())
            review = _normalise_review(metadata)
            wav_name = Path(str(metadata.get("wav") or "")).name
            wav = root / wav_name
            if not wav.is_file():
                continue
            details = metadata.get("details") or {}
            production = details.get("production") or {}
            rows.append({
                "id": path.stem,
                "event": metadata.get("event"),
                "trigger_time": metadata.get("trigger_time"),
                "wav": wav_name,
                "review": review,
                "production_status": production.get("status"),
                "production": {
                    "turntable_on": production.get("turntable_on"),
                    "music_active": production.get("music_active"),
                    "runout_locked": production.get("runout_locked"),
                },
                "reason": details.get("purpose") or details.get("reasons") or details.get("transition_type"),
                "candidate_id": details.get("candidate_id"),
            })
        except (OSError, ValueError, TypeError):
            continue
    rows.sort(key=lambda row: float(row.get("trigger_time") or 0), reverse=True)
    pending = sum(row["review"].get("status") != "reviewed" for row in rows)
    reviewed = len(rows) - pending
    return {"pending": pending, "reviewed": reviewed, "samples": rows[:max(1, int(limit))]}


def review_sample(recording_directory, sample_id, label):
    label = str(label or "").strip().lower()
    if label not in VALID_REVIEW_LABELS:
        raise ValueError("Invalid review label")
    path = _sidecar(recording_directory, sample_id)
    metadata = json.loads(path.read_text())
    metadata["review"] = {
        "status": "reviewed",
        "reviewed_label": label,
        "reviewed_unix": time.time(),
        "source": "human_after_the_fact",
    }
    # Keep the old top-level field informational only. Regression code uses
    # review.reviewed_label exclusively.
    metadata["label"] = label
    _atomic_json(path, metadata)
    return {
        "id": path.stem,
        "review": metadata["review"],
    }


def audio_bytes(recording_directory, sample_id):
    path = _sidecar(recording_directory, sample_id)
    metadata = json.loads(path.read_text())
    root = _root(recording_directory)
    wav = (root / Path(str(metadata.get("wav") or "")).name).resolve()
    if wav.parent != root or not wav.is_file() or wav.is_symlink():
        raise ValueError("Audio is unavailable")
    return wav.read_bytes()
