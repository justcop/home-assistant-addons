"""Durable updater recovery: a process restart must not erase failure state."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "addon_updater" / "app"))
from notifier import reconcile
from updater import Updater


def test_interrupted_transaction_survives_updater_process_restart(tmp_path):
    path = tmp_path / "updater.json"
    first = Updater(path)
    first.state["apps"]["demo"] = {
        "name": "Demo", "mode": "failed", "state": "started",
        "error": "Restart was unsuccessful",
    }
    first.state["transactions"]["demo"] = {
        "before": "a" * 40, "target": "b" * 40,
    }
    first.state["latest"] = "b" * 40
    first.save()

    reloaded = Updater(path)
    assert reloaded.state["transactions"]["demo"]["before"] == "a" * 40
    assert reloaded.state["apps"]["demo"]["error"] == "Restart was unsuccessful"
    assert reloaded.state["apps"]["demo"]["mode"] == "failed"
    assert reloaded.state["latest"] == "b" * 40


def test_undelivered_failure_notification_survives_restart_and_retries(tmp_path):
    path = tmp_path / "updater.json"
    first = Updater(path)
    first.state["apps"]["demo"] = {
        "name": "Demo", "mode": "failed", "state": "started",
        "error": "Failed to launch",
    }

    def unavailable(*args):
        raise ConnectionError("Home Assistant unavailable")

    reconcile(first.state, send=unavailable)
    first.save()

    reloaded = Updater(path)
    assert reloaded.state["apps"]["demo"]["error"] == "Failed to launch"
    assert reloaded.state["notifications"]["demo"]["sent"] is None
    sent = []
    reconcile(reloaded.state, send=lambda action, body: sent.append((action, body)))
    reloaded.save()
    assert len(sent) == 1
    assert sent[0][0] == "create"
    assert sent[0][1]["notification_id"] == "my_addons_demo_failure"

    third = Updater(path)
    sent_again = []
    reconcile(third.state, send=lambda *args: sent_again.append(args))
    assert sent_again == []  # Persisted deduplication prevents repeat alerts.


def test_verified_recovery_dismissal_survives_restart(tmp_path):
    path = tmp_path / "updater.json"
    first = Updater(path)
    first.state["apps"]["demo"] = {
        "name": "Demo", "mode": "failed", "state": "started",
        "error": "Failed to launch",
    }
    delivered = []
    send = lambda action, body: delivered.append((action, body))
    reconcile(first.state, send=send)
    first.save()

    reloaded = Updater(path)
    reloaded.state["apps"]["demo"].update(mode="current", error=None)
    reconcile(reloaded.state, send=send)
    reloaded.save()

    assert [action for action, _ in delivered] == ["create", "dismiss"]
    assert "demo" not in Updater(path).state["notifications"]
