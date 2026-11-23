"""GHCR publication safety: do not silently advertise missing WhatsApp image tags."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release


def test_missing_whatsapp_tag_is_rebuilt_at_same_version(monkeypatch):
    seen = []
    def fake_run(args, **kwargs):
        seen.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="manifest unknown")
    monkeypatch.setattr(release.subprocess, "run", fake_run)
    assert release.missing_whatsapp_image("0.3.18") is True
    assert seen == [["docker", "manifest", "inspect",
                     "ghcr.io/justcop/whatsapp-mcp-amd64:0.3.18"]]


def test_public_existing_manifest_does_not_trigger_repair(monkeypatch):
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout="{}", stderr=""))
    assert release.missing_whatsapp_image("0.3.18") is False


def test_unknown_registry_error_fails_closed(monkeypatch):
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=1, stdout="", stderr="TLS certificate failure"))
    with pytest.raises(RuntimeError, match="Ambiguous GHCR"):
        release.missing_whatsapp_image("0.3.18")
