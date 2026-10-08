# Money Locations

A local financial tracker for Home Assistant. Record a snapshot of account balances and the activity since your previous snapshot, then see savings, spending and investment returns with an explicit reconciliation.

## Features

- Overview with balance trends, asset groups, ISA totals and optional home valuations.
- Monthly check-in with automatic draft saving and explicit balance confirmation.
- Contributions and withdrawals per investment account; interest per cash account.
- SIPP relief, LISA bonuses and additional HMRC pension benefits separated from ordinary savings and growth.
- Mortgage interest treated as an investment cost, allocated proportionally across closing stocks and P2P balances. Mortgage principal contributes to savings.
- Editable past snapshots with automatically recalculated later results.
- Historical category-level contributions, without inventing individual account returns.
- SQLite persistence, recovery copies, complete JSON export/restore and CSV balance export.
- Home Assistant Ingress and optional password-protected direct access. No Google credentials, AI service or external finance API is required.

## Install

Add this repository in the Home Assistant add-on store, refresh the store, then install **Money Locations**. Set `web_password` in Configuration, start it and choose **Open Web UI**.

For a branch build, add the repository URL with `#fix/money-login-storage` appended, or use the `fix/money-login-storage` branch and the `money_locations` directory in your development manager.

Open **Backups & settings**, choose your private `Money-Locations-history.json` file and press **Import history**. Financial data is deliberately not bundled in the repository. Alternatively, add accounts and create an opening snapshot manually.

See [DOCS.md](DOCS.md) for entry conventions, accounting details and recovery.

## Development and tests

No Python packages or frontend build step are required.

```sh
python -m unittest discover -s money_locations/tests -v
MONEY_LOCAL=1 MONEY_DATA=/tmp/money-locations python money_locations/app/server.py
```

Local development listens only on `127.0.0.1:8099`. In Home Assistant the service accepts only the Ingress proxy at `172.30.32.2`; optional direct access uses a separate port 8100, disabled by default. Tests use synthetic financial examples.

The repository contains no CI workflow for this app. Home Assistant builds the small image locally.

Set `web_password` before starting. Open through Home Assistant Ingress; optional direct access uses a separately enabled 8100/tcp port. Include `share` in Home Assistant backups. See [DOCS.md](DOCS.md) for migration and recovery details.
