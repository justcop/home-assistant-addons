import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from regression import _safety_false_state, compare_profiles
from safety_monitor import SafetyMonitor


def frame(status, power=False, music=False, runout=False):
    return {
        "status": status,
        "turntable_on": power,
        "music_active": music,
        "runout_locked": runout,
    }


class Timeline:
    def __init__(self):
        self.events = []

    def record(self, event, now=None, **details):
        self.events.append(dict(event=event, now=now, **details))


class SafetyMonitorTests(unittest.TestCase):
    def test_known_off_counts_false_activation_episodes(self):
        with tempfile.TemporaryDirectory() as root:
            monitor = SafetyMonitor(root, Timeline())
            off = frame("Powered Off")
            false_on = frame("Motor Idle", power=True)
            monitor.observe(off, {}, 1000.0, diagnostic_mode="known_off")
            monitor.observe(false_on, {}, 1001.0, diagnostic_mode="known_off")
            monitor.observe(false_on, {}, 1002.0, diagnostic_mode="known_off")
            monitor.observe(off, {}, 1003.0, diagnostic_mode="known_off")
            monitor.observe(false_on, {}, 1004.0, diagnostic_mode="known_off")
            summary = monitor.summary(1004.0)
            self.assertEqual(
                summary["known_off_rates"]["production"]["false_episodes"], 2
            )
            self.assertGreaterEqual(
                summary["known_off_rates"]["production"]["false_seconds"], 2.0
            )

    def test_shadow_disagreement_only_counts_after_stable_period(self):
        with tempfile.TemporaryDirectory() as root:
            timeline = Timeline()
            monitor = SafetyMonitor(root, timeline)
            prod = frame("Motor Idle", power=True)
            same = {"test": frame("Motor Idle", power=True)}
            disagree = {"test": frame("Playing", power=True, music=True)}
            monitor.observe(prod, same, 1000.0)
            monitor.observe(prod, disagree, 1002.0)
            summary = monitor.observe(prod, disagree, 1006.0)
            state = summary["shadow_safety_rates"]["test"]["Motor Idle"]
            self.assertEqual(state["disagreement_episodes"], 1)
            self.assertGreater(state["disagreement_seconds"], 0.0)

    def test_metrics_survive_restart(self):
        with tempfile.TemporaryDirectory() as root:
            monitor = SafetyMonitor(root)
            prod = frame("Powered Off")
            monitor.observe(prod, {}, 1000.0, diagnostic_mode="known_off")
            monitor.observe(prod, {}, 1031.0, diagnostic_mode="known_off")
            before = monitor.summary(1031.0)["known_off_exposure_seconds"]
            restored = SafetyMonitor(root)
            self.assertEqual(
                restored.summary()["known_off_exposure_seconds"], before
            )


class RegressionSafetyGateTests(unittest.TestCase):
    def result(self, false_episodes=0, false_seconds=0.0, penalty=0.0):
        return {
            "fixture_count": 5,
            "total_penalty": penalty,
            "severe_failures": 0,
            "safety_fixture_count": 3,
            "safety_false_activation_episodes": false_episodes,
            "safety_false_activation_seconds": false_seconds,
            "fixtures": [],
        }

    def test_negative_control_state_rules(self):
        self.assertTrue(
            _safety_false_state("off", frame("Motor Idle", power=True))
        )
        self.assertFalse(
            _safety_false_state("motor", frame("Motor Idle", power=True))
        )
        self.assertTrue(
            _safety_false_state("motor", frame("Playing", power=True, music=True))
        )
        self.assertFalse(
            _safety_false_state("music", frame("Powered Off"))
        )

    def test_faster_candidate_cannot_override_new_false_episode(self):
        candidate = self.result(false_episodes=1, false_seconds=0.05, penalty=0.0)
        baseline = self.result(false_episodes=0, false_seconds=0.0, penalty=50.0)
        with patch("regression.evaluate_profile", side_effect=[candidate, baseline]):
            result = compare_profiles({}, {"baseline": True}, "/tmp")
        self.assertFalse(result["accepted"])
        self.assertFalse(result["safety_gate_passed"])
        self.assertIn("Safety gate failed", result["reason"])

    def test_equal_safety_can_still_win_on_general_regression(self):
        candidate = self.result(false_episodes=0, false_seconds=0.0, penalty=5.0)
        baseline = self.result(false_episodes=0, false_seconds=0.0, penalty=5.0)
        with patch("regression.evaluate_profile", side_effect=[candidate, baseline]):
            result = compare_profiles({}, {"baseline": True}, "/tmp")
        self.assertTrue(result["accepted"])
        self.assertTrue(result["safety_gate_passed"])


if __name__ == "__main__":
    unittest.main()
