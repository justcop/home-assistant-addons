# Working agreement for this personal Home Assistant add-on repository

These add-ons are primarily used by one person in their own Home Assistant installation. Optimise for *working changes and short feedback loops*, not enterprise release ceremony.

## Normal delivery workflow

- The owner does **not** use a separate staging Home Assistant instance, persistent testing branches or an ongoing development deployment. Normal delivery is: request a change, implement it in a short-lived PR branch, run proportionate checks, then merge the PR to `main` without asking the owner to test a branch.
- Treat `main` as the live add-on source. Full checks that happen *only after* a merge cannot protect against a broken release. Verify relevant high-risk functionality in the PR before merging; for a small cosmetic change, syntax and focused checks are sufficient.
- Handle routine PR creation, checks and merge end-to-end. Report genuine failures and fix them rather than handing off routine GitHub steps.
- Do not make persistent testing branches, additional staging releases, or another approval step the default.

## When modifying an add-on

- Change only the add-on and functionality requested; avoid unrelated refactors.
- Do the fastest meaningful verification first: syntax, focused unit tests and, for UI changes, a focused real-browser check when available.
- Fix real bugs and meaningful failing tests. For obsolete, flaky or over-specific tests, update the test to validate the intended behaviour rather than repeatedly changing correct application code to satisfy an old expectation.
- For authentication, account isolation, secrets, permission boundaries, database migrations, backups, critical startup or container architecture changes, run relevant comprehensive tests before delivery.
- Ordinary PRs should have quick checks. Full browser suites, all-backend tests, container image builds and cross-architecture validation belong on main, manual dispatch or only the affected runtime change.
- Avoid re-running expensive checks merely because several small commits were pushed; group coherent changes and cancel superseded, non-publishing PR checks.
- Do not introduce new workflows, inventory hashes, mandatory review notes, release steps or broad test matrices without a demonstrated benefit. First simplify or reuse existing checks.
- Do not disable a safety check just to make a genuinely broken implementation pass. Explain meaningful residual risks.
- Keep version bumping and add-on publishing limited to the add-ons that actually changed. Never cancel an in-progress publishing job automatically.
- Before finishing, report what was changed, what was actually verified, any failures, and whether the change is only in a PR or has reached main.

## Fast Home Assistant development deployments

- The Add-on Development Manager follows `main` and deploys changed add-on source independently of GitHub Actions. GitHub Actions are checks, not a gate for local deployment.
- For routine development changes to app Python, JavaScript, CSS or assets, **do not bump `config.yaml`'s version or edit Supervisor build metadata solely to mark the commit**. The Development Manager's Git SHA and history are the authoritative development version and rollback target.
- A changed app definition, declared version, root build metadata, translations or AppArmor policy forces a slower global Supervisor store refresh. Bump versions and generated release metadata for actual releases, not cosmetic previews.
- When diagnosing perceived deployment lag, check the manager's deployed commit and phases first; distinguish its polling, store refresh, local Docker build and app restart from independent CI jobs.
- Code-only changes still require a container rebuild in the current manager; do not claim they are hot-reloaded until an app-specific, verified asset or process-reload mechanism exists.

## GitHub Actions defaults

- Run ordinary push validation only on main; use pull_request for development branches so one commit does not create duplicate push and PR runs.
- Filter by affected paths, with shared paths included when necessary.
- Cache when useful, but prefer simple, understandable workflows over additional infrastructure.
- Use full cross-architecture checks only for affected runtime/container changes, or when explicitly dispatched.
