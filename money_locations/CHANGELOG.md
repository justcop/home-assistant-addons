# 0.2.0

- Add square icon and wide Home Assistant logo, plus matching app branding.
- Add password login for Ingress and optional direct access on separate port 8100.
- Move authoritative SQLite storage to /share/money_locations so reinstalling preserves history.
- Automatically migrate an existing /data database without overwriting shared data.
- Document backup requirements and recovery of history from older installations.

# Changelog

## 0.1.0

- Initial local financial tracker with monthly snapshots, editable history and account classifications.
- Per-account flows and grouped returns with mortgage interest allocated as investment cost.
- Separate pension tax benefits, excluded payments and exceptional capital changes.
- Optional dated home valuations, responsive dashboard and reconciliations.
- Transactional storage, draft saving, recovery copies and JSON/CSV exports.
- Private historical import converter and synthetic regression tests.
