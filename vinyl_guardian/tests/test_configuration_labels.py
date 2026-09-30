from pathlib import Path
import unittest
import yaml


class ConfigurationLabelTests(unittest.TestCase):
    def test_all_settings_have_names_and_help(self):
        root = Path(__file__).resolve().parents[1]
        schema = yaml.safe_load((root / 'config.yaml').read_text())['schema']
        labels = yaml.safe_load((root / 'translations/en.yaml').read_text())['configuration']
        def check(fields, descriptions):
            self.assertEqual(set(fields), set(descriptions))
            for name, field in fields.items():
                description = descriptions[name]
                self.assertTrue(description['name'])
                self.assertNotIn(description['name'][0], ('✅', '🧪', '⚪'))
                self.assertTrue(description['description'])
                if isinstance(field, dict):
                    check(field, description['fields'])
        check(schema, labels)

    def test_obsolete_runtime_settings_are_removed(self):
        root = Path(__file__).resolve().parents[1]
        config = yaml.safe_load((root / 'config.yaml').read_text())
        obsolete = {'code_branch', 'acoustid_key', 'audio_threshold', 'debug_one_shot', 'mic_volume', 'record_diagnostic_sample'}
        self.assertFalse(obsolete & set(config['options']))
        self.assertFalse(obsolete & set(config['schema']))
        launcher = (root / 'run.sh').read_text()
        self.assertNotIn('codeload.github.com', launcher)
        self.assertNotIn('tarfile', launcher)
        self.assertIn('exec python3', launcher)
