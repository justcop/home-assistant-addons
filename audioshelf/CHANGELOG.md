# 0.6.2

- Link Listening Analytics to owned albums and artists in AudioShelf, with separate record store search links.

# 0.6.1

- Stack sign-in labels and fields vertically in a compact responsive form. Keep the trust-browser checkbox and sign-in button beneath the credentials, with clear spacing on phones and desktop.

# 0.6.0

- Separate AudioShelf logins with independent libraries, Spotify connections, devices, track mappings, cover choices, catalogue filters and appearance settings.
- Existing library, Spotify credentials and two-factor settings remain with the `owner` account; no collection moves or copying on upgrade.
- Owner account management in Settings: create users, reset passwords and explicitly recover two-factor access, or disable/enable accounts while preserving collections.
- Personal password changes, two-factor authentication and trusted browsers for each account; account management requires owner access and fresh verification outside implicit Home Assistant ingress.
- Switch accounts inside Home Assistant or standalone. Ingress can explicitly return to the owner account after signing out.
- Spotify callbacks bind to the account that started authorization, including callbacks arriving without the original browser session.
- Prevent stale browser tabs and cached covers from crossing accounts. User databases, private credentials and replaceable caches have separate locations.


# 0.5.5

- Wait for your chosen Spotify device on the server, so playback can start while AudioShelf is hidden and you stay in Spotify.
- Keep the exact synced album or selected disc and chosen device fixed throughout the one-minute wait. Cancel waiting work on dialog close, navigation, a new request, or revoked access.
- Show the requested first track as Starting immediately, before Spotify activation completes. Confirm Playing using fresh Spotify status.
- Replace browser-owned retry checks with server handoff checks, including hidden-page playback and cancellation during activation.

# 0.5.4

- Serve responsive WebP covers at 128, 320 and 640 pixels without enlarging small originals. Cache derived covers only for shelf albums, invalidate by source content, include them in cache limits and evict them with removed albums. Use authenticated private ETag revalidation for repeat visits.
- Resize edition previews to 128 pixels without caching them. Load shelf covers lazily and prioritize the album detail cover.
- Make the reload alert a prominent sticky banner with contrasting colours and a larger Reload button, retaining edit and in-flight action safeguards.

# 0.5.3

- Ask for a playback device before first playback and remember the choice. Never select the only available speaker as a replacement for an unavailable phone. Add Change device beside Play in both frontends and in the recovery dialog.
- Opening Spotify with no chosen device refreshes the chooser without starting music. With a chosen device, retry only that device and preserve the synced album or disc.

# 0.5.2

- Show the selected record and first track immediately when Spotify accepts Play. Recheck rapidly during startup so stale Spotify responses do not put the previous record back on the turntable.
- Add a song progress bar with elapsed and total time. Advance locally between Spotify checks, freeze for paused or unavailable playback, and resynchronise on seeks and track changes.
- Keep normal playback checks at 15 seconds while visible. Expect a change after Play and at song boundaries, then recheck quickly until Spotify confirms it. Refresh immediately on returning to the app.

# 0.5.1

- Open Spotify without an album or track deep link, preserving AudioShelf’s synced queue and selected disc. Automatically retry the exact requested tracklist when returning from Spotify, with a one-minute limit and cancellation when the dialog closes or you navigate away.
- Activate the only available controllable Spotify device when no device is active. Offer device selection when several are available; retain preferred-device selection without playing elsewhere when it is missing.

# 0.5.0

- New Vinyl interface: dense Record Store racks, artist dividers, and larger front-facing covers on My Shelf ledges. Two covers across on phones.
- Keep the original interface with Settings → Appearance → Classic. Interface and colour choices are saved with the collection.
- Inspect a sleeve alongside its tracklist; matching, editions, covers and diagnostics remain available through Album settings. Existing MusicBrainz disc boundaries are preserved.
- Return to the same browsing position and store search after inspecting an album. Adding a record acknowledges it in place.
- On the turntable follows Spotify playback, including paused and unavailable states, independently of the album being inspected. It refreshes every 15 seconds while visible.
- Includes the Home Assistant 2FA recovery options from 0.4.3 and automatic browser update prompts.

# 0.4.3

- Add a one-time 2FA recovery request in Home Assistant add-on configuration. A changed nonblank request disables 2FA and revokes sessions, trusted browsers and support logins on restart. Standalone password protection, Spotify connection and the collection remain intact. Repeated restarts do not repeat the same reset.

# 0.4.2

