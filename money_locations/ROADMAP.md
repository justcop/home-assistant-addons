# Planned features

## Trading 212 data pull

Pull Trading 212 account and investment data into Money Locations. Aim to import account values and contributions/withdrawals, map them to local accounts, and populate snapshot drafts for review, following the LifeStage import workflow.

Before implementation, check the available API and export formats, account coverage (including ISA accounts), cash and investment valuation coverage, currency handling and transaction history. Use stable provider identifiers for repeat imports and include imported records in complete backups. Keep account review explicit before finalising a snapshot.
