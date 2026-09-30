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
        self.assertTrue(release.runtime_file("vinyl_guardian/translations/en.yaml"))
        self.assertFalse(release.runtime_file("vinyl_guardian/readme.md"))
        self.assertFalse(release.runtime_file("vinyl_guardian/tests/test_detector.py"))

    def test_catalog_and_updates_are_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.yaml"
            state = Path(folder) / "state.json"
            report = Path(folder) / "report.json"
            config.write_text('version: "4.40.0"\noptions:\n  code_branch: "main"\nschema:\n  code_branch: "str"\n')
            branches = {"main": {"sha": "a", "fingerprint": "a"},
                        "test/slash": {"sha": "b", "fingerprint": "b"}}
            with patch.object(release, "CONFIG", config), patch.object(release, "STATE", state), patch.object(release, "SETTINGS_REPORT", report), patch.object(release, "collect", return_value=branches), patch.object(release, 'load_branch_configs', return_value={}), patch.object(release, 'fingerprint', return_value='a'):
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

    def test_nested_fields_and_main_defaults_are_preserved(self):
        main = {'options': {'advanced': {'gain': 8}}, 'schema': {'advanced': {'gain': 'int'}}}
        branch = {'options': {'advanced': {'gain': 20, 'window': 5}, 'capture': False},
                  'schema': {'advanced': {'gain': 'int', 'window': 'int(1,10)'}, 'capture': 'bool'}}
        merged, report = release.merge_settings(main, {'experimental': branch})
        self.assertEqual(merged['options']['advanced'], {'gain': 8, 'window': 5})
        self.assertFalse(merged['options']['capture'])
        self.assertEqual(merged['schema']['advanced']['window'], 'int(1,10)')
        self.assertIn('advanced.window', report['added_options'])
        self.assertNotIn('window', main['options']['advanced'])

    def test_incompatible_new_fields_are_not_imported(self):
        main = {'options': {'port': 1883}, 'schema': {'port': 'port'}}
        a = {'schema': {'value': 'bool', 'port': 'str'}}
        b = {'schema': {'value': 'int'}}
        merged, report = release.merge_settings(main, {'a': a, 'b': b})
        self.assertNotIn('value', merged['schema'])
        self.assertEqual(merged['schema']['port'], 'port')
        self.assertEqual(merged['options']['port'], 1883)
        self.assertEqual(len(report['conflicts']), 2)

    def test_disagreeing_or_absent_defaults_make_new_settings_optional(self):
        main = {'options': {}, 'schema': {}}
        a = {'options': {'mode': 'a'}, 'schema': {'mode': 'str', 'secret': 'password'}}
        b = {'options': {'mode': 'b'}, 'schema': {'mode': 'str'}}
        merged, report = release.merge_settings(main, {'a': a, 'b': b})
        self.assertEqual(merged['schema'], {'mode': 'str?', 'secret': 'password?'})
        self.assertEqual(merged['options'], {})
        self.assertEqual(report['conflicts'][0]['reason'], 'defaults differ')
        # Never put default values (possibly credentials) into reports.
        self.assertNotIn('"a"', json.dumps(report['conflicts'][0].get('defaults', {})))

    def test_scalar_group_conflict_does_not_crash_or_mutate_main(self):
        main = {'options': {'value': 1}, 'schema': {'value': 'int'}}
        branch = {'options': {'value': {'child': True}}, 'schema': {'value': {'child': 'bool'}}}
        merged, report = release.merge_settings(main, {'branch': branch})
        self.assertEqual(merged, main)
        self.assertEqual(len(report['conflicts']), 1)

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

    def test_generated_schema_does_not_publish_a_second_update(self):
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                return subprocess.check_output(['git', '-C', folder, *args], text=True).strip()
            git('init', '-q', '-b', 'main')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.com')
            config = Path(folder) / 'vinyl_guardian/config.yaml'
            config.parent.mkdir()
            config.write_text('version: "4.40.0"\noptions:\n  code_branch: "main"\nschema:\n  code_branch: "str"\n')
            def commit():
                git('add', '.')
                git('commit', '-qm', 'fix: settings')
            commit()
            git('update-ref', 'refs/remotes/origin/main', 'HEAD')
            git('checkout', '-qb', 'experiment')
            config.write_text('version: "4.40.0"\noptions:\n  code_branch: "main"\n  capture: false\nschema:\n  code_branch: "str"\n  capture: "bool"\n')
            commit()
            git('update-ref', 'refs/remotes/origin/experiment', 'HEAD')
            git('checkout', '-q', 'main')
            with patch.object(release, 'git', git), patch.object(release, 'CONFIG', config), patch.object(release, 'STATE', Path(folder) / 'state.json'), patch.object(release, 'SETTINGS_REPORT', Path(folder) / 'report.json'):
                # Absolute test path differs from the repository's config path.
                def configs(current):
                    return {b: release.yaml.safe_load(git('show', f'origin/{b}:vinyl_guardian/config.yaml')) for b in current}
                original_fingerprint = release.fingerprint
                def fingerprint(ref, config_override=None):
                    with patch.object(release, 'CONFIG', Path('vinyl_guardian/config.yaml')):
                        return original_fingerprint(ref, config_override)
                with patch.object(release, 'load_branch_configs', configs), patch.object(release, 'fingerprint', fingerprint):
                    release.update()
                    first = release.yaml.safe_load(config.read_text())
                    self.assertEqual(first['version'], '4.40.1')
                    self.assertFalse(first['options']['capture'])
                    commit()
                    git('update-ref', 'refs/remotes/origin/main', 'HEAD')
                    release.update()
                    self.assertEqual(release.yaml.safe_load(config.read_text())['version'], '4.40.1')
