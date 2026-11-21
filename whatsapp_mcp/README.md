# WhatsApp MCP (read-only Home Assistant app)

Private WhatsApp search using [spare-cycles/whatsapp-mcp](https://github.com/spare-cycles/whatsapp-mcp), pinned to commit `5f19883b0d8e2f672d486aa6307f3e945b49532e`. The upstream API and MCP programs run in one app container. **This uses the unofficial Baileys WhatsApp client and may put the WhatsApp account at risk of restriction.** It is not an officially supported integration.

## Security boundaries

- `WHATSAPP_MCP_READONLY=true` is applied to the **WhatsApp API**, not just the MCP tool listing. Upstream removes the write tools from MCP capabilities.
- Only the gateway listens on TCP **8099** for other containers. The WhatsApp API (8097) and MCP backend (8098) are private to the app container and have independently generated, persistent 256-bit bearer tokens.
- The gateway **rejects every request** until Cloudflare Access team domain, app audience (AUD), and the single permitted email are configured.
- On every request the gateway verifies Cloudflare's `Cf-Access-Jwt-Assertion` signature, issuer, audience, expiry, and permitted email against Cloudflare's JWKS. Merely supplying an Access-looking header is insufficient.
- The gateway permits only `/mcp`, forwards the authenticated request to the upstream MCP backend, and strips client-supplied authorization and Access headers.
- By default, database state, linked-device credentials and downloaded media live under **/data/whatsapp** (Home Assistant app persistent storage). They can be moved together to an explicitly configured verified local STORAGE disk. The internal gateway secrets remain under **/data/.whatsapp-mcp-secrets**. Never put private data in a public Git repository.
- There is no public or host-mapped port by default. **Do not set a host port mapping or expose the upstream API.**
- Do not use this with clinical patient-identifiable data or material requiring NHS-approved handling.

## Meaningful conversation activity and contact names

From version 0.2.2, the **private archive viewer** ignores WhatsApp protocol notices when listing conversations and messages. It ignores `system` events and empty `other` entries (no text, transcript or media). In particular, repeated security-code/identity notices no longer make an otherwise inactive group appear recently active.

- Conversation order and date are computed **read-only from actual human messages in SQLite**; the upstream WhatsApp/MCP database is unchanged. An empty group has no last activity date.
- Contact names are taken from saved WhatsApp contact entries and their WhatsApp display names when available. Raw phone numbers remain the fallback if no known name exists.
- Names unavailable to WhatsApp appear as phone numbers or “Unknown contact”; identity matches are never invented.
- As of 0.2.7, unreferenced, decoded non-conversation notices are pruned on startup and omitted from new ingestion. Quotes and reactions protect referenced historical rows from deletion. The viewer excludes any remaining empty notices.

## Protocol retention (0.2.7)

A read-only diagnostic decoded the empty `kind=other` records using the exact pinned Baileys protobuf: 4,639 message-stub group/security notices, and 307 pure history/sender-key transport envelopes were confidently classified as disposable (4,946 combined at inspection). The remaining 121 records included 23 polls, 44 albums, 24 comments, 9 templates, 5 interactive messages, 2 events, placeholders, and unknown messages. Those are retained even if the current viewer does not render them yet.

The pruning pass runs **before** the WhatsApp API writer, deletes only the identified non-content records, preserves rows quoted by another message or carrying reactions, and compacts SQLite in one transaction. New copies of the same clearly disposable record types are filtered in the pinned upstream ingestion code; unknown types are retained. Separate current UI/date-range statistics exclude hidden records. Restorable Home Assistant backups are unchanged. The internal read-only diagnostic endpoint `/api/archive/unknown-types` returns aggregate counters, not message content or identifiers.

## Meta AI is excluded from the local archive

Starting with 0.2.5, the explicitly identified Meta AI JID `13135550002@s.whatsapp.net` is skipped on WhatsApp ingestion (both live messages and history replay), omitted from the private viewer and search, and removed from the **local** SQLite archive on app startup before the upstream API is launched. The narrow filter does not delete messages from the user's phone or WhatsApp's servers, and does not match chats simply named Meta AI. The prune operates in one SQLite transaction, keeps normal chats intact and checkpoints the local WAL. Old HA backups and any retained pre-migration rollback database may still contain earlier copies.

WhatsApp contact names are read directly from the synchronised contacts table; no Google Phonebook integration, administrator token or periodic Google API lookup is used. Manual vCard imports are no longer supported.

## Private WhatsApp Archive web interface

The archive uses WhatsApp's own synchronised contact names. The vCard import screen, write endpoint and local alias cache have been removed in 0.2.6; no external contact provider is required.


From version 0.2.0, the app provides a read-only archive viewer through Home Assistant **Ingress**. Open **Settings → Apps → WhatsApp MCP (Read Only) → Open Web UI**, or enable **Show in sidebar**.

- Conversation list, groups, timestamps, sender names, message bubbles and attachment downloads.
- Search conversations and full-text search messages/transcripts; click a result to open that period of the conversation.
- Responsive desktop/mobile layout. No direct port is published. The ingress listener is on **8100**, separate from Cloudflare MCP **8099**, and only accepts the Home Assistant Supervisor ingress peer.
- Home Assistant login and admin-only app panel protect the viewer. All browsing routes are GET-only; none can send, edit, delete or mark messages read.
- The viewer reads via the existing upstream read-only REST API, so it does **not** open a second live SQLite writer. The archive shows WhatsApp history actually synchronised to this linked device; it does not guarantee older history which WhatsApp did not provide.

## Move WhatsApp data to your existing STORAGE drive

If Home Assistant **Settings → System → Storage** already lists the local ext4 drive named `storage` with **Usage: Media**, **leave it exactly as it is**. Changing its usage to Share could break other apps. This version supports both existing Media and Share disk mounts.

For the existing local Media mount, set the app's `storage_path` to:

```yaml
storage_path: /media/storage/.whatsapp-mcp-private
```

The app definition maps Home Assistant's `/media` and `/share` to the container. The folder is deliberately dot-prefixed and owner-only (0700); it is not meant to be browsed through Home Assistant's media browser. **Because Media is also available to other HA components, this is not a separate access-control boundary against Home Assistant administrators or integrations running with elevated filesystem privileges.** Only the private Ingress archive UI should serve it to a browser; do not publish the underlying database or media files.

The previous configuration `/share/storage/whatsapp-mcp` **will not work** for a disk configured as Usage: Media. The correct mount path depends on its existing Usage.

**Migration safeguards:**
1. On startup before the WhatsApp API opens SQLite, verify that the selected `/media/storage` or `/share/<name>` is a separate mounted filesystem. A missing mount stops the app rather than creating a second empty archive.
2. If the private destination folder is absent, copy the stopped WhatsApp data directory into a temporary folder **on the external disk**, set private permissions and atomically rename it into place. This includes SQLite, linked-device state and cached media.
3. Keep the original `/data/whatsapp` intact as a rollback copy. The live API moves to the external directory only after a successful migration.
4. Never automatically merge with an existing archive or remove the original.
5. Test for actual write permission after dropping privileges to the app's unprivileged user.

`storage_path: ""` continues to use `/data/whatsapp` in app-local storage. Only place live SQLite WAL files on a real local filesystem with working file locks (e.g. ext4), not an SMB/NFS share. Keep independent encrypted backups; this move is **not** a substitute for them. Internal API/gateway bearer tokens remain under `/data/.whatsapp-mcp-secrets`.

## Install and link WhatsApp

1. Install **WhatsApp MCP (Read Only)** from this repository in **Settings → Apps**. It runs on **amd64 only**.
2. In the app configuration set **phone_number** to your WhatsApp number with country code, digits only (e.g. UK `447...`, no `+` and no leading zero). Save and start the app.
3. In the app log find `WhatsApp pairing code:`. On Android, open WhatsApp → Settings → Linked devices → Link a device → Link with phone number instead, then enter the current eight-character code.
4. Watch the app logs until WhatsApp connects and history begins synchronising. Only the history delivered to a newly paired linked device is available; this cannot guarantee complete old conversations.

Pairing codes in logs are temporary credentials. Avoid sharing screenshots or publicly accessible logs.

## Cloudflare + ChatGPT (after pairing)

Create a **separate Cloudflare Access MCP application** for a dedicated hostname, such as `wa-mcp.example.com`, with a policy permitting **only your Google identity**. Enable **Managed OAuth** on that Access application. Configure its existing Cloudflare Tunnel route to the app's *internal Docker-network address*, port 8099. Do **not** route to 8097 or 8098 and do not disable Access.

Set these app configuration values from the Cloudflare Access app:

| Setting | Value |
| --- | --- |
| `access_team_domain` | The team hostname, e.g. `yourteam.cloudflareaccess.com` |
| `access_audience` | The Access application **AUD tag**, not the hostname, app ID or OAuth client ID |
| `allowed_email` | The exact Google account email permitted to read WhatsApp |

Restart the app to apply these options. The external URL is `https://wa-mcp.example.com/mcp`. In ChatGPT **web**: Plugins → Add custom MCP server → enter URL → use Cloudflare OAuth → review read-only tools. ChatGPT does not accept a manually supplied bearer token in this flow; **Managed OAuth** is what makes the service usable without exposing unauthenticated data.

**Important:** Cloudflare must forward the verified JWT in `Cf-Access-Jwt-Assertion`. If that assertion is absent or invalid, the gateway intentionally responds 401/403, even to legitimate browser requests. Do not try to fix this by disabling gateway verification.

The precise internal Docker-network hostname is specific to the installed app's Supervisor slug and tunnel network; verify it from the actual installation. An inaccessible app hostname is a networking issue, not a reason to publish an unauthenticated WAN port.

## Data and lifecycle

- The app is configured **boot: manual** to avoid unexpectedly reconnecting to WhatsApp after a host restart. Change it to auto in the Home Assistant app UI only after verifying pairing and authentication.
- Linked-device credentials and SQLite storage persist in /data and are included in app backups. Anyone with those backups could potentially access sensitive WhatsApp information. Encrypt and control backup access.
- To revoke access immediately, unlink the linked device from WhatsApp on your phone, revoke the Cloudflare Access app session, and stop this app. Deleting /data will require re-pairing.
- Upstream WhatsApp changes can break unofficial clients; upgrade the pinned commit only after review and tests.
- External transcription services are **not configured**. Voice-note transcription therefore is not currently available; images and PDF metadata rely on the upstream media support.
- Persistent storage and the access controls do not guarantee full historical WhatsApp coverage. Old messages may require separately exported archives.

## Source and verification

`node --test whatsapp_mcp/tests/access.test.mjs` tests the JWT verification. The app-specific CI also builds the container against the pinned upstream checkout. A successful image build does **not** prove WhatsApp pairing, ChatGPT OAuth discovery or message retrieval; those require live testing after installing and linking the device.
