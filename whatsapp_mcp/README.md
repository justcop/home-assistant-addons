# WhatsApp MCP (read-only Home Assistant app)

Private WhatsApp search using [spare-cycles/whatsapp-mcp](https://github.com/spare-cycles/whatsapp-mcp), pinned to commit `5f19883b0d8e2f672d486aa6307f3e945b49532e`. The upstream API and MCP programs run in one app container. **This uses the unofficial Baileys WhatsApp client and may put the WhatsApp account at risk of restriction.** It is not an officially supported integration.

## Security boundaries

- `WHATSAPP_MCP_READONLY=true` is applied to the **WhatsApp API**, not just the MCP tool listing. Upstream removes the write tools from MCP capabilities.
- Only the gateway listens on TCP **8099** for other containers. The WhatsApp API (8097) and MCP backend (8098) are private to the app container and have independently generated, persistent 256-bit bearer tokens.
- The gateway **rejects every request** until Cloudflare Access team domain, app audience (AUD), and the single permitted email are configured.
- On every request the gateway verifies Cloudflare's `Cf-Access-Jwt-Assertion` signature, issuer, audience, expiry, and permitted email against Cloudflare's JWKS. Merely supplying an Access-looking header is insufficient.
- The gateway permits only `/mcp`, forwards the authenticated request to the upstream MCP backend, and strips client-supplied authorization and Access headers.
- All database state, linked-device credentials, downloaded media and internal secrets live under **/data** (Home Assistant app persistent storage). Do not put them in /share or a public Git repository.
- There is no public or host-mapped port by default. **Do not set a host port mapping or expose the upstream API.**
- Do not use this with clinical patient-identifiable data or material requiring NHS-approved handling.

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
