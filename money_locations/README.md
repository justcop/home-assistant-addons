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
- Optional Moneyhub / WPS LifeStage login with 2FA, daily or manual data pulls, account mapping and transaction search.
- Review imported balances in a new draft without changing historical snapshots.
- SQLite persistence in `/share/money_locations`, recovery copies, complete JSON export/restore and CSV balance export.
- Home Assistant Ingress and optional Google sign-in through Cloudflare Access for the standalone web interface (or an optional standalone password). Only the configured Google account can view financial data. LifeStage connects using your website login; no paid API account is required.

## Install

Add this repository in the Home Assistant add-on store, refresh the store, then install **Money Locations**. Start it and choose **Open Web UI**; Home Assistant ingress uses your Home Assistant login, without a second password. For direct web access, configure Cloudflare Access Google sign-in as described below (preferred) or set `web_password`.

Open **Backups & settings**, choose your private Money Locations history JSON and press **Import history**. Financial data is deliberately not bundled in the repository. Alternatively, add accounts and create an opening snapshot manually.

See [DOCS.md](DOCS.md) for entry conventions, accounting details and recovery.

## Google sign-in for standalone access

Money Locations uses the same Cloudflare Access Google identity provider as AudioShelf and Listening; this is not a separate Google OAuth client. Create a **separate self-hosted Access application** for the Money Locations hostname (for example, `money.justcop.co.uk`), with the existing Google IdP, an Allow policy restricted to your Google email, and automatic redirect to Google. Protect the hostname with Cloudflare Access and route it over HTTPS to the add-on's **direct port 8100**, not the Home Assistant ingress port.

Set these add-on options and restart:

- `cloudflare_team_domain`: the same team as AudioShelf/Listening (for example, `justcop.cloudflareaccess.com`)
- `cloudflare_audience`: **Money Locations' own** Access application AUD, not AudioShelf's or Listening's
- `cloudflare_owner_email`: the sole approved Google address

All three options must be configured together. The app verifies Cloudflare's signed JWT (RS256 signature, issuer, audience, expiry, human application identity, owner email) on **every standalone request**, including static assets. Cloudflare mode ignores any previously issued password sessions. Use the **Sign out** control to revoke your Cloudflare Access session. Home Assistant ingress continues using your Home Assistant authentication. Do not expose the direct add-on port directly to the Internet: use Cloudflare Access and HTTPS. The legacy `web_password` applies only if Cloudflare mode is off.

## Development and tests

The app uses PyJWT with cryptographic verification (installed by the add-on image); no frontend build step is required.

```sh
python -m unittest discover -s money_locations/tests -v
MONEY_LOCAL=1 MONEY_DATA=/tmp/money-locations python money_locations/app/server.py
```

Local development listens only on `127.0.0.1:8099`. In Home Assistant the Ingress service is on port 8099; optional direct access uses a separate password-protected port 8100 and is disabled by default. Tests use synthetic financial examples.

Include the Home Assistant `share` folder in backups. See [DOCS.md](DOCS.md) for migration and recovery details.
