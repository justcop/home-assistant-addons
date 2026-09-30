"""Versioned detector calibration profiles with safe rollback."""

import json
import os
import time


def _atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


class ProfileManager:
    def __init__(self, share_dir, active_file):
        self.share_dir = share_dir
        self.active_file = active_file
        self.root = os.path.join(share_dir, "profiles")
        self.pointer_file = os.path.join(self.root, "active_profile.json")
        os.makedirs(self.root, exist_ok=True)

    def _profile_files(self):
        files = []
        try:
            for name in os.listdir(self.root):
                if name.startswith("profile_") and name.endswith(".json"):
                    files.append(os.path.join(self.root, name))
        except Exception:
            pass
        return sorted(files)

    def _load(self, path):
        with open(path, "r") as handle:
            return json.load(handle)

    def _pointer(self):
        try:
            return self._load(self.pointer_file)
        except Exception:
            return {}

    def ensure_active_archived(self):
        """Archive an old pre-versioning active calibration once."""
        pointer = self._pointer()
        if pointer.get("profile_id"):
            return pointer.get("profile_id")
        if not os.path.exists(self.active_file):
            return None

        try:
            thresholds = self._load(self.active_file)
        except Exception:
            return None

        created = os.path.getmtime(self.active_file)
        profile_id = time.strftime("legacy_%Y%m%d_%H%M%S", time.localtime(created))
        path = os.path.join(self.root, f"profile_{profile_id}.json")
        if not os.path.exists(path):
            _atomic_json(path, {
                "profile_id": profile_id,
                "created_unix": created,
                "accepted": True,
                "source": "pre_versioning_active_calibration",
                "thresholds": thresholds,
                "metadata": {},
                "quality": {},
                "regression": {},
            })
        _atomic_json(self.pointer_file, {
            "profile_id": profile_id,
            "updated_unix": time.time(),
        })
        return profile_id

    def save_candidate(
        self,
        thresholds,
        metadata=None,
        quality=None,
        regression=None,
        accepted=False,
    ):
        self.ensure_active_archived()
        created = time.time()
        base_id = time.strftime("%Y%m%d_%H%M%S", time.localtime(created))
        profile_id = base_id
        counter = 1
        while os.path.exists(os.path.join(self.root, f"profile_{profile_id}.json")):
            profile_id = f"{base_id}_{counter:02d}"
            counter += 1

        payload = {
            "profile_id": profile_id,
            "created_unix": created,
            "accepted": bool(accepted),
            "source": "calibration",
            "thresholds": dict(thresholds or {}),
            "metadata": metadata or {},
            "quality": quality or {},
            "regression": regression or {},
        }
        path = os.path.join(self.root, f"profile_{profile_id}.json")
        _atomic_json(path, payload)
        if accepted:
            self.promote(profile_id)
        return payload

    def promote(self, profile_id):
        path = os.path.join(self.root, f"profile_{profile_id}.json")
        payload = self._load(path)
        thresholds = payload.get("thresholds")
        if not isinstance(thresholds, dict):
            raise ValueError("Profile has no thresholds dictionary")

        _atomic_json(self.active_file, thresholds)
        if not payload.get("accepted"):
            payload["accepted"] = True
            _atomic_json(path, payload)

        _atomic_json(self.pointer_file, {
            "profile_id": profile_id,
            "updated_unix": time.time(),
        })
        return payload

    def rollback_previous(self):
        self.ensure_active_archived()
        accepted = []
        for path in self._profile_files():
            try:
                payload = self._load(path)
                if payload.get("accepted"):
                    accepted.append(payload)
            except Exception:
                continue

        accepted.sort(key=lambda item: float(item.get("created_unix", 0.0)))
        if len(accepted) < 2:
            return None

        current_id = self._pointer().get("profile_id")
        current_index = None
        for i, item in enumerate(accepted):
            if item.get("profile_id") == current_id:
                current_index = i
                break

        if current_index is None:
            target = accepted[-2]
        elif current_index <= 0:
            return None
        else:
            target = accepted[current_index - 1]

        return self.promote(target["profile_id"])

    def status(self):
        pointer = self._pointer()
        profiles = []
        for path in self._profile_files():
            try:
                payload = self._load(path)
                profiles.append({
                    "profile_id": payload.get("profile_id"),
                    "created_unix": payload.get("created_unix"),
                    "accepted": bool(payload.get("accepted")),
                    "quality": payload.get("quality", {}),
                })
            except Exception:
                pass
        profiles.sort(key=lambda item: float(item.get("created_unix", 0.0)))
        return {
            "active_profile_id": pointer.get("profile_id"),
            "profile_count": len(profiles),
            "accepted_profile_count": sum(1 for x in profiles if x["accepted"]),
            "profiles": profiles,
        }
