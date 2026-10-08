# Changelog

## 0.5.0

- Extend LifeStage imports with saved sessions and use the previously verified MHCT2 login protocol.
- Migrate existing 0.4.0 provider records without deleting or replacing the original tables.
- Support optional lifestage_email and lifestage_password add-on settings.
- Persist sessions separately from financial exports, with reconnect and disconnect controls.
- Pull accounts and transactions atomically, refresh records by provider ID and retain source data.
- Add optional daily sync, transaction search, explicit account mapping and balance sign controls.
- Populate a new draft from mapped GBP balances, retaining source dates and requiring confirmation.
- Include imported records/mappings in complete backup and restore while preserving snapshot-only imports.

## 0.4.0

- Add an experimental read-only LifeStage / former Moneyhub connector using the site's native HTTP login and TOTP flow, without browser automation.
- Pull active accounts, all accounts and dated transactions into dedicated SQLite tables without changing snapshot accounting.
- Keep LifeStage passwords, TOTP codes, login challenges and CSRF session tokens in memory only; persist only email, tenant ID and device ID.
- Upsert imported accounts and transactions by provider UID so repeated pulls are idempotent and provider edits replace the stored raw record.
- Show pull status, stored counts and a recent-transaction preview in Backups & settings.
- Add an explicit LifeStage session disconnect and HTTPS CA certificates to the add-on image.
- Add deterministic authentication-derivation and raw-import regression tests.


## 0.3.0

- Replace the algebraic reconciliation presentation with an explicit period breakdown and clearly label savings and spending as inferred.
- Keep mortgage debt in net financial assets while property value remains optional, matching the investment-leverage accounting convention.
- Add 30-day-normalised savings, spending and net-return comparisons for uneven snapshot intervals.
- Add simple opening-balance return percentages and warnings for implausible inferred investment returns and negative inferred spending.
- Add safe reopening of final snapshots; reopening a historical period also reopens downstream final periods so their flows can be reviewed.
- Validate account wrapper and accessibility classifications on imports and saves.
- Authenticate protected POST requests before reading large bodies, cap login bodies, and avoid holding the auth lock during PBKDF2.
- Close SQLite connections explicitly, keep health checks independent of Ingress/data reads, and avoid loading the database for static files.
- Remove personal income-source defaults from the public app template and harden CSV string output.

## 0.2.0

- Add square icon and wide Home Assistant logo, plus matching app branding.
- Add password login for Ingress and optional direct access on separate port 8100.
- Move authoritative SQLite storage to /share/money_locations so reinstalling preserves history.
- Automatically migrate an existing /data database without overwriting shared data.
- Document backup requirements and recovery of history from older installations.

## 0.1.0

- Initial local financial tracker with monthly snapshots, editable history and account classifications.
- Per-account flows and grouped returns with mortgage interest allocated as investment cost.
- Separate pension tax benefits, excluded payments and exceptional capital changes.
- Optional dated home valuations and responsive dashboard.
- Transactional storage, draft saving, recovery copies and JSON/CSV exports.
- Private historical import converter and synthetic regression tests.
