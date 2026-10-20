# 0.6.35 — Unstick cold Spotify playback before Play dispatch

- Reuse the phone found by the background handoff instead of querying Spotify Connect twice before beginning Play. The command remains pinned to that exact selected phone.
- When Spotify acknowledges a paused transfer but keeps reporting a different current player, permit one explicitly device-targeted album Play **only if** the exact phone remains available and shuffle/repeat are known to be off. No speaker fallback, no automatic repeat of an ambiguous Play, and no change to canonical album track URIs.
- Treat a 404 reading the inactive Spotify player as an empty player instead of confusing it with a missing Spotify Connect phone.
- Publish safe, specific preparation stages and a finite, allowlisted retry reason to the authenticated Android job status. Preserve the server's strict final phone/track/playing verification and cancellation behaviour.

# 0.6.34 — Don't cancel Spotify startup on navigation

- Keep an active background playback handoff running when a user closes the startup dialog or browses to another AudioShelf page. This prevents the app from cancelling Spotify activation before Play is sent, as seen in the 16:55 Android log.
- Add an explicit **Cancel playback** action to the startup dialog. Choosing another album or a different playback device still safely cancels/replaces the older job.
- Publish a short, allowlisted cancellation reason in the owner and one-job, bearer-protected helper status. Distinguish explicit user cancellation, device change, replacement, direct playback, logout and Spotify disconnection without exposing private identifiers.
- Preserve one queued Spotify Play and the existing exact-device/first-track confirmation rules. No automatic retry of an ambiguous Play.

# 0.6.33 — Spotify playback-verification evidence

- Distinguish Spotify returning no current track from actually reporting the wrong track after accepting Play; a device-only player response can no longer be mislabeled as an incorrect recording.
- Publish a minimal, bounded observation to the short-lived, bearer-protected Android helper status: requested and reported track names, hashed track-ID fingerprints (including Spotify relinking), playback and device-match flags, and Spotify player-state age when available.
- Keep full Spotify player payloads, raw device and track IDs, Spotify credentials and job capabilities out of Android diagnostic logs. Do not change canonical queues, preferred device, acceptance criteria or automatic replay behaviour.

# 0.6.32 — Digital-first artwork and server-side bulk rebuild

- Prefer digital/Spotify front covers over scanned CD and vinyl sleeves while respecting custom uploads and individually selected covers.
- Back covers can come from any official edition of the same album. Download and retain back artwork locally, including 320px/640px WebP sizes.
- New Settings action to rebuild all cover art, on a background worker with server-side progress tracking; preserves manual covers.
- Phone and PWA cache back-cover thumbnails as well as fronts, with separate account-specific caching.

# 0.6.30 — Atomic offline shelf refresh

- Save a complete new offline shelf revision before activating it. Interrupted saves preserve the previous usable shelf instead of mixing old and new albums.
- Keep only required display metadata in the offline snapshot, remove superseded data after success, and tolerate optional cover preload failures.

# 0.6.29 — Integrated OAuth handoff and offline artwork

- Keep Spotify account linking usable from Android's embedded WebView by opening OAuth in the system browser rather than depending on an unsupported popup.
- When browsing saved album details without the server, allow large album artwork requests to fall back to the matching account's cached 320px thumbnail.
- Preserve independent PWA use, existing Spotify server-owned playback and the older Android companion fallback.

# 0.6.28 — Optional Android integration and saved offline shelf

- Keep the standalone PWA and self-hosted server authoritative for albums, accounts and Spotify playback.
- Detect the secure integrated Android bridge and wake Spotify within the same Android activity without opening the separate helper screen.
- Use optional Spotify media-session events to refresh Now Playing promptly; Android notification settings and launcher shortcuts are managed natively.
- Add an explicit, per-account offline shelf download with read-only artist, album and tracklist browsing, backed by the existing artwork cache. Clear saved data on sign-out or switching accounts; never cache credentials, Spotify playback or record-store searches.
- Keep the browser helper fallback unchanged when not running in the Android wrapper.

# 0.6.27 — Flip album sleeves to see back-cover artwork

- Tap the large album cover in Classic or Vinyl album view to flip to its reverse; tap again to see the front. Record-rack covers still open albums.
- Fetch back-cover art from the chosen sleeve edition first, then the selected canonical MusicBrainz release. Never substitute unrelated releases, Spotify front art or a placeholder for an unavailable reverse.
- Download only on first request, report absent artwork without flipping, and use independent server-side artwork caching and thumbnail sizes for albums on your shelf.
- Respect account permissions, remove cached reverse artwork when covers are reset or albums leave your shelf, and provide back-cover regression tests.

# 0.6.26 — Resumable tracklist scans and true recording equivalence

- Fetch only two eligible MusicBrainz pressings per response, preventing a slow rate-limited batch from holding a single proxy request open for too long.
- Cache successfully verified batches per account, album, market and release preferences for 30 minutes. Retry temporary request failures automatically at the same cursor, preserving already counted releases without duplicating progress.
- Save browsing progress for 30 minutes in the current browser tab so closing and reopening the tracklist picker or reloading the page does not restart a partially completed scan.
- Merge tracklist variants with different printed song titles only when their Spotify verification proves the same exact ordered recording identifiers. Preserve separate variants for genuinely different audio, disc arrangements, and unreliable matches.
- Improve scan progress feedback and test interruption recovery, cached retries, multi-page scans and safe equivalence grouping.

