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

## Private WhatsApp Archive web interface

From version 0.2.0, the app provides a read-only archive viewer through Home Assistant **Ingress**. Open **Settings → Apps → WhatsApp MCP (Read Only) → Open Web UI**, or enable **Show in sidebar**.

- Conversation list, groups, timestamps, sender names, message bubbles and attachment downloads.
- Search conversations and full-text search messages/transcripts; click a result to open that period of the conversation.
- Responsive desktop/mobile layout. No direct port is published. The ingress listener is on **8100**, separate from Cloudflare MCP **8099**, and only accepts the Home Assistant Supervisor ingress peer.
- Home Assistant login and admin-only app panel protect the viewer. All browsing routes are GET-only; none can send, edit, delete or mark messages read.
- The viewer reads via the existing upstream read-only REST API, so it does **not** open a second live SQLite writer. The archive shows WhatsApp history actually synchronised to this linked device; it does not guarantee older history which WhatsApp did not provide.

## Move live WhatsApp storage to the separate STORAGE drive

**Do not use "Move data disk"**: that moves all Home Assistant data and may overwrite a disk. Instead, on Home Assistant 2026.11+ use **Settings → System → Storage → Add storage → Local disk**, select your already-formatted STORAGE volume, choose **Usage: Share**, **Read-only: off**, and name the mount **STORAGE**. This should make it available at `/share/STORAGE` in apps mapping Home Assistant shared storage. If the disk is already mounted at a host-only path (such as `/mnt/storage`), that does **not** necessarily make it accessible inside this app; verify the actual mounted volume first. Do not format or erase existing files.

When the volume is visible, set this app option and restart it:

```yaml
storage_path: /share/STORAGE/whatsapp-mcp
```

**Migration safeguards:**
1. On startup (before the WhatsApp API opens SQLite), the app verifies that `/share/STORAGE` is a separate mounted filesystem, not an ordinary directory on the internal disk. A missing/invalid mount **stops the app**, rather than creating a second empty archive.
2. If the destination is absent, it copies the entire stopped WhatsApp data directory to a temporary folder on STORAGE, fixes ownership, then atomically renames it into place. This includes SQLite, WhatsApp linked-device state, and cached media.
3. The original `/data/whatsapp` is **retained as a rollback copy** until the new archive is verified; no automatic deletion takes place. After migration the live API uses only the external directory.
4. If an external archive already exists, it takes priority. Do not switch the option off casually: that would bring back the older internal archive. Keep an independent encrypted backup.

The option defaults to an empty string, meaning the existing Home Assistant app-local `/data/whatsapp` is used. If a migration fails, fix the volume or filesystem permissions; never bypass the mount check. **SQLite should be hosted on a locally attached filesystem that supports normal file locking** (prefer ext4); do not put its live WAL files on SMB/NFS shares.

Note that the MCP gateway's two private bearer tokens remain in `/data/.whatsapp-mcp-secrets` under app storage, while WhatsApp account state and the conversation archive move together. The archive viewer reports whether it is using app-local or external storage.

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
