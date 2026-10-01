"""Observational safety metrics for Vinyl Guardian detector development.

Latency improvements are useful only when they do not create new false state
changes. This monitor measures stable-state exposure and shadow-detector
pressure without influencing the production detector.
"""

import json
import os


STABLE_SECONDS = 5.0
SAVE_INTERVAL_SECONDS = 30.0


def _atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _nested_number(mapping, *keys):
    cursor = mapping
    for key in keys[:-1]:
        cursor = cursor.setdefault(key, {})
    return cursor.setdefault(keys[-1], 0.0)


def _add_nested(mapping, amount, *keys):
    cursor = mapping
    for key in keys[:-1]:
        cursor = cursor.setdefault(key, {})
    cursor[keys[-1]] = float(cursor.get(keys[-1], 0.0)) + float(amount)


def _inc_nested(mapping, *keys):
    cursor = mapping
    for key in keys[:-1]:
        cursor = cursor.setdefault(key, {})
    cursor[keys[-1]] = int(cursor.get(keys[-1], 0)) + 1


class SafetyMonitor:
    """Track false-transition opportunities and shadow pressure over real use."""

    def __init__(self, share_dir, timeline=None):
        self.path = os.path.join(share_dir, "experiments", "safety_metrics.json")
        self.timeline = timeline
        self.metrics = {
            "format_version": 1,
            "production_exposure_seconds": {},
            "stable_exposure_seconds": {},
            "production_transitions": {},
            "shadow_disagreement_seconds": {},
            "shadow_disagreement_episodes": {},
            "known_off_exposure_seconds": 0.0,
            "known_off_production_false_seconds": 0.0,
            "known_off_production_false_episodes": 0,
            "known_off_shadow_false_seconds": {},
            "known_off_shadow_false_episodes": {},
        }
        self.last_now = None
        self.production_status = None
        self.production_status_since = None
        self.shadow_episode_active = {}
        self.known_off_production_active = False
        self.known_off_shadow_active = {}
        self.last_save = -1e12
        self._load()

    def _load(self):
        try:
            with open(self.path, "r") as handle:
                saved = json.load(handle)
            if isinstance(saved, dict):
                for key in self.metrics:
                    if key in saved:
                        self.metrics[key] = saved[key]
        except (OSError, ValueError, TypeError):
            pass

    @staticmethod
    def _active_signature(frame):
        return bool(
            frame.get("turntable_on")
            or frame.get("music_active")
            or frame.get("runout_locked")
        )

    def observe(self, production, shadows, now, diagnostic_mode="normal"):
        now = float(now)
        status = str(production.get("status") or "Unknown")

        if self.last_now is None:
            dt = 0.0
        else:
            dt = max(0.0, min(1.0, now - self.last_now))
        self.last_now = now

        if self.production_status is None:
            self.production_status = status
            self.production_status_since = now
        elif status != self.production_status:
            before = self.production_status
            key = before + " -> " + status
            self.metrics["production_transitions"][key] = int(
                self.metrics["production_transitions"].get(key, 0)
            ) + 1
            self.production_status = status
            self.production_status_since = now
            self.shadow_episode_active.clear()
        else:
            _add_nested(self.metrics["production_exposure_seconds"], dt, status)

        stable_for = max(
            0.0,
            now - float(self.production_status_since if self.production_status_since is not None else now),
        )
        stable = stable_for >= STABLE_SECONDS
        if stable:
            _add_nested(self.metrics["stable_exposure_seconds"], dt, status)

        for name, shadow in (shadows or {}).items():
            shadow_status = str(shadow.get("status") or "Unknown")
            key = (name, status)
            disagrees = stable and shadow_status != status
            if disagrees:
                _add_nested(
                    self.metrics["shadow_disagreement_seconds"],
                    dt,
                    name,
                    status,
                )
                if not self.shadow_episode_active.get(key):
                    self.shadow_episode_active[key] = True
                    _inc_nested(
                        self.metrics["shadow_disagreement_episodes"],
                        name,
                        status,
                    )
                    if self.timeline is not None:
                        self.timeline.record(
                            "shadow_safety_disagreement_started",
                            now=now,
                            shadow=name,
                            production_status=status,
                            shadow_status=shadow_status,
                            production_stable_sec=stable_for,
                        )
            else:
                self.shadow_episode_active[key] = False

        if diagnostic_mode == "known_off":
            self.metrics["known_off_exposure_seconds"] = float(
                self.metrics.get("known_off_exposure_seconds", 0.0)
            ) + dt

            prod_false = self._active_signature(production)
            if prod_false:
                self.metrics["known_off_production_false_seconds"] = float(
                    self.metrics.get("known_off_production_false_seconds", 0.0)
                ) + dt
                if not self.known_off_production_active:
                    self.metrics["known_off_production_false_episodes"] = int(
                        self.metrics.get("known_off_production_false_episodes", 0)
                    ) + 1
            self.known_off_production_active = prod_false

            for name, shadow in (shadows or {}).items():
                false = self._active_signature(shadow)
                if false:
                    self.metrics["known_off_shadow_false_seconds"][name] = float(
                        self.metrics["known_off_shadow_false_seconds"].get(name, 0.0)
                    ) + dt
                    if not self.known_off_shadow_active.get(name):
                        self.metrics["known_off_shadow_false_episodes"][name] = int(
                            self.metrics["known_off_shadow_false_episodes"].get(name, 0)
                        ) + 1
                self.known_off_shadow_active[name] = false
        else:
            self.known_off_production_active = False
            self.known_off_shadow_active.clear()

        if now - self.last_save >= SAVE_INTERVAL_SECONDS:
            self.last_save = now
            self._save(now)
        return self.summary(now)

    @staticmethod
    def _rates(seconds_by_state, episodes_by_state):
        result = {}
        names = set(seconds_by_state) | set(episodes_by_state)
        for name in names:
            exposure = float(seconds_by_state.get(name, 0.0))
            episodes = int(episodes_by_state.get(name, 0))
            result[name] = {
                "episodes": episodes,
                "seconds": exposure,
            }
        return result

    def _save(self, now):
        payload = self.summary(now)
        _atomic_json(self.path, payload)

    def summary(self, now=None):
        payload = dict(self.metrics)
        payload["updated_unix"] = float(now if now is not None else (self.last_now or 0.0))

        stable = self.metrics.get("stable_exposure_seconds", {})
        shadow_seconds = self.metrics.get("shadow_disagreement_seconds", {})
        shadow_episodes = self.metrics.get("shadow_disagreement_episodes", {})
        rates = {}
        for shadow in set(shadow_seconds) | set(shadow_episodes):
            rates[shadow] = {}
            states = set(shadow_seconds.get(shadow, {})) | set(
                shadow_episodes.get(shadow, {})
            )
            for state in states:
                exposure = float(stable.get(state, 0.0))
                seconds = float(shadow_seconds.get(shadow, {}).get(state, 0.0))
                episodes = int(shadow_episodes.get(shadow, {}).get(state, 0))
                rates[shadow][state] = {
                    "stable_exposure_hours": exposure / 3600.0,
                    "disagreement_seconds": seconds,
                    "disagreement_fraction": seconds / exposure if exposure > 0 else None,
                    "disagreement_episodes": episodes,
                    "episodes_per_hour": episodes / (exposure / 3600.0) if exposure > 0 else None,
                }
        payload["shadow_safety_rates"] = rates

        known_off_hours = float(self.metrics.get("known_off_exposure_seconds", 0.0)) / 3600.0
        payload["known_off_rates"] = {
            "exposure_hours": known_off_hours,
            "production": {
                "false_seconds": float(
                    self.metrics.get("known_off_production_false_seconds", 0.0)
                ),
                "false_episodes": int(
                    self.metrics.get("known_off_production_false_episodes", 0)
                ),
                "false_episodes_per_hour": (
                    int(self.metrics.get("known_off_production_false_episodes", 0))
                    / known_off_hours
                    if known_off_hours > 0
                    else None
                ),
            },
            "shadows": {},
        }
        for name in set(self.metrics.get("known_off_shadow_false_seconds", {})) | set(
            self.metrics.get("known_off_shadow_false_episodes", {})
        ):
            episodes = int(
                self.metrics.get("known_off_shadow_false_episodes", {}).get(name, 0)
            )
            payload["known_off_rates"]["shadows"][name] = {
                "false_seconds": float(
                    self.metrics.get("known_off_shadow_false_seconds", {}).get(name, 0.0)
                ),
                "false_episodes": episodes,
                "false_episodes_per_hour": (
                    episodes / known_off_hours if known_off_hours > 0 else None
                ),
            }
        return payload