# 0.6.25 — Full regional tracklist scan and safer album matching

- Automatically inspect all available GB, Worldwide (XW) and US MusicBrainz pressings in successive eight-release batches, with live progress, pause/resume, and retry. Other countries are excluded from this tracklist picker without modifying saved MusicBrainz settings or cover selection.
- Consolidate equivalent verified musical tracklists and rejected variations across every page, and keep expanded tracklist details and scrolling intact while results update. The progress counter distinguishes eligible from total published releases.
- Confirm trailing title details in combined medley titles when performer, version and timing independently corroborate them. For albums of at least five songs, one moderately different recording duration may be verified only when every other canonical song is independently matched and the titles and artist are identical.
- Preserve strict checks against wrong performers, live or alternate versions, large timing discrepancies, partial albums and Spotify bonus-track additions. Final selection still independently revalidates every Spotify mapping before saving.
- Add backend and browser regression tests for regional filtering, multi-page automatic discovery, matching safeguards and unchanged library data.

# 0.6.24 — Verify tracklist variants before selection

- Group MusicBrainz pressings into musical tracklists, then verify every canonical song on a single Spotify album before offering that tracklist or its individual disc/side layouts.
- Preserve the green/red bonus-track comparisons and real multi-disc/vinyl-side choices. Selecting an offered layout presents the already verified Spotify editions; saving still independently rechecks every mapping.
- When a structured Spotify search lacks a complete match, retry a broader search. Prefer verified recordings during ordered alignment and inspect linked MusicBrainz recording metadata in memory when it may resolve a near match, without changing stored albums.
- Exclude unusable variants from the chooser while providing expandable track-level rejection reasons. Add regression coverage for incomplete matches, layout-specific failures, search fallback and unchanged collection data.

# 0.6.23 — Coloured tracklist differences

- Highlight added MusicBrainz tracks (+1, +2, etc.) in prominent green and missing tracks (−1, −2, etc.) in red, including the song names. Neutral ordering messages remain unchanged.
- Preserve escaped track titles and provide distinct, readable green/red colours across light, dark and high-contrast themes.
- Add browser regression assertions for the +/− labels, escaped titles and computed light/dark colours.

# 0.6.22 — Choose musical tracklist first, then Spotify

- Group equivalent MusicBrainz pressings by the actual ordered song/performer sequence; ignore recording identifiers, remaster label suffixes and tiny duration changes but retain bonus tracks, missing songs, alternate takes and reordered tracks as distinct options.
- Show a plainly worded tracklist comparison against the preferred original, naming added or removed songs and highlighting reordered sequences. Expand full tracklists when desired.
- Offer separate real disc layouts for matching musical tracklists. Optionally split playback into vinyl sides only when the selected MusicBrainz release has trustworthy A1/B1-style printed track numbers; never infer a midpoint. Preserve entire-disc playback separately.
- Query Spotify only after the listener has chosen the tracklist and disc/side structure. Verify every selected canonical track before updating the shelf; never queue Spotify bonus tracks or change the selected device.
- Continue paginated MusicBrainz discovery eight pressings at a time and combine equivalent options across pages. Respect existing preferred countries and formats and MusicBrainz rate limiting and caching.
- Clear saved vinyl-side boundaries whenever the underlying MusicBrainz edition changes; keep existing album/disc playback backward compatible.

# 0.6.21 — Continuous vinyl shelving

- Restore the artist name bar as a full-width, independently clickable expand/collapse toggle at all times. When expanded, show the “More from” link on its own slim action line within the white header, without replacing any part of the toggle.
- Remove gutters between adjacent artist sections and trailing blank rack padding so the artist bars and white ledges form a continuous wall of shelves across floating and cabinet views.
- Add browser regression checks for seamless adjoining shelves, full-width toggles, independent expanded actions, repeat expand/collapse behaviour and mobile widths.

# 0.6.20 — One-year login persistence and artist-header store links

- Keep standalone AudioShelf login sessions, their signed cookies and optionally trusted 2FA browsers valid for one year (365 days), rather than 12 hours and 30 days. Temporary support accounts remain bounded by their shorter grants. Signing out, password/2FA resets and session revocation continue to invalidate access.
- Update login and security-screen descriptions to match the new durations.
- Move Vinyl shelf's “More from” link from beneath each artist's albums onto the artist-name bar. Show it only when that artist is expanded (including Expand all), and link directly to the artist's record-store catalogue. Keep shelf ledges uninterrupted.

# 0.6.19

