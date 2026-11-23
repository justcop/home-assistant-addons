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

## GitHub-managed delivery

- `delivery/contract.py` is the shared fail-closed classifier used by images, CI and My Add-on Updater. `delivery/apps.json` describes verified runtime layouts. Do not bypass image compatibility checks.
- Code-only changes do not need a config version bump. The shared publisher increments versions automatically when build inputs, startup, permissions or persistence boundaries change. Do not manually edit `delivery/releases.json`.
- Every supported architecture must publish successfully before the new version is advertised to Supervisor. The publisher serializes release jobs and reads the committed version state before selecting the next version.
- Application Python, templates and static files may use compatible code deployments. The central updater stages checksummed runtime files on `/media/storage/addon-code`, atomically selects a revision and restarts the application. It does not rebuild an image or change installed options.
- Changes to protected/schema-bearing modules require an image and appropriate explicit migration safeguards. A code revert must never be treated as a database rollback.
- Use the My Add-ons interface to check status, deploy a compatible commit or recover an interrupted transaction. The legacy Development Manager is being retired; verify each installation's migration status and do not recreate local source-based installations.

## File placement and backup design

GitHub is the source of truth for application code. Keep persistent user data independent of replaceable code so installing an image, deploying a commit, or reverting compatible code cannot overwrite or delete it. Apply these rules to new features and changes to existing storage; do not reorganise unrelated storage.

| File type | Intended location | Recovery / backup |
| --- | --- | --- |
| Source, shipped templates/static assets and bundled artwork | GitHub and the built image; compatible runtime deployments under `/media/storage/addon-code` | Recreate from GitHub/prebuilt images. Avoid duplicate source trees in HA backups. |
| Small irreplaceable settings, calibration, credentials and application state | Existing app `/data`, or an explicitly documented backed-up app configuration/shared location | Include in the relevant HA backup, except documented app-specific exclusions. Keep private files out of GitHub and publicly served paths. |
| Databases and user uploads/artwork/media | Preserve the app's established location; large data belongs in an app-specific directory on the verified STORAGE filesystem, normally `/media/storage` | Irreplaceable even when externally stored. Document and verify a recovery route separately when normal HA backups exclude that volume. |
| Regenerable caches, thumbnails, downloads and temporary build/update files | Large retained caches on external STORAGE; short-lived scratch in temporary storage | Exclude where safely separable, bound retention and clean up after success. A file is disposable only if it can actually be recreated. |
| Updater transaction/recovery state | My Add-on Updater's `/data` | Keep in HA backups. Runtime code payloads are recreatable; this operational state must survive restarts. |

- Distinguish shipped assets from user-created files even if both are called artwork or static content. Never write uploads, databases, credentials or calibration into a runtime deployment tree.
- Choose paths using the actual container mappings and verified mount, not the directory name alone. For external storage, fail clearly if the drive is absent; do not silently create an internal fallback or move large data onto HA's internal disk. Preserve the drive's existing Media/Share usage.
- Backup coverage follows the actual path, mappings, selected backup folders and `backup_exclude` rules. A mapped directory or a small/zero-byte add-on backup does not prove external data is covered. The owner's `/share` is included in HA backups; Media is excluded in the current setup. Verify coverage when changing paths.
- Keep restorable source, dependencies, build output and regenerable caches out of backed-up state where safely separable. Do not use broad exclusions that also remove settings or user content. Document each changed persistent path, whether it is irreplaceable, its backup owner and how to restore it in the affected app's existing documentation; no separate inventory system is required.
- WhatsApp has an intentional exception: its live archive is on verified local external STORAGE and excluded from routine HA backups. Preserve its independent encrypted Google Drive database backup: replace only after successful upload and verification, then delete the previous managed version; keep the previous verified copy on failure and report failures in the app and HA notifications. Do not generalise this one-version policy to other apps or imply the database backup restores linked-device credentials/media. Honour the app's explicit secret/backup exclusions.

## Designing for fast updates and safe migrations

- Read `delivery/README.md`, `delivery/contract.py` and the affected entry in `delivery/apps.json` before changing delivery or runtime layout. These define actual compatibility; file extensions alone do not. Keep ordinary UI and application behaviour separate from startup, dependencies and persistence code where practical, without unrelated refactors.
- Compatible changes inside declared runtime paths use the existing central updater and shared image launcher. They need no image/version bump; currently they still restart the app and frontend tabs may need reloading. Do not add a competing per-app Git pull updater, full repository checkout, dependency installation or build step to this path.
- Dependency/lockfile, base image, OS package, startup wrapper/command, permissions/mappings, config schema, runtime layout, shared delivery contract and protected/schema-bearing module changes require a standard versioned image update through HA. Unknown build inputs fail closed. Never remove protection, move migration code or weaken classification merely to make a change eligible for fast deployment.
- A code deployment may replace/remove only declared application files. Keep state outside its destination and file cleanup scope, including apps whose existing runtime overlay is broad. Validate compatibility and paths before activation; preserve checksum checks, atomic revision selection, readiness/identity checks and interrupted-update recovery. Retain only the active runtime payload after successful verification, using the existing mechanism.
- Earlier compatible GitHub commits provide code recovery without an elaborate parallel rollback system. Database/path migrations need an explicit migration procedure and usable data recovery point; a code revert is never a database rollback.
- Repository implementation, image publication and live installation are different states. Inspect the affected installed app before claiming it has migrated or can accept fast updates. During migration, preserve options, credentials, databases, calibration, user content, external mounts and integrations; preserve installation identity where possible. If a slug changes, explicitly migrate private data/options and verify the replacement before retiring the original. Do not edit Supervisor internals or re-enable legacy automatic deployments.
- For relevant changes, verify that restart/image replacement/compatible code recovery preserve user data, that missing external storage fails safely, and that backup/restore coverage remains correct. Use focused existing checks and proportionate live verification; report anything not verified.

## GitHub Actions defaults

- Run ordinary push validation only on main; use pull_request for development branches so one commit does not create duplicate push and PR runs.
- Filter by affected paths, with shared paths included when necessary.
- Cache when useful, but prefer simple, understandable workflows over additional infrastructure.
- Use full cross-architecture checks only for affected runtime/container changes, or when explicitly dispatched.
