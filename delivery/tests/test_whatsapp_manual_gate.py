"""Keep WhatsApp deployment gated by repository compatibility checks."""
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "delivery"))
sys.path.insert(0, str(ROOT / "addon_updater" / "app"))

from updater import Updater  # noqa: E402


def test_approved_whatsapp_code_uses_same_auto_deploy_as_other_apps(tmp_path):
    app = Updater(state_path=tmp_path / "state.json")
    app.state["apps"] = {
        "whatsapp_mcp": {"mode": "live", "pinned": False},
        "audioshelf": {"mode": "live", "pinned": False},
        "vinyl_guardian": {"mode": "live", "pinned": True}
    }
    app.check = lambda: {}
    deployed = []
    app.deploy = lambda name, files=None: deployed.append(name)
    with patch("updater.reconcile", lambda state: None):
        app.run("automatic")
    assert deployed == ["whatsapp_mcp", "audioshelf"]
    assert not app.state.get("error")
    assert app.state["apps"]["whatsapp_mcp"]["mode"] == "live"


def test_whatsapp_incompatible_or_failed_release_never_auto_deploys(tmp_path):
    app = Updater(state_path=tmp_path / "state.json")
    app.state["apps"] = {
        "whatsapp_mcp": {"mode": "rebuild", "pinned": False},
        "audioshelf": {"mode": "live", "pinned": False},
        "vinyl_guardian": {"mode": "failed", "pinned": False},
    }
    app.check = lambda: {}
    deployed = []
    app.deploy = lambda name, files=None: deployed.append(name)
    with patch("updater.reconcile", lambda state: None):
        app.run("automatic")
    assert deployed == ["audioshelf"]


def test_whatsapp_manual_deploy_remains_possible(tmp_path):
    app = Updater(state_path=tmp_path / "state.json")
    app.state["apps"] = {"whatsapp_mcp": {"mode": "live", "pinned": False}}
    app.check = lambda: {}
    deployed = []
    app.deploy = lambda name, sha=None, files=None: deployed.append((name, sha))
    with patch("updater.reconcile", lambda state: None):
        app.run("deploy", "whatsapp_mcp")
    assert deployed == [("whatsapp_mcp", None)]

def test_stale_upstream_workflow_is_reported_without_blocking_other_apps(tmp_path):
    app = Updater(state_path=tmp_path / "state.json")
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"workflow_runs": [{
                "created_at": "2020-01-01T00:00:00Z",
                "html_url": "https://github.com/example/run",
                "status": "completed", "conclusion": "success"
            }]}
    with patch("updater.SESSION.get", return_value=Response()):
        app.monitor_whatsapp_upstream()
    assert "48 hours" in app.state["upstream_monitor"]["error"]
    assert not app.state.get("error")


def test_failed_upstream_check_produces_native_ha_notification():
    from notifier import reconcile
    calls = []
    state = {"apps": {}, "upstream_monitor": {
        "error": "GitHub workflow failed", "last_conclusion": "failure"}}
    reconcile(state, send=lambda service, data: calls.append((service, data)))
    assert calls == []  # Avoid noise from a single transient check.
    reconcile(state, send=lambda service, data: calls.append((service, data)))
    assert len(calls) == 1
    assert calls[0][0] == "create"
    assert calls[0][1]["notification_id"] == "my_addons_whatsapp_upstream_failure"
    state["upstream_monitor"]["error"] = None
    reconcile(state, send=lambda service, data: calls.append((service, data)))
    assert calls[-1][0] == "dismiss"