- Fix mixed-performer album matching, including the original 13-track *Yellow Submarine*: an album's MusicBrainz release-group artist does not necessarily perform every track.
- Read MusicBrainz release-track performer credits and their canonical artist identities when fetching a MusicBrainz edition. Require Spotify's track-level artist to match one of those exact identities; fall back to the album artist only when the track has no performer credits. No Beatles, George Martin, orchestra, or other artist-specific matching exceptions.
- Preserve existing safeguards against wrong performers, cover versions, remixes, demos, live recordings and implausible track durations.
- New Spotify-verifiable release picker now accepts complete legitimate mixed-performer editions, rather than excluding them for album-artist mismatches.
- Persist track performer credits in database schema v5 with non-destructive migration from earlier versions. Existing shelf membership, album review, artwork and manually corrected Spotify mappings are preserved.
- Includes mixed-performer soundtrack and unrelated collaborative-album regressions, mismatched artist and recording rejection, complete 13-track Spotify mapping, and migration tests.

# 0.6.18

- When adding an album from the Record Store, select a MusicBrainz release whose **entire canonical audio tracklist** can be verified against the connected Spotify catalogue in the configured market. Do not offer partially matched releases or silently save unplayable albums.
- Paginate the expensive MusicBrainz/Spotify compatibility checks in small batches (four editions at a time), with a continuation button if a batch has no complete matches. Retain the album's country, media, studio-catalogue and remaster preferences.
- Choosing an edition preflights every track against Spotify before replacing any stored tracklist or mappings, then saves verified Spotify mappings together with shelf membership. The first-play tracklist review remains required.
- Changing a MusicBrainz tracklist on an existing album now also shows only fully playable choices and saves the Spotify mapping in the same operation, instead of clearing matches.
- Cover-art edition browsing remains independent of Spotify playback and is not filtered. Legacy import/API addition remains compatible; disconnected users can still browse without hiding the MusicBrainz catalogue.
- Multi-user accounts get independent Spotify market/session checking. Add backend and browser regressions for incomplete tracks, atomic rejection, paginated candidates and playable choices.

# 0.6.17

- Confirm the chosen Spotify phone and first track every 500 ms during the first ten seconds after Play acceptance, once per second for another ten seconds, then at the existing interval. Retain stricter test/configured intervals and one-minute timeout. Device-discovery cadence is unchanged.
- On the server's authenticated Play-accepted acknowledgement, release the blocking wake dialog and keep monitoring final verification in the background, even if the user browses or opens another dialog. Keep the turntable at STARTING, do not claim success or cancel the server job. Closing the waiting dialog *before* Play acceptance still cancels the request.
- As soon as the server independently verifies the intended device and first track are playing, update the turntable to PLAYING synchronously and show the success notification. Do not require another potentially delayed/stale Spotify status request to render success; continue normal status refresh and stale-track protections.
- If background verification fails, report that honestly with a toast and clear the pending startup state. Starting another album still cancels/replaces a pending job.
- Preserve Android helper 0.1.14, the selected device and canonical album/disc tracklist. Includes server/browser regression tests.

# 0.6.16

- Check Spotify Connect for the selected phone every 500 ms during the first five seconds of a cold wake, every second until 15 seconds, then at the existing slower interval; respect shorter test/configuration intervals and the existing job cancellation and one-minute expiry.
- Expose only the authenticated job phase and Play-accepted indicator to the PWA so it can show real, accurate startup progress without exposing helper credentials or issuing an extra Spotify API call.
- While the PWA is visible, check its background playback job every 500 ms for 15 seconds, every second until 30 seconds, and every two seconds thereafter. Poll immediately when returning from the Android helper.
- Show device discovery, Play dispatch and verification in a live waiting dialog. Keep the album's turntable at STARTING until the server verifies the exact phone and first track; then update Now Playing automatically and dismiss the dialog.
- Preserve helper 0.1.14's return-on-discovery behaviour, the chosen device, canonical album/disc tracklist and cancellation semantics.

# 0.6.15

- Expose a persistent, authenticated Play-accepted flag as soon as Spotify successfully responds to the canonical album/disc playback request. Confirmation of the selected device and exact track continues independently.
- Log device-discovery timing, Spotify device request duration and Play acceptance time through the existing read-only helper endpoint, without sending Spotify tokens or playback commands to the helper.
- Poll Spotify Connect for the preferred waking phone every second for the first 15 seconds, then return to the existing two-second interval. Do not switch devices, repeat ambiguous Play requests or weaken playback verification.

# 0.6.14

- Respond immediately to helper playback-status snapshots and report device discovery, Play dispatch and exact-track confirmation progress. Keep helper reads available during slow Spotify Play requests.
- Preserve per-job authentication, cancellation, selected device and canonical tracklist handling. Helper 0.1.12 uses this feedback without requiring Android notification access.

# 0.6.13

- Locally cache collected albums' resized WebP covers in the standalone PWA, preserving saved images across ordinary updates without touching the existing Home Assistant server cache.
- Show the number of albums, thumbnails and measured image cache size in Settings → Offline artwork; preload your shelf, refresh its size or clear it from the phone.
- Partition the cache per AudioShelf account and purge old-account covers on signout/switch; invalidate manually changed artwork, recheck previously cached covers periodically and avoid caching Record Store previews or Spotify content.
- Preserve the verified-playback Android helper integration shipped in 0.6.12.

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
