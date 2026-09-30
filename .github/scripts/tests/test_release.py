import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "vinyl_guardian_release.py"
spec = importlib.util.spec_from_file_location("release", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_semantic_version_rules(self):
        self.assertEqual(release.bump_kind(["fix: avoid false triggers"]), "patch")
        self.assertEqual(release.bump_kind(["feat(audio): add input scanning"]), "minor")
        self.assertEqual(release.bump_kind(["feat!: change configuration"]), "major")
        self.assertEqual(release.bump_kind(["fix: input\n\nBREAKING CHANGE: new format"]), "major")
        self.assertEqual(release.bump_kind(["feat: feature"], "patch"), "patch")
        self.assertEqual(release.bump_version("4.40.9", "minor"), "4.41.0")
        self.assertEqual(release.bump_version("4.40.9", "major"), "5.0.0")
        self.assertEqual(release.bump_version("4.4", "patch"), "4.4.1")

    def test_runtime_scope(self):
        self.assertTrue(release.runtime_file("vinyl_guardian/detector.py"))
        self.assertTrue(release.runtime_file("vinyl_guardian/Dockerfile"))
        self.assertFalse(release.runtime_file("vinyl_guardian/readme.md"))
        self.assertFalse(release.runtime_file("vinyl_guardian/tests/test_detector.py"))

    def test_catalog_and_updates_are_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.yaml"
            state = Path(folder) / "state.json"
            config.write_text('version: "4.40.0"\noptions:\n  code_branch: "main"\nschema:\n  code_branch: "str"\n')
            branches = {"main": {"sha": "a", "fingerprint": "a"},
                        "test/slash": {"sha": "b", "fingerprint": "b"}}
            with patch.object(release, "CONFIG", config), patch.object(release, "STATE", state), patch.object(release, "collect", return_value=branches):
                release.update(seed=True)
                self.assertIn('list(main|test/slash)', config.read_text())
                self.assertIn('code_branch: "main"', config.read_text())
                release.update()
                self.assertIn('4.40.0', config.read_text())
                branches["main"]["sha"] = "docs-only"
                release.update()
                self.assertEqual(json.loads(state.read_text())["main"]["sha"], "a")
                del branches["test/slash"]
                release.update()
                self.assertIn('4.40.1', config.read_text())
                release.update()
                self.assertIn('4.40.1', config.read_text())

    def test_git_fingerprint_ignores_version_catalog_and_docs(self):
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                return subprocess.check_output(["git", "-C", folder, *args], text=True).strip()
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            addon = Path(folder) / "vinyl_guardian"
            addon.mkdir()
            config = addon / "config.yaml"
            config.write_text('version: "4.40.0"\nschema:\n  code_branch: "str"\n')
            code = addon / "detector.py"
            code.write_text("value = 1\n")
            def commit():
                git("add", ".")
                git("commit", "-qm", "fix: test")
            commit()
            with patch.object(release, "git", git):
                original = release.fingerprint("HEAD")
                config.write_text('version: "4.40.1"\nschema:\n  code_branch: "list(main|experiment)"\n')
                (addon / "readme.md").write_text("Documentation")
                commit()
                self.assertEqual(original, release.fingerprint("HEAD"))
                code.write_text("value = 2\n")
                commit()
                self.assertNotEqual(original, release.fingerprint("HEAD"))
