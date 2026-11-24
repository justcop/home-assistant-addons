# WhatsApp Full History Trial (independent Home Assistant add-on)

**Experimental, privacy sensitive. Does not replace the production WhatsApp MCP installation.**

This image is based on the pinned, published WhatsApp MCP 0.3.32 image,\nincluding the admin-ingress pairing banner, code display, connection status, and\nSettings re-pair control. Pairing status hides after successful linking.
This version now keeps the **same Safari companion identity** (\`Browsers.macOS("Safari")\`) through QR pairing **and** authenticated reconnects. The previous experiment using Windows Desktop (\`WIN_HYBRID\`) **after** Safari pairing was rejected repeatedly with \`Connection Terminated\`, despite WhatsApp accepting the pairing. Do not automatically change identity again or delete the trial credentials.

The existing experimental archive and linked-device credentials are preserved by updating the image in place. The main pairing UI stays visible when disconnected, reports whether WhatsApp actually issued a QR and provides a manual retry action if needed.

**Known limitation:** The history delivered to Safari companions appears to stop around 30 October 2025, and full-history retrieval older than that remains unproven. This change restores the functional WhatsApp connection; it does not claim to bypass that history boundary.

## One-time archive transfer to production

The private **GET /api/trial/export** endpoint reports whether a content-only
export exists. The private **POST /api/trial/export** endpoint requires the
header `X-WhatsApp-Export-Action: 1` and JSON
`{"confirm":"EXPORT TRIAL CONTENT ONLY"}`. No public MCP route exposes this.

It exports only **chats, contacts, messages, reactions and hashed suppression
records**, never WhatsApp authentication, API tokens or backup keys. The
snapshot is a sensitive **plaintext SQLite file** in a 0700 directory on
`/share` and must be removed after verified consolidation. It must never be
uploaded to GitHub or a public service. Production's merge controller imports
unique records into a *staged copy* of its external STORAGE archive and keeps
the original as a short-lived rollback copy. Do not delete or uninstall the
trial before the new production archive is verified. Nothing automatically
merges on trial start.

## Isolation contract
- Unique Home Assistant app slug `whatsapp_fullsync_trial`; independent
  /data/whatsapp SQLite and Baileys authentication database.
- No /media (STORAGE) mount. The trial keeps its own database in private /data;
  a temporary /share mapping permits an explicitly initiated content-only SQLite
  export into /share/whatsapp-history-transfer. Production's private STORAGE
  archive and auth remain inaccessible to the trial. Remove this mount after
  consolidation to restore strict isolation.
- No host-mapped ports; read-only archive accessible through admin ingress only.
- Cloudflare MCP gateway is disabled without Cloudflare Access credentials.
- Per-conversation recovery is paused until explicitly enabled in the private History sync UI, and automatically yields while new FULL history is being delivered.
- Never unlink the production WhatsApp device just to pair this second one.
- Keep the trial stopped after testing, since it consumes a linked-device slot.

## Clean-slate Windows Desktop (WIN_HYBRID) comparison

When deliberately resetting the experiment, **uninstall only** the Home Assistant
app `64b28acc_whatsapp_fullsync_trial`, then reinstall it from this repository.
Uninstall discards the trial's `/data/whatsapp` archive, its history checkpoints
and its QR-linked credentials. Verify the archive is empty before pairing, and
never uninstall `64b28acc_whatsapp_mcp` or clear the production STORAGE volume.
If the old trial linked device remains listed on the phone, remove **only** that
old trial session to free a linked-device slot.

The key success criterion is an imported human message dated **earlier than
30 October 2025**. Compare earliest timestamps and unique-message counts after
initial FULL delivery. This test cannot prove WhatsApp will provide a complete
lifetime archive, even with a desktop identity. Leave individual-chat recovery
paused until bulk history has settled so it doesn't confound the comparison.

## Live test
1. Confirm the production WhatsApp app is running and its encrypted Google Drive
   recovery backup is current, and retain its source/rollback branch.
2. Install **WhatsApp Full History Trial**, not WhatsApp MCP (Read Only).
   For phone-code pairing only, set the number to international digits without a leading +.
   Start the trial app; scan the QR from its private Home Assistant pairing UI (or change pairing mode to phone code in Settings).
3. On the phone use WhatsApp > Settings > Linked devices > Link a device,
   then scan the trial QR (or choose Link with phone number for code mode).
4. Keep the production app untouched. Wait for the initial full history
   sync to settle. Compare message totals/oldest timestamps on the trial
   versus production (including known incomplete direct chats).
5. Do not merge archives blindly: original deletion suppression, tombstones,
   and privacy contracts must be honoured in any future import.
6. Roll back by stopping/uninstalling **only** the trial app. This does not
   restore or change production credentials and archive.

## Cloudflare Access and eventual cleanup

The trial does **not** require Cloudflare Access. The message
`Cloudflare Access settings are incomplete: all external requests are denied`
is expected for the unconfigured external gateway. It does not block local
Home Assistant ingress, phone-number pairing, or WhatsApp history ingestion.

The trial configuration exposes only `phone_number`, has no external host
port published, and has no Cloudflare-specific provisioner. Installing the
trial does **not** create or change a Cloudflare Access app, tunnel, DNS
record, Access policy, service token, or route. **Do not add Cloudflare
configuration simply to clear this harmless message.**

When removing the experiment:

1. Stop only `64b28acc_whatsapp_fullsync_trial`; unlink its *trial*
   companion device on the phone if it is still paired, taking care not to
   unlink the original production WhatsApp companion.
2. Check whether any Cloudflare-specific resources were **manually created
   solely for the trial** after installation (Access applications and policies,
   tunnel routes, DNS records, service tokens). If none were created, **no
   Cloudflare rollback is required**. Never delete or reset shared resources
   or production WhatsApp Access settings.
3. If trial-only Cloudflare resources were manually created later, record
   their identifiers and previous configuration, confirm they are not
   shared, then delete only those trial resources and verify the production
   hostname and Access policy still function.
4. Uninstall only the trial Home Assistant add-on and confirm
   `64b28acc_whatsapp_mcp` remains installed and connected. This removes
   the trial's isolated /data; make a trial archive export first if its
   recovered history is worth preserving.
5. Retain the production WhatsApp archive, its Google Drive recovery backup,
   and the recovery Git branch. Source-code cleanup of this experimental
   folder or GHCR image is optional and must not remove the production image.

## Previous QR registration experiment
- macOS Desktop (DARWIN) on the same pinned Baileys version failed at registration, without a QR. See upstream Baileys issue #2880; this is why this trial uses Windows Desktop / WIN_HYBRID instead.
- Only scan the QR from this **experimental** add-on. Keep the original production linked-device session intact.
- If Windows Desktop also cannot produce a QR, stop only this experiment and record status-code evidence. Do not repeatedly re-pair production.

## Limitations
A successful build or pairing does not prove that WhatsApp sent all history.
Only a live controlled comparison can demonstrate increased coverage.
Baileys is an unofficial client and linking is at the account holder's risk.

## Resumable recovery and deduplication (trial only)

- **FULL batches** are WhatsApp's push-based initial synchronisation. Merely restarting this add-on does **not** reliably replay them; a new companion pairing might be needed, and even that cannot guarantee all historical messages.
- **Older-message recovery** is separately opt-in under **History sync** in the private Ingress UI. It requests one conversation at a time, prioritises direct chats, resumes from the oldest valid stored-message anchor, persists checkpoints between restarts and yields while FULL batches have arrived within the last 120 seconds. It can be paused at any time; the currently in-flight request may finish. A short or silent response does not prove completion.
- The 25-second response window, three-second inter-request delay and 2,000-request daily safety breaker are local choices, **not published WhatsApp limits**. Failures add a bounded backoff; the trial logs only aggregate error-status information.
- The SQLite schema has `messages_chat_id_id(chat_id,id)` uniqueness. Incoming replay uses upsert, not independent insert. Trial-specific protection prevents older history from restoring text on an existing revoked-message tombstone; existing delete-for-me suppression remains in force.
- The trial dashboard distinguishes **records received**, **newly inserted message keys**, **existing-row upserts** and **net changes in unique stored rows**. Existing-row upserts include replays and edits, not just identical duplicates; identity merges may reduce net row counts. Metrics begin when instrumentation is installed and cannot retroactively classify old batches.
- The trial offers manually repeatable recovery attempts for stalled and probably-complete conversations. A replay cannot be assumed to make any conversation definitively complete.
- Actionable stopped-worker or missing-checkpoint problems raise a Home Assistant persistent notification; an idle FULL-history stream does not.
- All recovery controls remain behind admin-only Home Assistant Ingress. No Cloudflare policy, port mapping, production archive or production code is changed.

Offline tests cover repeated and overlapping imports, tombstone replay, checkpoint retention, bulk-sync interlocks and the actual patched container. Passing them does not guarantee WhatsApp will provide more historical messages.
