# Using Money Locations

## First import

1. Install the add-on, set `web_password` in its Configuration tab, then start it and open its web interface. Ingress uses Home Assistant’s sidebar / Open web UI. Enter the same password on the app login screen.
2. In **Backups & settings**, select the separately supplied history JSON and choose **Import history**.
3. Check the latest financial position, account classifications and active account list.

The initial import includes account classifications, historical balances, aggregate contribution records and original displayed Summary values for audit. It preserves blanks and flags incomplete history as provisional. The app calculates future periods locally. It does not maintain a second editable Google Sheet.

## Monthly check-in

Choose **Monthly check-in**, set the actual snapshot date and start a check-in. Income and all activity cover the interval from the preceding final snapshot to this date, not an assumed calendar month.

Enter each balance, or choose **Unchanged** to explicitly confirm the previous value. Mortgages and credit-card debts are negative. Drafts save automatically after a short pause; the header shows whether saving succeeded. You may leave a draft incomplete and resume it later. Navigating away saves outstanding draft edits. Final snapshot edits are saved explicitly.

Expand an investment account to enter own-money contributions and withdrawals. For example, moving money from one investment account to another is a withdrawal at the source and a contribution at the destination. Moving cash into an investment requires the contribution on the investment account; cash growth comes from explicitly entered interest rather than cash balance movements.

For cash accounts, enter interest credited. Do not enter the same interest as ordinary income. For investment accounts, reinvested dividends and interest already inside the balance are part of growth, not own-money contributions. A distribution paid out should be entered as a withdrawal from the investment, including when the provider describes it as interest or a dividend.

Enter ordinary net income by source. Additional sources can be added in settings. All blank activity fields are treated as zero only when you confirm the completeness checkbox; until then results are provisional. Blank balances remain missing, not confirmed zeros. Finalising requires all required balances and the activity confirmation.

Use **Review period** to inspect the accounting breakdown, sanity warnings and missing entries. Savings and spending are inferred from the balance movement; the breakdown is therefore not an independent reconciliation check.

You can correct figures on an existing final snapshot without changing its date. If you need to move a final snapshot or insert a new snapshot between existing final periods, reopen the affected later snapshot first. Reopening a final snapshot also reopens every later final snapshot as a draft, preserving their data but requiring those periods to be reviewed and finalised again in chronological order.

## Pension relief and bonuses

- Enter your personal SIPP contribution as an own-money contribution against the SIPP.
- Enter the basic-rate top-up once, when it first enters the balance you are recording. If the provider includes pending relief in the displayed balance, record it then and do not add it again on settlement.
- Enter additional relief received from HMRC as **Additional pension tax benefit received**. This may be a cash refund or an identified pension-related benefit through a tax code. Ordinary net income must exclude that same amount, avoiding double counting.
- An entire tax refund is not necessarily pension relief. Record the identified pension-related amount.
- Expected future relief outside recorded balances is not included in net worth. It can be noted in the period notes. An automatic pending-relief ledger is not part of this version.
- LISA bonuses use the account relief/bonus field and also sit outside ordinary savings and investment growth.

These entries classify increases that are already in recorded balances. They never add money to a balance a second time.

## Mortgage and calculation conventions

This tracker uses the owner's investment-cost convention for mortgage interest:

1. Calculate each investment's gross return as closing balance minus opening balance minus contributions plus withdrawals minus relief/bonuses minus exceptional account capital changes.
2. Allocate mortgage interest in proportion to closing stocks and P2P balances. Cash, crypto and property receive no allocation. Amounts are rounded to pennies with a residual adjustment so costs sum exactly. If there is no eligible balance, show the entire mortgage cost as unallocated investment cost.
3. Net investment return = gross return minus mortgage interest.
4. Inferred savings from ordinary income = change in net financial assets minus net investment return minus tax benefits minus exceptional capital changes.
5. Inferred spending = ordinary income minus inferred savings.
6. Excluded payments reduce both displayed income and displayed spending by the same amount, leaving savings unchanged.

The source spreadsheet's original proportional allocation used closing stocks and P2P balances. Some later stock-profit formulas omitted their share of mortgage interest even though the overall return deducted the entire cost. This app consistently applies the intended allocation so grouped returns agree with the total.

Mortgage principal is calculated from the change in the mortgage balance, adjusted for any exceptional capital change recorded against that account. For straightforward repayments, enter the interest directly or use the helper: total payments minus reduction in debt. Borrowing, fees and capitalised interest need explicit treatment; the helper must not be used blindly for those periods.

Income saved into cash and investments and principal repaid are shown separately. Other savings is the total inferred savings figure less mortgage principal; it can also reflect other debt repayments and receivable changes. Ordinary account transfers are not overall capital changes.

