import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release', Path(__file__).resolve().parents[1] / 'vinyl_guardian_release.py')
release = importlib.util.module_from_spec(spec); spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_semantic_versions(self):
        self.assertEqual(release.bump_kind(['fix: capture']), 'patch')
        self.assertEqual(release.bump_kind(['feat: calibration']), 'minor')
        self.assertEqual(release.bump_kind(['feat!: remove branch selection']), 'major')
        self.assertEqual(release.bump_kind(['fix: x\n\nBREAKING CHANGE: schema']), 'major')
        self.assertEqual(release.bump_version('4.50.1', 'major'), '5.0.0')
        self.assertEqual(release.bump_version('5.0.0', 'patch'), '5.0.1')

    def test_runtime_scope_excludes_reports_and_tests(self):
        self.assertTrue(release.runtime_file('vinyl_guardian/detector.py'))
        self.assertTrue(release.runtime_file('vinyl_guardian/translations/en.yaml'))
        self.assertFalse(release.runtime_file('vinyl_guardian/readme.md'))
        self.assertFalse(release.runtime_file('vinyl_guardian/tests/test_detector.py'))
        self.assertFalse(release.runtime_file('.github/vinyl-guardian-settings-report.json'))

    def test_catalog_state_migrates_once_without_importing_other_branches(self):
        with tempfile.TemporaryDirectory() as root:
            config=Path(root)/'config.yaml';state=Path(root)/'state.json'
            config.write_text('version: "4.50.1"\noptions: {}\nschema: {}\n')
            state.write_text(json.dumps({'main':{'sha':'old'},'experiment':{'sha':'other'}}))
            def git(*args):
                if args[0]=='rev-parse':return 'promoted'
                if args[0]=='show':return 'feat!: promote detector and remove runtime branches'
                raise AssertionError(args)
            with patch.object(release,'CONFIG',config),patch.object(release,'STATE',state),patch.object(release,'fingerprint',return_value='runtime'),patch.object(release,'git',side_effect=git):
                release.update()
                self.assertIn('5.0.0',config.read_text())
                self.assertEqual(set(json.loads(state.read_text())), {'sha','fingerprint'})
                before=config.read_text();release.update()
                self.assertEqual(config.read_text(),before)

    def test_real_runtime_changes_bump_once_and_docs_or_bot_versions_do_not(self):
        import os
        import subprocess
        def run(*args):
            subprocess.check_call(['git', *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with tempfile.TemporaryDirectory() as root:
            original=os.getcwd()
            try:
                os.chdir(root)
                run('init');run('config','user.name','Test');run('config','user.email','test@example.test')
                Path('vinyl_guardian').mkdir();Path('.github').mkdir()
                release.CONFIG.write_text('version: "5.0.0"\noptions: {}\nschema: {}\n')
                Path('vinyl_guardian/app.py').write_text('value=1\n')
                run('add','.');run('commit','-m','initial')
                release.update(seed=True)
                run('add','.');run('commit','-m','seed release')
                Path('vinyl_guardian/app.py').write_text('value=2\n')
                run('add','.');run('commit','-m','feat: improve detector')
                release.update()
                self.assertIn('5.1.0',release.CONFIG.read_text())
                run('add','.');run('commit','-m','chore: publish version')
                previous=release.STATE.read_text();release.update()
                self.assertEqual(release.STATE.read_text(),previous)
                Path('vinyl_guardian/readme.md').write_text('docs\n')
                run('add','.');run('commit','-m','feat: document feature')
                release.update()
                self.assertIn('5.1.0',release.CONFIG.read_text())
                Path('vinyl_guardian/app.py').write_text('value=3\n')
                run('add','.');run('commit','-m','fix: improve capture')
                release.update()
                self.assertIn('5.1.1',release.CONFIG.read_text())
            finally:
                os.chdir(original)
