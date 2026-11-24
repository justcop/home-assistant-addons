# WhatsApp Full History Trial (independent Home Assistant add-on)

**Experimental, privacy sensitive. Does not replace the production WhatsApp MCP installation.**

This image is based on the pinned, published WhatsApp MCP 0.3.30 image.
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

## Limitations
A successful build or pairing does not prove that WhatsApp sent all history.
Only a live controlled comparison can demonstrate increased coverage.
Baileys is an unofficial client and linking is at the account holder's risk.
