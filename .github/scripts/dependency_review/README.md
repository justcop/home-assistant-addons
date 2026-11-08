# Dependabot AI reviewer

**Status: advisory-only; automatic merging is NOT implemented or enabled.**

The trusted-base GitHub Actions workflow `.github/workflows/dependabot-ai-reviewer.yml`
runs on new or updated Dependabot pull requests. It reads PR metadata, the
changed-file diff, and current check-run summaries using the GitHub API,
sends a bounded evidence packet to the OpenAI Responses API, and writes or
updates one advisory comment per PR. It never checks out PR code, runs PR
scripts with API secrets, or merges anything.

## Setup

1. In repository Settings → Secrets and variables → Actions, add
   **repository secret `OPENAI_API_KEY`** using your own OpenAI API key.
   API billing is separate from ChatGPT. Do not put the key in code.
2. After merging the workflow, manually dispatch **Dependabot AI review
   (no auto-merge)** with a Dependabot PR number to test it, or wait for a
   newly opened / updated Dependabot PR. Previous PRs are not backfilled
   automatically.
3. Read the review comment. It distinguishes evidence from uncertainty.
   Use the relevant app's CI and manual checks before merging.
4. If the API key is absent, the workflow exits successfully without
   posting a review. An absent review means **no approval**.

The workflow's `pull_request_target` event executes **only trusted
default-branch code**. It never checks out the PR head. PR descriptions,
patches and changelogs are untrusted model inputs. The GitHub token has
`contents: read` and `pull-requests: write` only; the model has no GitHub
tool privileges. No GitHub secrets are sent to OpenAI.

The AI model is configured in the workflow's `AI_REVIEW_MODEL` variable.
If the model or API is unavailable, the review fails closed; it never
marks a PR safe or merges it. This is a risk-oriented advisory, not an
independent live vulnerability scan or a substitute for tests.

## Fixed policy

The deterministic rules in `policy.py` are *not* controlled by the AI.
Only one patch-level pinned dependency update in
`lastfm_analytics/requirements-dev.txt`, with no other file edits,
could ever be a structural auto-merge candidate. Runtime dependencies,
Vinyl Guardian, Dockerfiles, GitHub Actions, Gradle, grouped updates,
minor/major changes, and security updates requiring judgment are
manual-only.

`merge_safety_gate` also requires:
- actual owner-enabled automation (default False);
- explicit AI approval;
- required CI checks passing on the current exact head SHA;
- verified main branch protection with no bypass;
- no merge conflict;
- at least 48 hours since opening;
- fewer than one automated merges in the previous 24 hours.

**These dynamic checks are a policy specification, not an implemented
merge service.** The repository contains no merge-capable automation
workflow and no auto-merge credentials. Turning on auto-merging would
require a *separate reviewed pull request*, protected branches with
mandatory check requirements, robust SHA/rate-limit verification and
a credential restricted to the necessary repository permissions.

Test locally using:

```bash
python -m unittest discover -s .github/scripts/dependency_review -p 'test_*.py' -v
```

## Caveats

The AI receives release notes from Dependabot's PR body, snippets of
changed-file patches, and test-status summaries, but does not
automatically browse the entire upstream changelog or analyze every
source file. It must explicitly identify unknowns. Review comments
are not GitHub review approvals.

The reviewer deliberately does not add labels, mutate PR branches,
trigger publishing, or restart Home Assistant. Never add PR-head
checkout or a write-capable repository token to the same privileged
workflow.
