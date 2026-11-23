import importlib.util
from pathlib import Path
import unittest

SRC = Path(__file__).resolve().parents[1] / "upstream-policy.py"
spec = importlib.util.spec_from_file_location("whatsapp_upstream_policy", SRC)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class PolicyTests(unittest.TestCase):
    def test_pin_is_unique_and_immutable(self):
        old = "5f19883b0d8e2f672d486aa6307f3e945b49532e"
        self.assertEqual(mod.pinned("ARG UPSTREAM_COMMIT=" + old + "\n"), old)
        with self.assertRaises(ValueError):
            mod.pinned("ARG UPSTREAM_COMMIT=main\n")
        with self.assertRaises(ValueError):
            mod.pinned("ARG UPSTREAM_COMMIT=" + old + "\nARG UPSTREAM_COMMIT=" + old)

    def test_only_documentation_changes_may_auto_approve(self):
        files = [{"filename": "README.md", "status": "modified"},
                 {"filename": "docs/SETUP.md", "status": "modified"}]
        risk, reason = mod.classify(files, "ahead", 2)
        self.assertEqual(risk, "low")
        self.assertIn("Documentation-only", reason)

    def test_privacy_protocol_database_and_dependencies_are_always_high(self):
        for filename in [
            "packages/api/src/whatsapp/ingest.ts",
            "pnpm-lock.yaml", "packages/api/package.json",
            "packages/api/src/db/migrations/014.sql",
            "packages/api/src/whatsapp/privacy-retention.ts",
            "packages/mcp/src/tools/reads.ts",
            ".github/workflows/build.yml", "Dockerfile"
        ]:
            with self.subTest(filename=filename):
                self.assertEqual(mod.classify(
                    [{"filename": filename, "status": "modified"}], "ahead", 1
                )[0], "high")

    def test_default_deny_for_truncated_diverged_or_ambiguous_comparisons(self):
        docs = [{"filename": "README.md", "status": "modified"}]
        for status, count, truncated in [
            ("diverged", 1, False), ("behind", 1, False),
            ("ahead", 101, False), ("ahead", 1, True),
            ("ahead", 0, False)
        ]:
            with self.subTest(status=status, count=count, truncated=truncated):
                self.assertEqual(mod.classify(docs,status,count,truncated)[0], "high")
        self.assertEqual(mod.classify([], "ahead", 1)[0], "high")
        self.assertEqual(mod.classify(
            [{"filename": "docs/new.md", "status": "added"}], "ahead", 1)[0], "high")
        self.assertEqual(mod.classify(docs * 300, "ahead", 1)[0], "high")


if __name__ == "__main__":
    unittest.main()
