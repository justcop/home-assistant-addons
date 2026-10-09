# 0.6.13

- Offline artwork cache for collected albums in the standalone AudioShelf PWA, retained across app updates and isolated per account. Settings displays the measured number of albums, cached thumbnail files and total local cache size, with controls to preload the shelf and clear local artwork.
- Existing authenticated Android helper playback-result handshake from 0.6.12 is retained. Cached artwork reloads when edited and its seven-day refresh policy prevents permanent staleness; Record Store and Spotify media are not cached.

# 0.6.12

- Enable short-lived, scoped Android helper playback-status long polling; don't use SDK callbacks or timers as proof of playback.
- Confirm the selected phone and exact first track are playing before reporting success; distinguish accepted-but-unverified Spotify playback and never reissue it.
- Preserve cancellation and multi-account isolation, invalidating helper tokens when a job is replaced.

# 0.6.11

- Automatically launch the enabled Android helper after Play finds the preferred phone unavailable. Available devices play directly. Preserve the selected album/disc, cancellation and manual wake fallback.

# 0.6.10

- Send the exact AudioShelf page URL to Android helper 0.1.2 for the return after explicitly opening Spotify.

# 0.6.9

- Redesign the Now Playing record as a matte black disc with clear concentric grooves, inspired by the supplied vinyl animation. Remove the reflective streak and oversized decorative mark.
- Add a simple AudioShelf cream-and-green centre label with a small spindle. Keep the stationary tonearm, play/pause behaviour, authentic 33⅓ rpm rotation, reduced-motion accessibility and existing bar dimensions.

# 0.6.8

- Add an optional Android companion helper to wake Spotify and return to the existing AudioShelf page when the selected phone is unavailable. Available-device playback and tracklist handling are unchanged.
- Add an on-demand APK build with configurable private signing and setup instructions.

# 0.6.7

- A distinctive two-tone record label and an off-centre groove marker now make platter rotation obvious while music is playing. Keep the tonearm stationary and show a subtle playing indicator, without changing the Now Playing bar height. Honour reduced-motion preferences.
- Play/pause commands no longer report a false failure if the response from the server fails after Spotify has already obeyed. Confirm ambiguous responses against the real playback state, and distinguish failures in the command from later status-refresh errors.

# 0.6.6

- The Now Playing bar uses Spotify's current album cover, even when the record is not on your shelf. Larger artwork fills the existing bar without making it taller.
- Tap the album cover to open its AudioShelf album. Uncollected canonical albums open in the Record Store; unknown, live or ambiguous releases open a prefilled Record Store search instead.
- A spinning turntable at the right toggles Spotify pause/resume. Previous/next controls are optional, off by default, with a per-account setting and server-enforced preference.
- Preserve playback handoff, progress, and the original album-only queue logic.

# 0.6.5

- Browse every artist as a single horizontally scrolling rail of cover art, with a continuous white shelf underneath. Tap covers to inspect the album; titles and years stay on the album page in compact mode.
- Expand one artist or Expand all to see the detailed grid, with album title and year in a fixed space immediately above the matching cover. Keep the white floating shelf and cabinet styles in Appearance.
- Preserve artist search, album navigation and returning to the right place on the shelf.

# 0.6.4

- Choose continuous white floating shelves or a white record cabinet in Appearance settings, saved per account across devices. Full-width ledges continue through partially filled rows.
- Start with artists collapsed. A small Expand all / Collapse all button replaces the two layout tabs; individual artists still open one at a time.

# 0.6.3

- Switch between the existing open shelves and compact artist dividers. Expand one artist at a time, search the collection, and return from an album to the same open artist. The layout choice is remembered per account in this browser.
- Artist links from Listening Analytics open the matching divider automatically.

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
