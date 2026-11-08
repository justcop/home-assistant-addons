import unittest
from policy import structural_eligibility, merge_safety_gate


def pr(author="dependabot[bot]", base="main", head_repo="justcop/home-assistant-addons"):
    return {
        "user": {"login": author, "type": "Bot"},
        "base": {"ref": base, "repo": {"full_name": "justcop/home-assistant-addons"}},
        "head": {"repo": {"full_name": head_repo}},
    }


def change(filename="lastfm_analytics/requirements-dev.txt"):
    return [{"filename": filename, "status": "modified"}]


class PolicyTests(unittest.TestCase):
    def test_allows_exactly_one_test_patch(self):
        yes, _ = structural_eligibility(pr(), change(), "pytest==8.4.2\nPyYAML==6.0.2\n", "pytest==8.4.3\nPyYAML==6.0.2\n")
        self.assertTrue(yes)

    def test_rejects_all_runtime_changes(self):
        for path in ("vinyl_guardian/requirements.txt", "vinyl_guardian/Dockerfile",
                     "audioshelf/requirements.txt", "lastfm_analytics/requirements.txt",
                     ".github/workflows/vinyl-guardian-release.yml"):
            with self.subTest(path=path):
                self.assertFalse(structural_eligibility(pr(), change(path), "pytest==8.4.2", "pytest==8.4.3")[0])

    def test_rejects_major_minor_downgrade_and_multiple(self):
        for a, b in (
            ("pytest==8.4.2", "pytest==9.0.0"),
            ("pytest==8.4.2", "pytest==8.5.0"),
            ("pytest==8.4.3", "pytest==8.4.2"),
            ("pytest==8.4.2\nPyYAML==6.0.2", "pytest==8.4.3\nPyYAML==6.0.3"),
            ("pytest==8.4.2", "requests>=2.0"),
            ("pytest==8.4.2", "pytest==8.4.2; --extra"),
        ):
            with self.subTest(new=b):
                self.assertFalse(structural_eligibility(pr(), change(), a, b)[0])

    def test_rejects_author_fork_and_multiple_files(self):
        self.assertFalse(structural_eligibility(pr("stranger"), change(), "pytest==8.4.2", "pytest==8.4.3")[0])
        self.assertFalse(structural_eligibility(pr(head_repo="stranger/fork"), change(), "pytest==8.4.2", "pytest==8.4.3")[0])
        self.assertFalse(structural_eligibility(pr(), change() + change(), "pytest==8.4.2", "pytest==8.4.3")[0])

    def test_automerge_disabled_even_when_everything_else_passes(self):
        common = dict(structurally_eligible=True, ai_approved=True, required_checks_passed=True,
                      checks_current=True, branch_protected=True, conflict_free=True,
                      age_hours=49, merges_last_24h=0)
        self.assertFalse(merge_safety_gate(**common))
        self.assertTrue(merge_safety_gate(**common, automerge_enabled=True))
        for key, value in (("ai_approved", False), ("checks_current", False),
                           ("branch_protected", False), ("conflict_free", False),
                           ("age_hours", 47), ("merges_last_24h", 1)):
            with self.subTest(key=key):
                self.assertFalse(merge_safety_gate(**{**common, key: value}, automerge_enabled=True))


if __name__ == "__main__":
    unittest.main()