- Version browser JavaScript and CSS by release and asset content, bypass stale HTTP caches and replace old service-worker shell caches automatically.
- Check for new releases on focus, visibility and every minute. Show a Reload AudioShelf button without interrupting edits, dialogs or in-flight changes. Works through standalone and Home Assistant ingress.

# 0.4.1

- Default to the album-level MusicBrainz release-group cover independently of the edition chosen for the tracklist. Explicitly selected and uploaded covers still take priority, and older automatic covers are refreshed.
- Retain downloaded artwork only for albums on your shelf. Browsing the store and cover picker does not save image files. Remove store-only cached images on upgrade and evict downloads when an album leaves the shelf. Uploaded covers remain durable.
- Serve edition thumbnails through authenticated AudioShelf preview URLs, follow approved artwork redirects on the server, and show a placeholder when a cover is unavailable. Previews are never cached.

# 0.4.0

- Require a standalone password; keep Home Assistant ingress authentication. Use Secure cookies and fixed 12-hour server-side sessions, with invalidation on password changes and logout.
- Add optional authenticator two-factor authentication with a locally generated QR code, manual setup key, replay protection, ten single-use recovery codes and revocable 30-day trusted browsers.
- Add owner-managed expiring support logins with view-only or changes/playback permissions, private hashed credentials, immediate session revocation and an off-by-default Home Assistant master toggle.
- Require fresh owner authentication for sensitive changes, persist authentication throttling and show recent security activity. Keep authentication data outside collection exports and backups.

# 0.3.1

- Remove stale album-specific interface instructions and examples. Use the same preference-based edition ranking in all paths, without a separate country penalty.

- Remember a preferred Spotify Connect device, activate it before playback, and offer an Open Spotify and retry flow when unavailable. Optional app opening is saved separately for each browser.

- Group multi-disc tracklists by their original MusicBrainz disc boundaries and play any disc independently, keeping whole-album playback.

- Remove artist-specific catalogue lists, automatic series selection and album-country defaults from production. Existing explicit user preferences remain intact.
- Find MusicBrainz release-group series for any artist directly in Manage catalogue. Search results are suggestions; saving validates that the series contains albums credited to the artist.
- Retain successful catalogue membership snapshots for any series across restarts and cache deletion, with temporary-outage fallback.
- Apply catalogue selections to the artist being browsed and handle collaborative album credits without depending on credit order.
- Keep the Beatles as a regression example for soundtrack albums, regional repackagings and compilations.

# 0.3.0

- Configurable MusicBrainz country and format priorities: GB, US, worldwide, Europe; vinyl, CD, digital. Optional strict country restriction; cassette excluded by default.
- Persistent global and per-album country preferences, original-year safeguards and paginated edition selection with retry.
- Beatles Core Catalogue and reusable MusicBrainz release-group series selection for other artists, with editable include/exclude overrides.
- Match printed-title variants generically using each linked MusicBrainz recording's title, aliases and ISRCs, retaining artist, duration and version safeguards. Enrich existing shelves without replacing tracklists or manual corrections.
- Reject generic dated remixes and paginate fallback Spotify searches.
- Choose covers from other editions without changing mappings; request media before filtering cover editions, refresh stale edition artwork and handle legacy cassette covers.
- Download album diagnostics with matching explanations and bounded recent errors, excluding credentials.
- Ten persistent visual themes with previews, light/dark palettes, contrast checks and responsive layouts.
- Preserve existing shelves, tracklists, uploaded covers and manual Spotify corrections on upgrade.

# 0.2.0

- Locally cached album art with Cover Art Archive edition fallback and Spotify fallback.
- Separate configurable `/share/audioshelf-cache` for downloaded images and raw API responses. Existing response caches migrate out of the collection database.
- Upload custom covers and restore automatic artwork. Uploaded files stay with the collection.
- Prefer the latest labelled remaster or dated studio mix, while preserving original track order and excluding bonus tracks.
- Search additional Spotify result pages, retain manual corrections and show the playback edition.

# 0.1.0

- First AudioShelf MVP: mobile shelf, artist views, chronological studio-album Record Store and album pages.
- MusicBrainz catalogue, release-group artwork, original edition selection and persistent collection in `/share/audioshelf`.
- Track-level Spotify resolution and corrections, exact ordered playback, Premium PKCE connection and token refresh.
- One-time original tracklist review, blocked playback for incomplete mappings, and Shuffle/Repeat checks.
- Home Assistant ingress, standalone port, optional web password, PWA shell, collection export and database backups.
- Automated backend and mobile/desktop browser checks.
