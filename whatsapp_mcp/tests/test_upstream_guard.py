import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "whatsapp_upstream_guard",
    Path(__file__).resolve().parents[1] / "upstream_guard.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class UpstreamGuardTests(unittest.TestCase):
    def test_docs_only_is_low_risk(self):
        risk, flagged = guard.risk_for_paths(
            ["README.md", "docs/setup.md", "tests/example.test.ts"], True)
        self.assertEqual(risk, "low")
        self.assertEqual(flagged, [])

    def test_all_executable_changes_high_risk(self):
        paths = ["packages/api/src/whatsapp/ingest.ts",
                 "pnpm-lock.yaml",
                 "packages/api/src/db/schema.ts",
                 "packages/mcp/src/tools/reads.ts",
                 "packages/api/package.json",
                 ".github/workflows/docker.yml",
                 "packages/api/node_modules/baileys"]
        for name in paths:
            with self.subTest(name=name):
                self.assertEqual(guard.risk_for_paths([name], True)[0], "high")

    def test_missing_or_truncated_comparison_never_auto_approves(self):
        for paths, complete in [([], True), (["README.md"], False)]:
            self.assertEqual(guard.risk_for_paths(paths, complete)[0], "high")

    def test_pin_not_floating(self):
        text = guard.DOCKERFILE.read_text()
        self.assertEqual(len(guard.PIN.findall(text)), 1)
        self.assertIn("pnpm install --frozen-lockfile", text)
        self.assertIn("node /src/patch-upstream.mjs", text)

    def test_plan_uses_exact_sha_not_branch(self):
        current = guard.PIN.findall(guard.DOCKERFILE.read_text())[0][1]
        latest = "a" * 40 if current != "a" * 40 else "b" * 40
        diff = {"status": "ahead", "ahead_by": 1, "files": [{"filename": "README.md"}]}
        plan = guard.plan(latest, diff)
        self.assertEqual(plan["risk"], "low")
        self.assertEqual(plan["latest"], latest)
        self.assertIn(current, plan["upstream_compare"])


if __name__ == "__main__":
    unittest.main()
