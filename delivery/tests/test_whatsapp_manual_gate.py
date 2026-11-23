"""Keep WhatsApp out of the shared unattended code deployment loop."""
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "addon_updater" / "app"))

from updater import MANUAL_DEPLOY_APPS, Updater  # noqa: E402


def test_whatsapp_never_auto_deploys_even_when_compatible(tmp_path):
    assert "whatsapp_mcp" in MANUAL_DEPLOY_APPS
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
    assert deployed == ["audioshelf"]
    assert not app.state.get("error")
    assert app.state["apps"]["whatsapp_mcp"]["mode"] == "live"


def test_whatsapp_manual_deploy_remains_possible(tmp_path):
    app = Updater(state_path=tmp_path / "state.json")
    app.state["apps"] = {"whatsapp_mcp": {"mode": "live", "pinned": False}}
    app.check = lambda: {}
    deployed = []
    app.deploy = lambda name, sha=None, files=None: deployed.append((name, sha))
    with patch("updater.reconcile", lambda state: None):
        app.run("deploy", "whatsapp_mcp")
    assert deployed == [("whatsapp_mcp", None)]
