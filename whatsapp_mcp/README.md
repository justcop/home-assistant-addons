# WhatsApp MCP (read-only Home Assistant app)

Private WhatsApp search using [spare-cycles/whatsapp-mcp](https://github.com/spare-cycles/whatsapp-mcp), pinned to commit `5f19883b0d8e2f672d486aa6307f3e945b49532e`. The upstream API and MCP programs run in one app container. **This uses the unofficial Baileys WhatsApp client and may put the WhatsApp account at risk of restriction.** It is not an officially supported integration.

## Upstream maintenance and safe deployment

The upstream source is pinned to a **full Git commit SHA**, never a floating
branch. The scheduled GitHub Action
[Check WhatsApp upstream (guarded)](../.github/workflows/whatsapp-upstream.yml)
runs daily at **07:17 UTC** and can also be triggered manually.

- It compares the pinned SHA against `spare-cycles/whatsapp-mcp/main`.
  Documentation/tests-only changes are classified low-risk; all application
  code, Baileys, storage/schema, authentication, build files, dependencies
  and unknown/incomplete comparisons are **high-risk**.
- Each changed upstream candidate gets its own pull request and an explicit
  WhatsApp test workflow run. A docs/tests-only candidate can be merged by
  the GitHub Action **only after the full image build and tests pass**.
  High-risk candidates stay open for human review; merging is not automatic.
- The candidate tests use synthetic data, not live WhatsApp messages.
  They test deletion/replay/privacy semantics, read-only HTTP behaviour,
  SQLite integrity, cloud recovery, patch anchors and the actual compiled
  upstream API/Baileys/MCP modules. If any patch stops matching upstream,
  the Docker build fails.
- GitHub's image publisher must verify that the image exists and can be
  **anonymously pulled** before advertising a new version. WhatsApp
  additionally performs a full amd64 pull and architecture check, protecting
  against the previous GHCR missing-manifest incident.
- Home Assistant's WhatsApp **automatic image updates are disabled**.
  The shared My Add-on Updater also refuses unattended **live-code**
  deployments for `whatsapp_mcp`, even if its generic compatibility
  classifier considers a change safe. All production installation is
  deliberate after CI and publication checks.
- **My Add-ons** monitors the upstream GitHub workflow. A failed run,
  missing run, or a workflow silent for over 48 hours causes a deduplicated
  native Home Assistant persistent notification. Deployment/startup
  failures are independently reported through the same notifier.
- For rollback, use GitHub Actions →
  **Propose WhatsApp image-only rollback** with an earlier verified repo
  commit. It opens a **manual-review PR** restoring executable image source,
  leaving HA options, credentials, backups and the live STORAGE database
  untouched. After tests and approval, publish a **new immutable image** and
  install it manually. Never restore an older SQLite database automatically:
  that can resurrect deleted messages.

Passing CI cannot prove future WhatsApp protocol compatibility indefinitely.
Monitor connection, backup, archive and deletion behaviour after installing
any reviewed update. If a new image appears incompatible with the existing
SQLite schema, *do not roll it back automatically* without checking schema
compatibility first.

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

## Home Assistant app branding

The app supplies an original green messaging/archive/shield design as `icon.png` (square 128×128) and `logo.png` (horizontal 500×200), as recommended for Home Assistant app presentation. The icons convey a locally stored, read-only messaging archive rather than implying this is an official WhatsApp client.

## Private WhatsApp Archive web interface

The archive uses WhatsApp's own synchronised contact names. The vCard import screen, write endpoint and local alias cache have been removed in 0.2.6; no external contact provider is required.


From version 0.2.0, the app provides a read-only archive viewer through Home Assistant **Ingress**. Open **Settings → Apps → WhatsApp MCP (Read Only) → Open Web UI**, or enable **Show in sidebar**.

- Separate **Direct** and **Groups** tabs for browsing. Conversation-name search and full-text message search remain global across both types, including search-result navigation.
- Conversation timestamps, sender names, message bubbles and attachment downloads.
- Search conversations and full-text search messages/transcripts; click a result to open that period of the conversation.
- Responsive desktop/mobile layout. No direct port is published. The ingress listener is on **8100**, separate from Cloudflare MCP **8099**, and only accepts the Home Assistant Supervisor ingress peer.
- Home Assistant login and admin-only app panel protect the viewer. All browsing routes are GET-only; none can send, edit, delete or mark messages read.
- The viewer reads via the existing upstream read-only REST API, so it does **not** open a second live SQLite writer. The archive shows WhatsApp history actually synchronised to this linked device; it does not guarantee older history which WhatsApp did not provide.

## Privacy-aware per-message deletions (0.3.2)

The pinned Baileys upstream previously treated every `messages.delete` event as a
message revoke. The wrapper now patches this distinction **without** opening a
WhatsApp write endpoint:

- **Delete for me (any author):** when Baileys emits a per-message
  `messages.delete` event, the archive permanently removes its current SQLite
  row, the FTS search entry, reactions, quote references, and any future
  `message_revisions` entries for that message. A small SHA-256 digest of the
  chat/message identifier (no message content) prevents a later WhatsApp history
  replay from re-inserting the removed message.
- **Delete for everyone (your message):** hard-purge instead of keeping your own
  revoked content. On startup, the app also cleans up previously marked
  `from_me=1` revocations.
- **Delete for everyone (someone else's message):** preserve the existing
  deleted-message marker for the future version-history viewer.
- Content-addressed attachment files are removed **only if no other stored
  message uses them**. Existing backup snapshots and possible quoted copies of
  the content embedded inside *other* raw WhatsApp messages are not automatically
  rewritten. SQLite uses `secure_delete`, but WAL and independently held
  historical backups must be managed separately.

**Limits:** The pinned Baileys release has a handler for
`deleteMessageForMeAction` in app-state sync, but the phone does not always
deliver those events reliably to unofficial linked clients. A successful test of
this feature therefore requires deleting a non-sensitive test message *on the
phone* and verifying that it disappears from the archive after sync. If the
event is never received, no code on the archive can infer which unseen messages
were intentionally deleted. **Do not compare the archive against a partial
WhatsApp history sync and delete every missing entry**: that would cause
irreversible data loss during incomplete history coverage or reconnection.

### Clear-chat detection: limited self-chat test

Baileys understands WhatsApp's `clearChatAction` app-state operation but its
published event stream does not expose it. The Docker build therefore patches
the **deployed, pinned Baileys runtime** to emit metadata through an internal
`chats.update` event; the WhatsApp API consumes this without sending anything
to WhatsApp.

For the **first live test only**, automatic whole-chat clearing is restricted to
your own **Messages to Yourself** conversation. Clear-chat actions in every
other conversation are observed in aggregate diagnostics but **do not delete**
data from the archive. This guard is deliberate until phone-originated events
have been verified against the running linked client.

A self-chat clear is performed only when all conditions hold:

1. An actual incremental app-state `clearChatAction` is delivered (not an
   inferred absence, chat deletion, or initial history replay).
2. WhatsApp explicitly indicates that **starred messages are also deleted**,
   rather than preserving them. If WhatsApp requests that starred messages
   be kept, the archive ignores the whole clear for now rather than guessing
   which historical messages were starred.
3. The action includes a valid last-message timestamp. Only this chat's
   messages at or before that boundary are purged; newer messages survive.
4. The batch contains at most 20,000 local message rows. Purged identifiers
   are recorded as non-content digests so an old history replay cannot
   restore them.

The app logs only generic event outcomes and aggregate counts, **not**
message text, IDs, contact names, or chat IDs.

**Live test procedure:** Open the Messages to Yourself thread and verify
one or more expendable messages are visible in the private archive.
On your phone select **Clear chat**, explicitly select deletion of starred
messages if that option appears, then inspect the archive and HA app logs.
Send a new message after clearing and verify it survives. If the old messages
remain, check whether the log reported an ignored action or no action was
received at all; do not interpret this as a successful purge. A test in this
self-chat does not prove that WhatsApp sends the same events in all chats.

**Delete chat** is a separate WhatsApp action (`chats.delete`) and is not
purged by this feature; an empty or deleted chat must not be confused with
`clearChatAction`.

The privacy purge only affects live data after this version is installed. It
cannot remove content already copied into earlier Home Assistant/cloud backups.
This is a functional retention mechanism, not a secure-erase guarantee across
all storage layers. It retains the existing Ingress and Cloudflare access
controls, and offers no remote-message-delete function.

## Context-efficient MCP conversation reviews (0.3.0)

Two additional **read-only** tools are available to ChatGPT from this Home Assistant package:

- `whatsapp_review_conversations` performs one bounded local SQLite scan of up to 90 days (21 days by default). It returns compact conversation contexts, message IDs and tentative flags for reply requests, commitments and upcoming arrangements. Default scope is direct messages; set `mode=all` to include groups. Results are paginated with `offset` and `next_offset`.
- `whatsapp_get_review_context` reads up to 20 chat IDs in a single request, with the recent messages, timestamps and IDs needed to verify those tentative findings.

These tools use the existing verified database path, run in the MCP container under the same unprivileged account and do not open an additional network listener or WhatsApp connection. They neither send messages nor mark any read. They do not call an external AI service, write message summaries to disk or alter SQLite. Query results are exposed only through the existing Cloudflare Access JWT-verified `/mcp` gateway.

**Important:** The detection is heuristic. A pending reply may have been handled in person; a promise may already have been fulfilled elsewhere. An incomplete WhatsApp sync, media attachments and unavailable voice-note transcripts can conceal relevant actions. Treat flagged conversations as leads, read the context and verify before declaring anything overdue. The 50,000-message per-request scan cap is reported explicitly if reached.

## Backup issue alerts (0.3.18)

The archive keeps normal cloud backup settings in **Settings** at the bottom
of its conversation sidebar. When a backup fails or old cloud copies cannot
be cleaned up, an always-visible red warning appears above the conversation
pane, with a direct link to backup settings. The warning disappears once the
next backup completes and one-copy retention is verified.

The add-on also publishes a Home Assistant **persistent notification** named
`whatsapp_cloud_backup_failure` with a short error summary. Repeated failures
update the same notification instead of generating duplicates. It dismisses
the notification after successful backup or successful retention cleanup.
A previously unresolved failure is reported again on add-on restart.
Notifications use Home Assistant Core's native API via Supervisor
(`homeassistant_api: true`); the WhatsApp message APIs remain read-only,
and no message content or OAuth credentials are sent in notifications.
This does not automatically send a phone push notification; users can
configure their own notification automation if desired.

## Independent, encrypted Google Drive backup (0.3.15)

**Purpose:** Back up the **live SQLite message database** daily, completely independently
of Home Assistant's native snapshots and the installed Google Drive Backup add-on.
The live database continues to be stored on the local mounted STORAGE drive.

### Initial setup (one-time Google authorisation)

1. Open **WhatsApp Archive → Cloud backup & recovery**. In a Google Cloud project,
   enable the **Google Drive API** and create an OAuth client of type
   **TVs and Limited Input devices**. If necessary, configure the Google OAuth
   consent screen and authorise your account as a test user. A Google OAuth
   app left in testing mode may expire refresh tokens after a week; use a
   published production consent screen for unattended, long-term backups.
2. Enter the OAuth **client ID** and **client secret** in the backup panel,
   then click **Connect Google Drive**. Visit Google's displayed verification
   URL and enter the one-time code; the app polls Google's device-token
   endpoint over HTTPS until authorised. Google Drive's limited
   `drive.file` scope permits this new app to manage **only its own files**.
   It cannot directly reuse the other add-on's private OAuth credentials.
3. Select **Show / generate recovery key**. Copy the 32-byte base64url
   recovery key to a password manager **outside Home Assistant**, then confirm.
   Neither the recovery key nor the Google refresh token is included in Home
   Assistant app backups: `/data/.whatsapp-cloud-backup` is excluded.
4. Click **Enable daily backup**, choose the default **05:30 Europe/London**
   schedule or edit it, and optionally **Run backup now** for the first test.

The live SQLite writer is not stopped. Each run uses Node's SQLite online
backup API to create a consistent snapshot; verifies `PRAGMA integrity_check`;
encrypts with AES-256-GCM using a fresh random nonce per run; uploads via the
Google Drive resumable API to a dedicated app-owned private folder; checks
the Drive object size and MD5 of the encrypted bytes; then deletes all other
managed backup files in that folder. A failed snapshot, encryption or upload
**does not delete the previous verified backup**. If removal fails, the app
marks cleanup pending and retries rather than reporting successful
one-copy retention. There can be a brief overlap during upload and deletion.

Backup files are named `whatsapp-db-<UTC timestamp>.wacloud`, not shared
publicly, and no WhatsApp message text, media, OAuth tokens, or encryption
key is written to normal backup logs. The UI reports the last successful
backup, any errors, and whether cleanup remains incomplete.

**What is backed up:** `whatsapp.db`, including messages and the database's
existing tables, but **not** downloaded media/attachments, the WhatsApp
linked-device credentials, the cloud configuration or separate internal API
tokens. A full disaster recovery may require re-pairing WhatsApp.
Temporarily created plaintext database snapshots are deleted after each run.

**Important retention limitation:** one copy visible through Google Drive
after successful cleanup is not a cryptographic guarantee of erasure from
Google's internal replicas, trash, litigation holds, cached copies or
independently configured cloud backups. Old Home Assistant snapshots
predating this feature are unaffected.

### Restoring from Google Drive

In the archive's backup panel, click **Download latest encrypted backup**.
On a trusted computer with Node 24+, download the companion source files
`cloud-backup.mjs` and `restore-cloud-backup.mjs` from this repository,
and create a private text file containing only the saved recovery key.
Then run:

```sh
node restore-cloud-backup.mjs whatsapp-db-DATE.wacloud recovered.sqlite recovery-key.txt
```

The tool verifies AES-GCM authenticity and SQLite integrity, refuses to
overwrite an existing output and leaves the encrypted input unchanged.
**Stop the WhatsApp add-on before replacing the live database**. Back up
the current live state only if you deliberately want a rollback; otherwise
you may inadvertently restore deliberately deleted messages from an older
snapshot. Copy the recovered SQLite database to the expected external
volume using a trusted local file-management tool, with matching owner and
0600 permissions. With the WhatsApp writer stopped, remove any old
`whatsapp.db-wal` and `whatsapp.db-shm` files before restarting: they belong
to the previous live SQLite database and must never be applied to the
recovered database. Re-pair if needed. Do not restore an encrypted backup
from an unknown sender or place its recovery key in GitHub.

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
3. Validate the external SQLite database using an integrity check and schema read, and test for actual write permission before starting any WhatsApp worker.
4. After that verification, **delete** the retired `/data/whatsapp` copy so old/deleted messages are not carried indefinitely in future Home Assistant app backups. If validation fails, stop the app **without** deleting the old copy. No row-count comparison or old-data merge is performed: fewer external rows may represent intentional deletion.
5. Once configured, always use the same external path. A missing external drive fails startup rather than silently falling back to internal storage.

The app also declares `backup_exclude` patterns for `/data/whatsapp` as defence in depth against future app backups. The remaining `/data/.whatsapp-mcp-secrets` contains gateway credentials, **not the message database**.

**Deleted-message privacy and backups:** Do not include the external `media` volume in Home Assistant automatic or manual backups, and exclude this folder from other NAS/cloud/snapshot backup jobs. Deleting a message from live SQLite **does not remove it from backups already taken**. Review old Home Assistant snapshots, Google Drive backups and any other previously created backup copies independently. If the archive volume itself uses filesystem snapshots, those may still retain prior data. Keeping no historic message backups entails accepting loss of the archive if the drive fails.

`storage_path: ""` still permits an explicitly configured internal archive (to avoid breaking other installations), but it is **not** the intended privacy-focused configuration. Only place live SQLite WAL files on a real local filesystem with working file locks (e.g. ext4), not an SMB/NFS share.

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

## Guarded upstream maintenance and image-only rollback

The workflow .github/workflows/whatsapp-upstream.yml checks
spare-cycles/whatsapp-mcp daily at 07:17 UTC. All candidates use immutable
40-character upstream commit SHAs. Missing, incomplete, divergent or truncated
upstream compare results are classified HIGH risk, as are changes to executable
runtime code, Baileys, lockfiles, schemas, privacy, authentication and protocol.
Only documentation and tests-only upstream diffs are LOW risk.

A new upstream revision creates a candidate PR with exact diff, risk label and
explicitly dispatched container build and privacy regression tests. HIGH risk
PRs always require human approval, even if tests pass. LOW risk docs-only
candidates may merge after all tests pass. Neither path installs anything into
Home Assistant automatically. The GitHub repository must allow Actions to
create pull requests, otherwise the job fails visibly.

The image delivery workflow checks every GHCR tag anonymously before
advertising a new version to Home Assistant. A failed build or manifest check
prevents advertisement. After installing an update manually, check WhatsApp
connection, read-only MCP tools, STORAGE database path and backup health.

For source-only rollback, manually dispatch
.github/workflows/whatsapp-image-rollback.yml with a full SHA of a verified
previous repository commit. It opens a manual review PR restoring executable
application source only. It never modifies the message database, STORAGE
volume, keys or backups. Publish a NEW tested image version to revert code.
Never restore old message databases automatically: that could resurrect
deleted messages. Confirm a schema downgrade is safe before deploying an
older application build.

