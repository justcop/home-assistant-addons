"""PulseAudio input discovery and per-addon source selection for Vinyl Guardian.

Vinyl Guardian talks to Home Assistant's PulseAudio service through the ALSA
Pulse plugin.  Source selection is deliberately per-process via PULSE_SOURCE,
so choosing a turntable input does not unnecessarily change Home Assistant's
global default microphone.

The explicit scan workflow is designed for a user who is playing music:
1. sample every currently exposed non-monitor input;
2. if none contains convincing signal, temporarily probe inactive
   input-capable card profiles;
3. restore profiles after each probe;
4. keep only the winning profile/source and remember it under /share.

No detector thresholds are learned here.  This module only finds the capture
path that actually contains audio.
"""

import json
import math
import os
import subprocess
import time

import numpy as np

from telemetry import FeatureExtractor, pcm16_channels


AUTO_OPTION = "Auto / remembered"
SYSTEM_DEFAULT_OPTION = "System default"


def _atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def _dbfs(value):
    return 20.0 * math.log10(max(float(value), 1e-12))


def score_pcm(raw, rate=44100, channels=2):
    """Score whether captured PCM looks like a useful music-bearing input.

    This is intentionally broad.  It is not a music classifier; during a scan
    the user is explicitly asked to play music, so the useful question is
    primarily "which input actually contains a healthy changing signal?"
    """
    channel_data = pcm16_channels(raw, channels)
    if channel_data.shape[0] < max(256, rate // 20):
        return {
            "score": 0.0,
            "usable": False,
            "rms": 0.0,
            "rms_dbfs": -120.0,
            "peak": 0.0,
            "music_rms": 0.0,
            "music_dbfs": -120.0,
            "activity_fraction": 0.0,
            "spectral_entropy": 0.0,
            "spectral_flatness": 0.0,
        }

    mono = np.mean(channel_data, axis=1)
    rms = float(np.sqrt(np.mean(mono * mono)))
    peak = float(np.max(np.abs(mono)))

    pre = mono[1:] - 0.95 * mono[:-1]
    music_rms = float(np.sqrt(np.mean(pre * pre))) if len(pre) else 0.0

    chunk = 2048
    extractor = FeatureExtractor(rate)
    chunk_rms = []
    entropies = []
    flatnesses = []
    for offset in range(0, len(mono) - chunk + 1, chunk):
        part = mono[offset:offset + chunk]
        chunk_rms.append(float(np.sqrt(np.mean(part * part))))
        features = extractor.extract(part)
        entropies.append(float(features.get("spectral_entropy", 0.0)))
        flatnesses.append(float(features.get("spectral_flatness", 0.0)))

    if chunk_rms:
        arr = np.asarray(chunk_rms, dtype=float)
        activity_gate = max(0.00015, float(np.percentile(arr, 25)) * 1.8)
        activity_fraction = float(np.mean(arr >= activity_gate))
    else:
        activity_fraction = 0.0

    entropy = float(np.median(entropies)) if entropies else 0.0
    flatness = float(np.median(flatnesses)) if flatnesses else 0.0
    rms_db = _dbfs(rms)
    music_db = _dbfs(music_rms)

    # A line input carrying music should score strongly on both overall and
    # pre-emphasised energy.  Spectral entropy/activity prevent a stationary
    # hum from winning merely because it is loud.
    energy_score = _clamp((rms_db + 72.0) / 45.0)
    changing_score = _clamp((music_db + 78.0) / 45.0)
    entropy_score = _clamp((entropy - 0.18) / 0.62)
    activity_score = _clamp(activity_fraction)

    score = (
        0.38 * energy_score
        + 0.38 * changing_score
        + 0.14 * entropy_score
        + 0.10 * activity_score
    )

    usable = (
        peak >= 0.002
        and rms_db >= -68.0
        and music_db >= -74.0
        and score >= 0.24
    )

    return {
        "score": float(score),
        "usable": bool(usable),
        "rms": rms,
        "rms_dbfs": rms_db,
        "peak": peak,
        "music_rms": music_rms,
        "music_dbfs": music_db,
        "activity_fraction": activity_fraction,
        "spectral_entropy": entropy,
        "spectral_flatness": flatness,
    }


def choose_best_candidate(candidates):
    usable = [item for item in candidates if item.get("metrics", {}).get("usable")]
    if not usable:
        return None
    usable.sort(
        key=lambda item: float(item.get("metrics", {}).get("score", 0.0)),
        reverse=True,
    )
    best = usable[0]
    second_score = (
        float(usable[1].get("metrics", {}).get("score", 0.0))
        if len(usable) > 1 else 0.0
    )
    best_score = float(best.get("metrics", {}).get("score", 0.0))
    result = dict(best)
    result["runner_up_score"] = second_score
    result["score_margin"] = best_score - second_score
    if best_score >= 0.62 and (len(usable) == 1 or best_score - second_score >= 0.12):
        result["confidence"] = "high"
    elif best_score >= 0.40:
        result["confidence"] = "medium"
    else:
        result["confidence"] = "low"
    return result


class AudioSourceManager:
    def __init__(
        self,
        share_dir,
        rate=44100,
        channels=2,
        scan_seconds=2.5,
        logger=None,
    ):
        self.share_dir = share_dir
        self.rate = int(rate)
        self.channels = max(1, int(channels))
        self.scan_seconds = max(1.0, min(8.0, float(scan_seconds)))
        self.log = logger or (lambda message: print(message, flush=True))
        self.selection_path = os.path.join(share_dir, "audio_source.json")
        self.scan_path = os.path.join(share_dir, "audio_scan_last.json")
        self.selected_source = None
        self.selected_description = None
        self.selected_card = None
        self.selected_profile = None
        self.last_scan = None

    def _pactl_json(self, kind):
        try:
            result = subprocess.run(
                ["pactl", "-f", "json", "list", kind],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=8,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                payload = json.loads(result.stdout)
                if isinstance(payload, list):
                    return payload
        except Exception:
            pass
        return []

    def list_sources(self, include_monitors=False):
        sources = []
        raw = self._pactl_json("sources")
        if raw:
            for item in raw:
                name = str(item.get("name") or "")
                if not name:
                    continue
                properties = item.get("properties") or {}
                device_class = str(properties.get("device.class") or "")
                is_monitor = (
                    name.endswith(".monitor")
                    or device_class == "monitor"
                    or item.get("monitor_of_sink") not in (None, 4294967295, "4294967295")
                )
                if is_monitor and not include_monitors:
                    continue
                try:
                    card_index = int(item.get("card"))
                except (TypeError, ValueError):
                    card_index = None
                sources.append({
                    "index": item.get("index"),
                    "name": name,
                    "description": str(
                        item.get("description")
                        or properties.get("device.description")
                        or name
                    ),
                    "card_index": card_index,
                    "state": str(item.get("state") or ""),
                    "properties": properties,
                })
            return sources

        # Older pactl versions may not support JSON. Short output still gives
        # a stable symbolic source name, which is enough for selection.
        try:
            result = subprocess.run(
                ["pactl", "list", "short", "sources"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=8,
                check=False,
            )
            for line in result.stdout.splitlines():
                cols = line.split("\t")
                if len(cols) < 2:
                    cols = line.split()
                if len(cols) < 2:
                    continue
                name = cols[1]
                if name.endswith(".monitor") and not include_monitors:
                    continue
                sources.append({
                    "index": cols[0],
                    "name": name,
                    "description": name,
                    "card_index": None,
                    "state": cols[-1] if cols else "",
                    "properties": {},
                })
        except Exception:
            pass
        return sources

    def list_cards(self):
        cards = []
        for item in self._pactl_json("cards"):
            name = str(item.get("name") or "")
            if not name:
                continue
            profiles_raw = item.get("profiles") or {}
            profiles = []
            if isinstance(profiles_raw, dict):
                iterator = profiles_raw.items()
            elif isinstance(profiles_raw, list):
                iterator = (
                    (entry.get("name"), entry)
                    for entry in profiles_raw
                    if isinstance(entry, dict)
                )
            else:
                iterator = []

            for profile_name, spec in iterator:
                if not profile_name or not isinstance(spec, dict):
                    continue
                try:
                    n_sources = int(spec.get("n_sources", spec.get("sources", 0)) or 0)
                except (TypeError, ValueError):
                    n_sources = 0
                try:
                    n_sinks = int(spec.get("n_sinks", spec.get("sinks", 0)) or 0)
                except (TypeError, ValueError):
                    n_sinks = 0
                profiles.append({
                    "name": str(profile_name),
                    "description": str(spec.get("description") or profile_name),
                    "n_sources": n_sources,
                    "n_sinks": n_sinks,
                    "available": str(spec.get("available") or "unknown"),
                    "priority": int(spec.get("priority", 0) or 0),
                })

            active = item.get("active_profile")
            if isinstance(active, dict):
                active = active.get("name")
            cards.append({
                "index": item.get("index"),
                "name": name,
                "description": str(
                    item.get("properties", {}).get("device.description")
                    or item.get("description")
                    or name
                ),
                "active_profile": str(active or ""),
                "profiles": profiles,
            })
        return cards

    def default_source(self):
        try:
            result = subprocess.run(
                ["pactl", "get-default-source"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        try:
            result = subprocess.run(
                ["pactl", "info"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )
            for line in result.stdout.splitlines():
                if line.lower().startswith("default source:"):
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
        return None

    def _saved(self):
        try:
            with open(self.selection_path, "r") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _set_card_profile(self, card, profile):
        if not card or not profile:
            return False
        try:
            result = subprocess.run(
                ["pactl", "set-card-profile", str(card), str(profile)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=8,
                check=False,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _source_by_name(self, name):
        for source in self.list_sources():
            if source.get("name") == name:
                return source
        return None

    def select_source(
        self,
        source_name,
        description=None,
        card_name=None,
        profile_name=None,
        persist=True,
        reason="manual",
    ):
        source_name = str(source_name or "").strip()
        if not source_name:
            return False

        os.environ["PULSE_SOURCE"] = source_name
        try:
            subprocess.run(
                ["pactl", "set-source-mute", source_name, "0"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except Exception:
            pass
        self.selected_source = source_name
        source = self._source_by_name(source_name)
        self.selected_description = (
            description
            or (source or {}).get("description")
            or source_name
        )
        self.selected_card = card_name
        self.selected_profile = profile_name

        if persist:
            _atomic_json(self.selection_path, {
                "source": self.selected_source,
                "description": self.selected_description,
                "card": self.selected_card,
                "profile": self.selected_profile,
                "selected_unix": time.time(),
                "reason": reason,
            })
        return True

    def use_system_default(self, persist=False):
        source_name = self.default_source()
        if not source_name:
            return False
        os.environ.pop("PULSE_SOURCE", None)
        try:
            subprocess.run(
                ["pactl", "set-source-mute", source_name, "0"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except Exception:
            pass
        self.selected_source = source_name
        source = self._source_by_name(source_name)
        self.selected_description = (source or {}).get("description") or source_name
        self.selected_card = None
        self.selected_profile = None
        if persist:
            _atomic_json(self.selection_path, {
                "source": source_name,
                "description": self.selected_description,
                "card": None,
                "profile": None,
                "selected_unix": time.time(),
                "reason": "system_default",
                "follow_system_default": True,
            })
        return True

    def apply_startup(self, preference="auto"):
        preference = str(preference or "auto").strip()
        sources = self.list_sources()

        if preference.lower() in {"default", "system_default", "system default"}:
            self.use_system_default(persist=False)
            return self.status()

        if preference.lower() not in {"auto", ""}:
            source = next((x for x in sources if x.get("name") == preference), None)
            if source:
                self.select_source(
                    source["name"],
                    description=source.get("description"),
                    persist=False,
                    reason="configured",
                )
                return self.status()
            self.log(f"⚠️ Configured audio source is unavailable: {preference}")

        saved = self._saved()
        saved_profile = saved.get("profile")
        saved_card = saved.get("card")
        if saved_profile and saved_card:
            if self._set_card_profile(saved_card, saved_profile):
                time.sleep(0.7)
                sources = self.list_sources()

        saved_name = saved.get("source")
        if saved_name:
            source = next((x for x in sources if x.get("name") == saved_name), None)
            if source:
                self.select_source(
                    saved_name,
                    description=saved.get("description") or source.get("description"),
                    card_name=saved_card,
                    profile_name=saved_profile,
                    persist=False,
                    reason="remembered",
                )
                return self.status()

        # No remembered usable choice: follow HA's default rather than guessing
        # that the first alsa_input is necessarily the user's turntable.
        self.use_system_default(persist=False)
        return self.status()

    def _capture_source(self, source_name, seconds=None):
        seconds = self.scan_seconds if seconds is None else float(seconds)
        attempts = [self.channels]
        if self.channels != 1:
            attempts.append(1)

        for channels in attempts:
            proc = None
            try:
                env = os.environ.copy()
                env["PULSE_SOURCE"] = str(source_name)
                proc = subprocess.Popen(
                    [
                        "parec",
                        "--raw",
                        "--format=s16le",
                        f"--rate={self.rate}",
                        f"--channels={channels}",
                        f"--device={source_name}",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                )
                time.sleep(seconds)
                proc.terminate()
                stdout, stderr = proc.communicate(timeout=3)
                if stdout:
                    return stdout, channels, stderr.decode("utf-8", errors="ignore")
            except Exception as exc:
                try:
                    if proc is not None:
                        proc.kill()
                        proc.communicate(timeout=1)
                except Exception:
                    pass
                last_error = str(exc)
            else:
                last_error = "No PCM data returned."
        return b"", self.channels, locals().get("last_error", "Capture failed.")

    def _test_source(self, source, progress=None, context=None):
        if progress:
            progress(
                f"Testing {source.get('description') or source.get('name')}…"
            )
        raw, channels, error = self._capture_source(source["name"])
        metrics = score_pcm(raw, rate=self.rate, channels=channels)
        result = {
            "source": source.get("name"),
            "description": source.get("description") or source.get("name"),
            "card_index": source.get("card_index"),
            "profile": None,
            "card": None,
            "channels_recorded": channels,
            "metrics": metrics,
        }
        if context:
            result.update(context)
        if error and not raw:
            result["error"] = error
        return result

    def _profiles_to_probe(self, card):
        active = card.get("active_profile")
        profiles = []
        for profile in card.get("profiles", []):
            if profile.get("name") == active:
                continue
            if int(profile.get("n_sources", 0) or 0) <= 0:
                continue
            if str(profile.get("available", "")).lower() == "no":
                continue
            profiles.append(profile)

        # Preserve playback when possible: duplex profiles before input-only.
        profiles.sort(
            key=lambda p: (
                int(p.get("n_sinks", 0) or 0) <= 0,
                -int(p.get("priority", 0) or 0),
            )
        )
        return profiles[:6]

    def scan(self, progress=None, probe_hidden_profiles=True):
        started = time.time()
        progress = progress or self.log
        original_cards = {
            card["name"]: card.get("active_profile")
            for card in self.list_cards()
        }
        candidates = []
        tested_keys = set()

        sources = self.list_sources()
        progress(f"Found {len(sources)} currently exposed capture source(s).")
        for source in sources:
            key = (source.get("name"), None)
            tested_keys.add(key)
            candidates.append(self._test_source(source, progress=progress))

        active_best = choose_best_candidate(candidates)

        # Only disturb card profiles if the currently exposed inputs do not
        # already contain a convincing signal.
        if probe_hidden_profiles and (
            active_best is None
            or float(active_best.get("metrics", {}).get("score", 0.0)) < 0.48
        ):
            progress("No strong active input yet; probing inactive input profiles…")
            cards = self.list_cards()
            for card in cards:
                original = card.get("active_profile")
                for profile in self._profiles_to_probe(card):
                    if not self._set_card_profile(card["name"], profile["name"]):
                        continue
                    time.sleep(0.8)
                    exposed = [
                        source
                        for source in self.list_sources()
                        if (
                            source.get("card_index") == card.get("index")
                            or source.get("card_index") is None
                        )
                    ]
                    for source in exposed:
                        key = (source.get("name"), profile.get("name"))
                        if key in tested_keys:
                            continue
                        tested_keys.add(key)
                        candidates.append(
                            self._test_source(
                                source,
                                progress=progress,
                                context={
                                    "card": card["name"],
                                    "card_description": card.get("description"),
                                    "profile": profile["name"],
                                    "profile_description": profile.get("description"),
                                    "profile_has_output": bool(
                                        int(profile.get("n_sinks", 0) or 0) > 0
                                    ),
                                },
                            )
                        )
                    if original:
                        self._set_card_profile(card["name"], original)
                        time.sleep(0.5)

        # Ensure every card is back where it started before deciding.
        for card_name, profile_name in original_cards.items():
            if profile_name:
                self._set_card_profile(card_name, profile_name)
        time.sleep(0.5)

        winner = choose_best_candidate(candidates)
        applied = False
        if winner is not None:
            card_name = winner.get("card")
            profile_name = winner.get("profile")
            source_name = winner.get("source")
            if card_name and profile_name:
                if self._set_card_profile(card_name, profile_name):
                    time.sleep(0.8)
                    available = self.list_sources()
                    source = next(
                        (x for x in available if x.get("name") == source_name),
                        None,
                    )
                    if source is None:
                        # Source symbolic names can change with a profile. Keep
                        # the winning card and select its newly exposed input.
                        winner_card_index = winner.get("card_index")
                        source = next(
                            (
                                x for x in available
                                if x.get("card_index") == winner_card_index
                            ),
                            None,
                        )
                    if source:
                        source_name = source["name"]
                        winner["source"] = source_name
                        winner["description"] = source.get("description") or source_name

            if self._source_by_name(source_name):
                applied = self.select_source(
                    source_name,
                    description=winner.get("description"),
                    card_name=card_name,
                    profile_name=profile_name,
                    persist=True,
                    reason="music_scan",
                )

        result = {
            "started_unix": started,
            "finished_unix": time.time(),
            "scan_seconds_per_source": self.scan_seconds,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "winner": winner,
            "applied": bool(applied),
            "selected": self.status(),
        }
        self.last_scan = result
        try:
            _atomic_json(self.scan_path, result)
        except Exception:
            pass

        if applied:
            progress(
                f"Selected {winner.get('description')} "
                f"(confidence {winner.get('confidence')}, "
                f"score {winner.get('metrics', {}).get('score', 0.0):.2f})."
            )
        else:
            progress(
                "No convincing music-bearing input was found; existing source "
                "selection was left unchanged."
            )
        return result

    def selectable_options(self):
        return [AUTO_OPTION, SYSTEM_DEFAULT_OPTION] + [
            source["name"] for source in self.list_sources()
        ]

    def select_option(self, option):
        option = str(option or "").strip()
        if option == AUTO_OPTION:
            self.apply_startup("auto")
            return True
        if option == SYSTEM_DEFAULT_OPTION:
            return self.use_system_default(persist=True)
        source = self._source_by_name(option)
        if source is None:
            return False

        card_name = None
        profile_name = None
        card_index = source.get("card_index")
        if card_index is not None:
            for card in self.list_cards():
                try:
                    same_card = int(card.get("index")) == int(card_index)
                except (TypeError, ValueError):
                    same_card = False
                if same_card:
                    card_name = card.get("name")
                    profile_name = card.get("active_profile")
                    break

        return self.select_source(
            source["name"],
            description=source.get("description"),
            card_name=card_name,
            profile_name=profile_name,
            persist=True,
            reason="home_assistant_select",
        )

    def volume_target(self):
        return self.selected_source or os.environ.get("PULSE_SOURCE") or "@DEFAULT_SOURCE@"

    def status(self):
        return {
            "source": self.selected_source,
            "description": self.selected_description,
            "card": self.selected_card,
            "profile": self.selected_profile,
            "pulse_source_env": os.environ.get("PULSE_SOURCE"),
            "system_default": self.default_source(),
            "available_sources": self.list_sources(),
            "saved_selection": self._saved(),
        }
