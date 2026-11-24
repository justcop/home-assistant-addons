# WhatsApp Full History Trial (independent Home Assistant add-on)

**Experimental, privacy sensitive. Does not replace the production WhatsApp MCP installation.**

This image is based on the pinned, published WhatsApp MCP 0.3.31 image,\nincluding the admin-ingress pairing banner, code display, connection status, and\nSettings re-pair control. Pairing status hides after successful linking.
At image-build time it enables macOS Safari's recognised pairing-code profile
while retaining Baileys `syncFullHistory: true`. The regular app already
enabled the flag, but existing credentials cannot replay initial device pairing.
The documented `Browsers.macOS("Desktop")` identity is rejected during
phone-number pairing by this pinned Baileys/WhatsApp path. Safari is a hypothesis,
not equivalent proof of Desktop's claimed extended history.

## Isolation contract
- Unique Home Assistant app slug `whatsapp_fullsync_trial`; independent
  /data/whatsapp SQLite and Baileys authentication database.
- No /media or /share volume mapping; image also hard-forces internal storage,
  so it cannot open the original archive even if given a storage_path.
- No host-mapped ports; read-only archive accessible through admin ingress only.
- Cloudflare MCP gateway is disabled without Cloudflare Access credentials.
- Backfill requests are disabled while measuring Baileys initial history sync.
- Never unlink the production WhatsApp device just to pair this second one.
- Keep the trial stopped after testing, since it consumes a linked-device slot.

## Live test
1. Confirm the production WhatsApp app is running and its encrypted Google Drive
   recovery backup is current, and retain its source/rollback branch.
2. Install **WhatsApp Full History Trial**, not WhatsApp MCP (Read Only).
   Set the phone number to international digits, without a leading +.
   Start the trial app; retrieve the temporary pairing code from its own log.
3. On the phone use WhatsApp > Settings > Linked devices > Link a device >
   Link with phone number instead. Complete the new pairing.
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

## Limitations
A successful build or pairing does not prove that WhatsApp sent all history.
Only a live controlled comparison can demonstrate increased coverage.
Baileys is an unofficial client and linking is at the account holder's risk.
