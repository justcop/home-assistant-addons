# Fresh pairing / full history experiment (24 November 2026)

**Status: isolated source experiment, NOT deployed or verified against a live phone.**
Baseline: `backup/whatsapp-before-fresh-full-sync-2026-11-24` at
`4d1deb44e46dec01e9665f6180d77979544b8175`.

## Hypothesis

The pinned upstream WhatsApp API creates a Baileys socket with
`Browsers.macOS("Chrome")`, and this repository already enables
`syncFullHistory: true`. Reusing an authenticated session does not
renegotiate the initial history. Baileys recommends
`Browsers.macOS("Desktop")` with `syncFullHistory: true` for
additional initial history, but this pinned pairing-code path rejects the
unrecognized Desktop companion label with IQ 400. The experiment changes
**only** the browser label to the canonical macOS Safari profile and keeps
`syncFullHistory: true`. Safari pairing-code acceptance and actual
historical coverage remain live-test hypotheses, not guarantees.

## Required safeguards before live testing

1. **Do not deploy this branch onto the currently running add-on's existing
   `/data` or `storage_path`.** Its database contains pairing credentials,
   live message records, deletion markers, and suppression state; deleting
   or replacing it would defeat the preservation goal.
2. Stop the existing WhatsApp add-on first. Preserve its current image/version,
   SQLite database including WAL/SHM (via a consistent SQLite backup or
   offline filesystem copy), pairing/auth state, history-backfill checkpoints,
   deletion/suppression state, and encryption/Google Drive backup settings.
   Verify the backups restore, not merely that files exist. A Git branch
   preserves none of this runtime data.
3. Use a **separate isolated experimental installation/data directory** with
   a fresh auth store and new database. Never point it at the production
   external archive. Do not run two linked writers against one database.
   The existing Home Assistant add-on slug and GHCR image reference are
   shared, so this branch is not itself a parallel-installable add-on.
   A separate experimental slug/image or an explicit offline restore/
   cutover is required before a live test.
4. Pair with the phone-number pairing code issued by that new installation.
   Do not revoke/unlink the original WhatsApp device until it is no longer
   required for recovery. Codes are short-lived credentials; don't paste
   them into issues or CI logs.
5. Let full initial history finish before starting the on-demand backfill.
   This branch explicitly disables the latter for measurement.
6. Compare aggregate direct-chat counts, oldest timestamps, and sync event
   totals against the baseline *without logging message bodies or chat IDs*.
   Do not infer completion from a short page, an empty on-demand reply, or
   a stale history checkpoint.
7. If login rejects the macOS Safari profile, stop the experiment. Do not
   silently revert to the Chrome profile or clear production authentication
   state. An alternate QR-path Desktop test is a **separate** experiment.
8. Only after confirming stable pairing, history persistence, absence of
   duplicates, correct deletion suppression, and no regressions should
   data be merged. Use ID-based upsert, preserving existing tombstones and
   deletion suppression. No blanket overwrite from new history.

## Rollback

Stop the isolated experiment and resume the unchanged production installation
and preserved runtime data. The recovery branch restores code only; restoring
pairing credentials and the SQLite archive requires the runtime backup.
No automatic deployment, no automatic unlinking, and no automatic import.

## Static verification

- Build-time patch must find the pinned upstream browser comment exactly once.
- The generated socket configuration must contain
  `browser: Browsers.macOS("Safari")` and `syncFullHistory: true`.
- Pairing remains via `requestPairingCode`, with the existing read-only,
  archive privacy and suppression patches unchanged.
- Initial on-demand history backfill is disabled in the experimental build.

This design intentionally avoids claiming that a successful build or new
pairing proves a complete WhatsApp archive.
