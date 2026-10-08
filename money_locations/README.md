# Money Locations

A local financial tracker for Home Assistant. Record dated account balances and the activity since the previous snapshot, then see net financial assets, inferred savings and spending, and investment returns.

## Features

- Overview with date-scaled balance trends, asset groups, ISA totals and optional home valuations.
- Net financial assets include mortgages and other liabilities while excluding the property value unless home is explicitly included.
- Monthly or ad-hoc check-ins with automatic draft saving and explicit balance confirmation.
- Contributions and withdrawals per investment account; interest per cash account.
- SIPP relief, LISA bonuses and additional HMRC pension benefits separated from ordinary savings and growth.
- Mortgage interest treated as an investment financing cost, allocated proportionally across closing stocks and P2P balances. Mortgage principal repayment contributes to savings.
- Inferred savings and spending are labelled as such, with sanity warnings for implausible inferred investment returns and negative inferred spending.
- Uneven snapshot intervals are normalised to 30 days in comparative history and longer-view averages.
- Safe historical editing: existing final figures can be corrected, while inserting or moving historical snapshots requires affected later snapshots to be reopened and reviewed.
- Historical category-level contributions, without inventing individual account returns.
- SQLite persistence in `/share/money_locations`, recovery copies, complete JSON export/restore and CSV balance export.
- Home Assistant Ingress and optional password-protected direct access. No Google credentials, AI service or external finance API is required.

## Install

Add this repository in the Home Assistant add-on store, refresh the store, then install **Money Locations**. Set `web_password` in Configuration, start it and choose **Open Web UI**.

Open **Backups & settings**, choose your private Money Locations history JSON and press **Import history**. Financial data is deliberately not bundled in the repository. Alternatively, add accounts and create an opening snapshot manually.

See [DOCS.md](DOCS.md) for entry conventions, accounting details and recovery.

## Development and tests

No Python packages or frontend build step are required.

```sh
python -m unittest discover -s money_locations/tests -v
MONEY_LOCAL=1 MONEY_DATA=/tmp/money-locations python money_locations/app/server.py
```

Local development listens only on `127.0.0.1:8099`. In Home Assistant the Ingress service is on port 8099; optional direct access uses a separate password-protected port 8100 and is disabled by default. Tests use synthetic financial examples.

Include the Home Assistant `share` folder in backups. See [DOCS.md](DOCS.md) for migration and recovery details.