Net financial assets deliberately include the mortgage as a negative balance even when the property value is excluded. This reflects the owner's treatment of the mortgage as financing held alongside the investment portfolio. Mortgage principal repayment therefore increases net financial assets just as adding the same amount to an investment account would.

## Exceptional changes

An account capital adjustment explains a balance movement outside contributions, growth and ordinary income, such as a receivable write-off. Positive adds value, negative removes value. For a new mortgage advance, the debt increase is negative capital on the mortgage, offset by positive capital in the receiving cash account. The pair does not change savings.

The overall capital-change field is for adjustments not already recorded against an account. Do not enter the same adjustment in both places. Historic legacy transfer amounts are preserved as reference data; the app does not assume they are gains, income or capital changes.

## Home value

Add dated valuations of your share of the property in settings. A snapshot uses the latest valuation on or before that date. There is no interpolation and no assumed historic property value. The default headline is **Net financial assets**, which includes the mortgage and excludes property value. Enabling home value shows **Total net worth including home** by adding the latest applicable property valuation. Revaluation is separate from savings. Update a valuation by saving another value on the same date.

## Account management

Inactive accounts remain in history. A previously non-zero account must still be closed with an explicit zero balance before it can disappear from a new check-in. Account names, notes and active status are editable. Type, wrapper and accessibility are locked once used in history, avoiding accidental restatement of past results. New accounts become part of newly created check-ins.

## Backups and recovery

The authoritative database is stored in `/share/money_locations/money.sqlite`. This dedicated shared folder survives add-on updates and uninstall/reinstall, including changing from a local development installation to a repository installation on the same Home Assistant host. The folder is private to the household; other add-ons with access to `/share` can also access it.

Version 0.2.0 automatically copies an existing `/data/money.sqlite` into this folder on first startup, preserving its revisions and recovery checkpoints. The old copy is left untouched for recovery. An existing shared database is never overwritten. Migration errors stop startup instead of silently starting with empty data. The startup log shows the database path.

**Include the Home Assistant `share` folder in your backups**, or export JSON regularly. An add-on-only backup of `/data` no longer contains the current database. A full backup with `share` does. Restoring an older add-on-only backup does not overwrite a newer shared database; use the app’s JSON restore to deliberately replace it.

In version 0.1.0, ordinary updates and restarts preserved `/data`, but uninstalling removed it. A change of add-on repository/slug also creates a different `/data` directory. If history has already disappeared, check the old installation’s recovery copies or restore its Home Assistant backup and export JSON; otherwise reimport the original history JSON. This release cannot recover a database already deleted without a backup.

Every successful write retains a copy of the preceding state inside the database. The app retains 40 non-draft checkpoints plus 10 draft checkpoints. These recovery copies protect against editing mistakes, but are on the same device: export a complete JSON backup or take Home Assistant backups for device-loss recovery.

**Complete backup (JSON)** includes all accounts, snapshot balances and activity, notes, source metadata and home valuations. It restores the whole current dataset. Recovery history itself is retained only in Home Assistant's database backup. **Balance history (CSV)** exports account balances and flows, not the complete dataset; use JSON for restoration.

To restore, download a recovery copy or use an exported JSON file. Select it under **Import or restore** and type `RESTORE` when replacing existing data. The current state is backed up before replacement. Files are validated before an atomic database update. A stale browser revision is rejected instead of overwriting another tab's changes.

## Current scope

GBP and a single household dataset. No bank connections, Google Drive synchronisation, statement imports, OCR or AI. Pension entitlement outside tracked accounts is not valued. Historical account-level returns cannot be recovered from aggregate category flows. Percentage returns currently use gross inferred return divided by opening balance as a sanity check; they are not Modified Dietz or another money-weighted performance measure. No API credentials are needed.

## Login and access

Set `web_password` in the add-on Configuration tab and restart. Passwords are verified with a salted PBKDF2 hash in memory; they are never stored in the financial dataset or exports. Sessions expire after 12 hours, and restarting or changing the password logs everybody out. Five failed attempts temporarily block further logins. Password login protects both Ingress and direct access, including exports and recovery copies. Without a password, production shows configuration instructions and provides no financial API access.

Ingress stays on port 8099 and accepts only the Home Assistant ingress proxy. For optional direct LAN access, enable the separate **8100/tcp** network port in Home Assistant. It is disabled by default, and its listener starts only when a password is configured. Direct HTTP should be used on a trusted LAN; use HTTPS through Home Assistant Ingress or an HTTPS reverse proxy for remote access. No port forwarding is required for Ingress.
