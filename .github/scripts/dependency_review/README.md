# Dependabot AI reviewer

**Status: advisory-only; automatic merging is NOT implemented or enabled.**

The trusted-base GitHub Actions workflow `.github/workflows/dependabot-ai-reviewer.yml`
runs on new or updated Dependabot pull requests. It reads PR metadata, the
changed-file diff, and current check-run summaries using the GitHub API,
sends a bounded evidence packet to the OpenAI Responses API, and writes or
updates one advisory comment per PR. It never checks out PR code, runs PR
scripts with API secrets, or merges anything.

## Setup: keyless GitHub Actions workload identity federation

No permanent OpenAI API key or GitHub Actions secret is required.
Create an OpenAI Platform project and a dedicated service account with
permission for model requests. Then in **Organization Settings → Security →
Workload Identity Provider**, configure:

- Provider name: `GitHub Dependabot Reviewer`
- OIDC issuer: `https://token.actions.githubusercontent.com`
- Audience: `https://api.openai.com/v1`
- Use uploaded JWKS: **off** (GitHub OIDC discovery)

Create a **service account mapping** for that provider using the exact
key/value restrictions below. These are assertions on the signed GitHub
OIDC JWT, not shell environment variables:

| Claim key | Required value |
| --- | --- |
| `iss` | `https://token.actions.githubusercontent.com` |
| `aud` | `https://api.openai.com/v1` |
| `repository` | `justcop/home-assistant-addons` |
| `ref` | `refs/heads/main` |
| `workflow_ref` | `justcop/home-assistant-addons/.github/workflows/dependabot-ai-reviewer.yml@refs/heads/main` |

Map these claims to the dedicated service account in the chosen project.
Set the mapping's API permissions to **model requests only**
(`api.model.request`, where offered). Check that the service account's
project permissions are no broader than necessary.

In **GitHub repository Settings → Secrets and variables → Actions →
Variables**, create these **three repository variables** (not secrets):

| Variable | Value |
| --- | --- |
| `OPENAI_WIF_AUDIENCE` | `https://api.openai.com/v1` |
| `OPENAI_IDENTITY_PROVIDER_ID` | the actual provider ID from OpenAI Platform |
| `OPENAI_SERVICE_ACCOUNT_ID` | the actual mapped service account ID |

Do not use display names in place of IDs. The workflow requests a GitHub
OIDC JWT via `id-token: write`, then POSTs the JWT to
`https://auth.openai.com/oauth/token` with OAuth token-exchange parameters.
OpenAI validates its signature, audience and **all** mapping assertions
before returning a short-lived bearer token for the Responses API.
The GitHub OIDC JWT and exchanged token are never stored in GitHub secrets
or deliberately logged. Neither is bound to a device, so a leaked temporary
bearer token must still be treated as sensitive.

After this pull request is merged and the mapping/variables are configured,
manually dispatch **Dependabot AI review (no auto-merge)** with an existing
Dependabot PR number to test it. Previous PRs are not backfilled automatically.
The workflow stays advisory-only. Missing configuration safely skips review;
an identity exchange failure fails the job without authorising any merge.

The `pull_request_target` job uses **only trusted default-branch code**,
never PR-head code. The GitHub token has `contents: read`,
`checks: read`, `pull-requests: write` (comments) and `id-token: write`.
No GitHub credential is sent to OpenAI. PR descriptions, patches and
changelogs are untrusted model inputs. The model has no GitHub tools.
An absent review is **not approval**. OpenAI API usage is billed
separately from ChatGPT.

The model is configured using `AI_REVIEW_MODEL` in the workflow. If the
model, GitHub OIDC endpoint or OpenAI token exchange is unavailable, the
review fails closed and never merges. The AI cannot independently verify
all security vulnerabilities or live application behavior.

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
