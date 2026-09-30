from pathlib import Path
import unittest
import yaml


class ConfigurationLabelTests(unittest.TestCase):
    def test_all_settings_and_nested_fields_have_branch_labels_and_help(self):
        root = Path(__file__).resolve().parents[1]
        schema = yaml.safe_load((root / 'config.yaml').read_text())['schema']
        labels = yaml.safe_load((root / 'translations/en.yaml').read_text())['configuration']
        def check(fields, descriptions):
            self.assertEqual(set(fields), set(descriptions))
            for name, field in fields.items():
                description = descriptions[name]
                self.assertIn(description['name'][0], ('✅', '🧪', '⚪'))
                self.assertTrue(description['description'])
                if isinstance(field, dict):
                    check(field, description['fields'])
        check(schema, labels)
