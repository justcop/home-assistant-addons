# AudioShelf

### Session and shelf presentation

Standalone owner and personal-account sessions now persist for up to **one year**, and the optional **Trust this browser for one year** checkbox skips repeat 2FA challenges when signing back in with a password. The 2FA trust is not a password-free login. Explicit logout, credential changes and revocation still invalidate sessions, and temporary support grants retain their own limited expiration.

In the Vinyl interface, a collapsed artist rail displays only its continuous covers and shelf. Expand an artist to reveal **More from ↗** inside its artist-name header, linking to that artist's catalogue in the Record Store.


An album-first collection for your phone. MusicBrainz supplies artists, studio albums and original tracklists. Spotify supplies playback.

## Separate accounts (0.6.0)

Your current library and Spotify connection belong to **owner** after updating. Sign in with username `owner` and the existing `web_password`, or use Home Assistant ingress as before.

In **Settings → Your account → Manage accounts**, the owner can create additional accounts with a username and a password of at least 12 characters. New users start with an empty shelf and connect their own Spotify account in Settings. Everyone uses the same configured Spotify developer client and redirect URI; each AudioShelf account stores its own authorization tokens. Spotify developer app user restrictions still apply to each Spotify account.

Use **Switch account / Sign out** to change libraries. This also works through ingress. After signing out inside ingress, **Use Home Assistant owner account** returns to owner using Home Assistant authentication, including when no standalone owner password is configured. Explicitly signed-in additional users must verify their own password and authenticator for security changes, even through ingress.

Each account has its own collection, track mappings, uploaded covers, artwork cache, catalogue rules, Spotify device choice, interface and theme. Two-factor authentication, recovery codes, trusted browsers and temporary support logins are scoped to that account. A support login uses its account’s username and its temporary support password. The owner manages accounts; other users cannot list or manage them. Users can change their own password in Security settings. The owner password continues to come from Home Assistant configuration.

The owner can disable accounts without deleting their libraries or Spotify tokens. Disabling, password resets and password changes revoke that account’s sessions and trusted browsers. Password resets preserve two-factor authentication unless the owner explicitly checks the recovery option. There is no public registration or automatic sharing between shelves.

Existing owner paths stay unchanged. Additional users have opaque account IDs and these locations:

- Collection and uploaded covers: `<data_directory>/users/<account-id>/`.
- Replaceable artwork and metadata: `<cache_directory>/users/<account-id>/`.
- Spotify credentials and authentication: `/data/audioshelf-private/users/<account-id>/`.
- Private account registry: `/data/audioshelf-private/accounts.db`.

Back up the complete collection directory and add-on private data to restore all accounts, usernames and Spotify connections. A collection export or database backup downloaded by a user contains only their own library; it excludes account credentials and the other libraries. Pending Spotify authorization requests from before the upgrade need to be started again.

An album-first collection for your phone. MusicBrainz supplies artists, studio albums and original tracklists. Spotify supplies playback. Your shelf belongs to AudioShelf and starts empty.

Browse artists, explore their studio albums in release order, add records to your shelf, and play their original tracks. Generic catalogue rules exclude singles, compilations, live albums and remixes; a curated series or explicit album override can include an original soundtrack album. Reissues within the same MusicBrainz release group appear as one album. Genuine collaborative albums appear under each credited artist.

## Shelf layout

The Vinyl shelf starts with artists collapsed. Tap an artist to reveal their records, or use **Expand all** and **Collapse all**. Opening an individual artist closes the others. Returning from an album keeps your browsing position; a fresh visit starts collapsed.

In **Settings → Appearance → Shelf furniture**, choose **Floating shelves** or **White record cabinet**. Both use continuous white ledges across each row, including partially filled rows. The cabinet adds white sides and dividers. This style is saved per account across devices. Search and external artist links work with either style.

## Install

1. In Home Assistant, refresh the add-on store for the existing repository: `https://github.com/justcop/home-assistant-addons`.
2. Install **AudioShelf**, version **0.6.4**. Enable the sidebar entry if wanted, then start it.
3. Use **Open Web UI** for Home Assistant ingress. The standalone UI is also exposed on port **8098** by default, for your existing external-access system.
4. Open the Record Store, search for an artist, browse their studio albums, and add one to your shelf. Spotify is not needed to collect records.

The initial release supports amd64 and aarch64. It runs independently of Vinyl Guardian and Work Audit. It does not request Home Assistant API access, audio hardware access or host-network privileges.

## Connect Spotify

Playback requires Spotify Premium and your own Spotify developer app. The February 2026 development API restrictions are accounted for: search requests use at most 10 results, album reads use individual endpoints, and removed profile fields and batch endpoints are not needed.

1. Create or use your app at <https://developer.spotify.com/dashboard> with the Web API enabled. If your Spotify developer account already has an app, use that app; new development accounts have a one-client-ID limit.
2. Register your **exact HTTPS callback address**, for example `https://audioshelf.your-domain.example/auth/spotify/callback`. Your proxy must send this path to AudioShelf, as well as the app UI and API paths. OAuth returns the **browser** to this address; Spotify does not need to open an inbound server-to-server connection to your host.
3. In add-on configuration, set `spotify_client_id` and `spotify_redirect_uri`, then save and restart. The redirect must match Spotify's registered URI exactly, including any path or trailing slash. The callback path ends in `/auth/spotify/callback`, with no trailing slash.
4. Add your Spotify account to the app's user access allowlist if needed. Development apps allow up to five users, and the owner needs Premium.
5. Open AudioShelf Settings, tap **Connect Spotify**, and approve playback access. Authorization opens a separate tab. Return to AudioShelf afterwards. Refresh connection status if necessary.

AudioShelf uses Authorization Code with PKCE. **No client secret is required.** It requests only `user-read-playback-state` and `user-modify-playback-state`. Tokens refresh automatically; reconnect when Spotify revokes or expires the authorization. Tokens and the session key are in `/data/audioshelf-private`, separate from your shared collection folder, with restrictive file permissions. They never go into exported library files or browser storage.

External HTTPS is supplied by your existing proxy. A `web_password` is required for standalone API access. Home Assistant ingress uses HA authentication and bypasses that password only for the Supervisor ingress address, not for a spoofed header. If `web_password` is empty, standalone API access is locked. Set it in Home Assistant configuration or use ingress. Standalone login cookies are Secure and require HTTPS. The app provides its UI at the root of its routed address. Ingress prefixes are handled automatically.

## Play an original album

1. Open an album. Check its displayed original tracklist once and tap **This tracklist is correct**. If it includes bonus tracks or misses tracks, choose **Album settings → Change original tracklist**, and pick the standard MusicBrainz edition instead.
2. Tap **Match Spotify tracks**. The best complete candidate is saved automatically, preferring the newest explicitly labelled remaster or dated studio mix, and other editions are offered. Recent release dates alone do not prove a new remaster. Spotify search is checked across up to three pages (30 editions), so an unlisted edition can still be selected manually. Existing mappings are kept on upgrade; tap **Find another edition** to rematch an already playable album. Verified manual mappings remain unchanged. This may take several seconds. Matching compares artist, normalized track title, recording-version labels, duration and sequence. A remaster suffix can be ignored, but live, demo, acoustic, generic remix, instrumental and edit labels are preserved. Explicit dated production suffixes such as “2022 Mix” or “2022 Stereo Mix” are allowed, including the type of new mix used on Revolver. Choosing a Spotify edition never changes the original album tracklist.
3. If necessary, paste a Spotify album link in Album settings. If a track still needs review, use its **Edit** button, paste the correct Spotify track link, and confirm the recording. Individual tracks can come from different Spotify albums. Manual corrections survive automatic rematching.
4. Tap **Play album** and choose your phone or speaker on first use. If the chosen device is unavailable, tap **Open Spotify**. You can stay in Spotify while AudioShelf waits for that device and starts playback. AudioShelf sends an ordered list of verified track URIs, never an album context. Missing or unverified tracks block playback rather than quietly playing a different album.

Original albums with multiple audio discs keep all those discs. Video discs are excluded. Spotify bonus tracks can occur anywhere in its edition and are skipped by ordered matching. A deluxe release can therefore supply the original songs without its extras becoming part of your shelf.

Shuffle and Repeat are switched off and checked before the playback command. Turn **Autoplay off in Spotify** if you want playback to end in silence. Spotify controls Autoplay, Smart Shuffle and device behaviour; AudioShelf cannot prevent you or another client changing the queue/settings after playback starts. AudioShelf plays your explicitly chosen device and replaces its current playback queue; it does not append an album to an existing queue. Albums over 100 tracks are not supported for playback in this release.

## Persistence and backups

The SQLite database lives at `/share/audioshelf/audioshelf.db` by default. It contains your shelf, browsed album records, canonical editions, track mappings, settings, catalogue overrides, cover references and bounded diagnostic events. Raw API responses are stored separately in the replaceable cache. Updates and container rebuilds preserve this folder. Changing `data_directory` selects a different database; copy the existing folder first if moving your collection.

Use the local Home Assistant `/share` filesystem for the live database, rather than a network-mounted SMB/NFS directory. Only one running AudioShelf instance should use it. Connections are short lived and transactional; canonical edition changes and mapping writes are serialized. Schema version checks prevent an older app silently opening a newer database.

Settings provides a portable **Export collection** JSON file and a consistent **Download database backup**. The newest seven database backups are also retained in `/share/audioshelf/backups/`. Backups exclude pending OAuth verifier data and do not include Spotify tokens. Include `/share/audioshelf` in your host backups separately; do not assume the add-on's own `/data` backup covers the shared folder. To restore a database, stop the add-on, replace `audioshelf.db` with the backup and start again. Reconnect Spotify if its private add-on data was lost.

## Mobile app

The UI adapts to phones and desktops, with Shelf and Record Store navigation, artwork, search, filtering and chronological album grids. Open your standalone HTTPS address in your phone browser and choose **Add to Home Screen**. The PWA manifest and shell service worker are included. The service worker caches only the UI shell, not library/API responses or OAuth callbacks. Browsing the collection needs a connection to your add-on. Home Assistant ingress does not register the service worker because its session URLs are temporary.

## Development

```bash
cd audioshelf
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest PyYAML
AUDIOSHELF_DATA_DIRECTORY="$PWD/local-data" \
AUDIOSHELF_PRIVATE_DIRECTORY="$PWD/local-data/private" \
AUDIOSHELF_PORT=8099 .venv/bin/python -m app
```

Options can come from `/data/options.json`, another JSON file selected by `AUDIOSHELF_OPTIONS`, or environment overrides: `AUDIOSHELF_DATA_DIRECTORY`, `AUDIOSHELF_SPOTIFY_CLIENT_ID`, `AUDIOSHELF_SPOTIFY_REDIRECT_URI`, `AUDIOSHELF_SPOTIFY_MARKET`, `AUDIOSHELF_WEB_PASSWORD`. Development data and credentials must stay out of Git.

```bash
.venv/bin/python -m pytest tests -q
node --check app/static/app.js
bash -n run.sh
docker build --build-arg BUILD_VERSION=0.6.4 --build-arg BUILD_ARCH=amd64 -t audioshelf .
```

## Artwork and replaceable cache

Downloaded artwork is retained only for albums on your shelf. Browsing the store and cover picker does not save image files; removing an album evicts its downloaded cover. Older store-only artwork caches are removed on startup. Uploaded covers remain durable. Edition thumbnails use authenticated same-origin preview URLs, return a placeholder when unavailable, and are never cached.

Covers load through the add-on, including edition thumbnails. AudioShelf defaults to the album-level MusicBrainz release-group cover, then a suitable edition cover, then Spotify artwork when connected. An explicitly chosen edition cover or uploaded image takes priority. Spotify fallback works before track matching as well as afterwards. Downloads are validated as JPEG, PNG or WebP, limited to 5 MB, and follow redirects only to known artwork hosts. Missing artwork shows a record placeholder. Shelf albums retry after five minutes, or immediately when Spotify becomes connected or the album gains a mapping; store artwork and previews retry on the next request.

`cache_directory` defaults to **`/share/audioshelf-cache`**, separate from **`/share/audioshelf`**. It contains `metadata.db` for reproducible MusicBrainz/Spotify API responses and `artwork/` for downloaded images. You manage backup exclusions for this folder yourself. No Home Assistant backup settings are changed. The paths must be separate, with neither containing the other. Any custom path must be accessible inside the add-on; the `/share` filesystem is already mapped. Environment override: `AUDIOSHELF_CACHE_DIRECTORY`.

Downloaded CAA covers refresh after 30 days, and Spotify fallback covers after one day. Artwork storage is capped at 512 MB by evicting the oldest downloaded images. To discard the cache, stop AudioShelf, delete this cache folder, and start it again. Artwork and API data rebuild on demand without changing your shelf or mappings. Upgrading from 0.1.0 removes the old raw-response cache from the collection database and compacts it; your shelf, tracklists, mappings and authorization states survive.

In **Album settings**, upload a JPEG, PNG or WebP cover (up to 5 MB and 16 megapixels), or choose **Restore automatic cover**. Uploaded covers are personal choices, so their original files remain in **`/share/audioshelf/custom-artwork`**, alongside the collection. Include those files in your collection-folder backup. A database-only backup preserves their references but cannot contain image files. Export collection is a JSON catalogue export, not a cover-file export. Cover choice is independent of Spotify playback edition.

The automated suite uses deterministic metadata and player fixtures. Real account authorization, live MusicBrainz catalogue completeness and playback on your own Spotify Connect device must be checked after installation. No real tokens or audio are needed by CI. `/health` checks database connectivity. Settings shows the package version, delivery channel and revision marker; `build.json` records these values. This MVP deliberately has one published main-channel add-on, with no runtime branch-switching code. Development branches can be tested locally before publishing a version bump.

MusicBrainz requests have a shared one-request-per-second limiter, persistent metadata caching, pagination and bounded retries. Cover images come from the Cover Art Archive release-group endpoint and fall back visually when unavailable. MusicBrainz cannot guarantee every artist is perfectly classified or every edition correctly annotated. That is why the original tracklist is visible, editable by release selection and reviewed before its first playback.

## API reference sources

- <https://musicbrainz.org/doc/MusicBrainz_API>
- <https://musicbrainz.org/doc/Cover_Art_Archive/API>
- <https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow>
- <https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide>
- <https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback>
- <https://developers.home-assistant.io/docs/apps/configuration/>

## Edition preferences and curated catalogues

In **Settings → MusicBrainz releases**, country preferences default to **GB, US, XW, XE**, followed by any other country. Audio formats default to **vinyl, CD, digital**, in that order; cassette and other formats are excluded unless enabled. Change country order by editing the comma-separated codes, and format order with the arrows. **Only use these countries** turns the country preference into a strict filter. Unexpanded standard editions win first, then preferred country, closeness to the original release year, preferred format and earliest date. This avoids a later unlabelled vinyl reissue beating an original-year CD in the same country. All audio media in an edition must use an enabled format; video discs are skipped. Spotify playback still prefers the latest suitable labelled remaster or dated studio mix.

The edition picker loads one MusicBrainz page at a time. Use **Load more editions** to inspect later pages. A failed page offers **Retry edition search** and keeps editions already loaded. If no edition matches a strict restriction or allowed format, adjust Settings. Saving preferences keeps all existing tracklists and manual Spotify corrections. **Album settings → Change original tracklist** explicitly replaces an edition and clears its mappings after confirmation. **Country preference for this album** can override the global country order for any album. No album has a built-in regional exception.

Artist discography membership is independent of edition countries and formats. On any artist's Record Store page, open **Manage catalogue → Find catalogues** to search for MusicBrainz release-group series. Search starts with the artist's name; you can enter another catalogue name or paste a series link. Results are suggestions, not automatically selected rules: a series may describe a core catalogue, regional releases, reissues or a thematic collection. Review its purpose, choose it, then **Save curated series**. The selected series must contain at least one known album credited to that artist.

The same rules apply to every artist. Series membership narrows the catalogue and can admit original soundtrack albums; live albums, compilations, remixes and other excluded types remain outside unless individually overridden. Catalogue classifications alone cannot reliably distinguish an artist’s core albums from regional repackagings. A user-selected series supplies that membership evidence without relying on artist names or bundled album lists. Clearing the series restores generic studio-album rules. Individual include/exclude overrides take precedence, and browsing collaborative albums uses the catalogue of the artist being viewed.

Every successful series lookup retains a last successful membership snapshot with the collection. Series membership remains available if the series endpoint temporarily fails, including after a restart or replaceable-cache deletion. Other uncached MusicBrainz requests still need the service to be reachable. Existing explicit catalogue and country preferences are preserved, and no catalogue change removes records already on your shelf or replaces tracklists and mappings.

## Themes

**Settings → Appearance** offers ten themes: Record Store, Midnight, Paper, Forest, Ocean, Sunset, Plum, Monochrome, Amber and High Contrast. Each has a preview. The choice is stored in the collection database and reused on other devices. A browser-local copy applies it promptly on reopening. Colour palettes include light and dark choices; typography and corner styles vary. Browser checks cover all ten palettes, text/button contrast and phone layout.

## Problem reports and recording matching

Use **Album settings → Download diagnostic report** after reproducing an issue. Send that JSON file with a description of what went wrong. It includes build information, the saved tracklist and mapping methods, country/format settings, edition decisions, the latest Spotify candidates and matching explanations, artwork source and recent album errors. It excludes OAuth tokens, account credentials, cookies and private configuration. The database retains at most 200 diagnostic events across albums and one latest detailed automatic/manual matching assessment per album. The download includes that album's latest 30 events. API response caches and downloaded artwork remain in the separate replaceable cache folder.

Track matching uses the printed track title and the title of its linked MusicBrainz recording. If a track remains uncertain, AudioShelf looks up that exact recording ID for its title, aliases and ISRCs, then checks the Spotify candidates again. Printed titles and recording names can differ. Matching uses evidence attached to the exact recording rather than a list of title substitutions. Alternate recording names require a matching artist and a corroborating duration; live, demo, acoustic, edit and remix distinctions remain protected. Recordings with similar names but different durations or versions remain distinct. Metadata is retained with the collection, and lookups use the shared MusicBrainz cache and rate limiter. A lookup failure leaves the track available for manual review and appears in diagnostics.

Existing albums gain this recording metadata when **Match Spotify tracks** or **Find another edition** is used, without replacing their printed titles, track order or review status. Automatic rematching preserves confirmed manual corrections. Dated generic remixes remain rejected; dated Mix labels and explicit Stereo/Mono Mix labels can match the original studio recording. Both structured and fallback Spotify searches inspect up to 30 editions.

Legacy cassette tracklists are flagged for review rather than silently replaced. Automatic artwork tries an eligible edition for these albums, while keeping the saved tracklist and matches. **Choose cover from another edition** changes only the image; covers download into the replaceable cache and the chosen edition ID is saved with the collection. A failed cover download preserves a working custom upload. **Restore automatic cover** clears both edition-cover and uploaded-cover overrides. The MusicBrainz release-group cover is the automatic default, followed by a suitable edition cover and Spotify fallback, and cached artwork is refreshed when the selected edition changes.

Multi-disc albums show a Play disc button for each disc in the selected original edition. Only that disc’s verified tracks are sent to Spotify, in order. Turn Spotify Autoplay off for silence afterwards. Disc boundaries follow the chosen MusicBrainz edition, so a vinyl and CD edition may divide an album differently.

In Settings, choose a preferred Spotify playback device. Open Spotify on that device first if it does not appear, then refresh the device list. AudioShelf remembers the selection and does not switch to another device when it is unavailable. Enable “Open Spotify after pressing Play on this browser” on your phone to try opening the Spotify app after playback starts. A visible Open Spotify link is provided if the browser blocks automatic app opening. When the device is unavailable, tap Open Spotify. You can stay there: the server waits up to one minute and sends the synced tracklist when the chosen device appears.

## Checks and test maintenance

The numbered, timestamped [test inventory](TEST_INVENTORY.md) lists all backend cases and the other release checks. CI fails when tests, test fixtures or check configuration change without an updated inventory and review note. Previous versions are retained in Git history. This enforces review visibility and freshness; human review still decides whether coverage is redundant.

Refresh after reviewing changes with `python audioshelf/scripts/test_inventory.py --update --review-note "Describe the review and any limitations"`.

Tests are in [tests/](tests/). GitHub publishes results under the repository's **Actions → AudioShelf checks**, and on each pull request's **Checks** tab.

| Area | What it protects |
| --- | --- |
| Catalogue and release filters | Shared catalogue rules, regional editions, country and format preferences, pagination |
| Recording matching | Alternate recording names, artist and version distinctions, ordered tracks and bonus-track exclusion |
| Storage and routes | Collection persistence, upgrades, manual corrections, authentication and input validation |
| Spotify playback | Exact album or disc queues, device selection, unavailable devices and playback controls |
| Artwork and transport | Covers, caching, API failures and retries |
| Browser flows | Shelf, settings, catalogue selection, edition picker and playback retry |
| Container build | Add-on packaging |

List every backend test case without running it:

```sh
python -m pytest audioshelf/tests --collect-only -q
```

Run the backend suite with readable test names:

```sh
python -m pytest audioshelf/tests -v
```

Keep tests that protect an observable requirement or a meaningful past failure. Real album examples are fixtures for shared behaviour, never production exceptions. Consolidate duplicate scenarios where they protect the same behaviour. When removing a feature, remove tests for its obsolete behaviour; retain migration or rejection tests only where old data or calls still need handling. Test count is not a quality target. Run relevant checks during editing and the complete suite before publication; repeat only after changes or failures justify it. Release summaries should state which check groups passed, failed or were not run, and identify remaining live-device checks.


## Security

Version 0.4.0 locks standalone access when no web password is set. Existing installations using an empty password must configure a long, unique password in Home Assistant, then restart. Home Assistant ingress remains available through its own authentication. Put the standalone site behind HTTPS; ordinary standalone login sessions expire after one year.

In **Settings → Security → Set up authenticator**, confirm your owner password, scan the locally generated QR code (or enter its manual setup key), then enter a six-digit authenticator code. Setup expires in ten minutes and two-factor authentication stays off until confirmed. Save the ten recovery codes when shown. Each code works once alongside your owner password. Codes are never shown again. A freshly used authenticator code cannot be reused; wait for the next code for another sensitive action.

At login, **Trust this browser for one year** remembers the second factor. If you sign out or the one-year session expires, your password is required again; the trusted browser only skips the second factor. **Revoke other sessions and trusted browsers** invalidates existing sessions and trusted-browser credentials; logout also forgets the current trusted browser. Changing the configured owner password invalidates existing sessions and trusts on restart. Disabling two-factor authentication requires a fresh owner factor; owner-authenticated Home Assistant ingress can recover access if you lose your authenticator and recovery codes. Protect your Home Assistant login accordingly.

For temporary testing, enable **Allow temporary support access (advanced)** in Home Assistant add-on configuration and restart. It is off by default. In AudioShelf **Settings → Security**, create a login with **View only** (default) or **Allow changes and playback**, lasting one to eight hours (default one hour). Share only the generated temporary password, shown once. Use the normal login screen over HTTPS. Temporary logins cannot change security settings, create more credentials, connect/disconnect Spotify, or export backups/diagnostics. View-only access cannot invoke modifying endpoints or playback. Revocation and expiry terminate already logged-in sessions too. Turning the Home Assistant toggle off and restarting revokes all support credentials, even if later re-enabled.

Authentication secrets, recovery-code hashes, support-password hashes, server sessions and security activity are stored in private add-on data, separate from collection exports and database backups. Back up private add-on data securely as part of your Home Assistant backups. Login and setup attempts are throttled in persistent storage; forwarded IP headers do not bypass limits. Proxies sharing an address can share the throttle. The app records recent security activity without passwords or authenticator secrets. Two-factor authentication protects login, not vulnerabilities elsewhere in the app.

### Recover 2FA from Home Assistant add-on options

If you lose your authenticator and recovery codes, open **Home Assistant → Settings → Apps (or Add-ons) → AudioShelf → Configuration**. Set **2FA recovery request (advanced)** (`two_factor_reset_request`) to a new value, such as `reset-1`. Save and restart AudioShelf. This disables 2FA, removes its recovery codes and pending setup, revokes sessions, trusted browsers and temporary support logins, and clears login throttling. Your normal standalone web password is still required. Spotify connection and your collection are preserved.

Log in over HTTPS with your web password and enrol a new authenticator under **AudioShelf → Settings → Security**. You may clear the recovery-request field afterwards. Each changed nonblank value is processed only once; leaving it set does not disable your new authenticator on later restarts. For a future reset, use a different value, such as `reset-2`. No AudioShelf login is required to use the Home Assistant configuration recovery option.

Browser updates: JavaScript and CSS URLs include the release and content fingerprint. The service worker fetches fresh shell files, activates automatically and removes older shell caches. An open app checks for updates every minute and when brought back into focus; use Reload AudioShelf when prompted. Edited forms and open dialogs require confirmation before reload, and in-flight changes block reload. Ingress uses the same update prompt without registering a service worker. The first upgrade from an older build may still need one normal browser reload to install this mechanism.

## Vinyl and Classic interfaces

AudioShelf opens in the Vinyl interface. My Shelf displays larger front-facing sleeves grouped by artist, while the Record Store uses denser racks and artist dividers. Phones show two records across. Open a sleeve for its tracklist, then use **Back to browsing** to return to your search and position. Adding a record keeps the store open.

Choose **Settings → Appearance → Classic** for the original interface, or **Vinyl** to switch back. This preference and the existing ten colour palettes are shared across your devices.

**On the turntable** follows the connected Spotify account every 15 seconds while AudioShelf is visible, with faster checks during playback startup and at song boundaries. The song progress bar shows elapsed and total time, moves between Spotify checks and freezes when playback is paused or unavailable. It distinguishes playing, paused and unavailable status. Opening another album does not change playback. Library tracks link back to their sleeve when the mapping identifies the album; ambiguous or external tracks show Spotify’s album name without claiming a library match. No additional Spotify scopes are required.

Album settings retains cover selection, original tracklist changes, Spotify editions and diagnostic downloads. The new interface uses the same library and artwork cache rules: uncollected store covers are not saved locally.

Spotify handoff: Open Spotify launches the app without selecting an album or track. AudioShelf sends the synced tracklist through its playback API. Choose a device before first playback. AudioShelf remembers it and never substitutes another available device. Change device is available beside Play. If your phone is missing, open Spotify on that phone and return to refresh the chooser. If the chosen device is unavailable, the server waits up to one minute for it and sends the same synced album or selected disc. Open Spotify and stay there; the AudioShelf page can be hidden. Now Playing shows the requested first track as Starting immediately and confirms Playing with fresh Spotify status. Closing the dialog or navigating away cancels the retry. Your preferred device is never silently replaced by another.

Mobile covers: the browser selects 128, 320 or 640 pixel WebP images for the displayed size and screen density. Small originals are not enlarged. Shelf variants share the replaceable artwork cache and are evicted with removed albums; store-only browsing and edition previews do not persist derived images. Uploaded originals remain durable. Private ETag revalidation saves repeat image transfers while requiring authentication and checking for cover changes. The update prompt is a sticky contrasting banner; its Reload button still protects unsaved edits and in-flight changes.

## Spotify-verified shelf editions

When you add an album from the Record Store, AudioShelf checks MusicBrainz editions against the Spotify albums accessible to your connected Spotify account. Official MusicBrainz per-track artist credits are respected, even if a track's performer differs from the release-group artist; name/version/duration verification remains strict. Only MusicBrainz tracklists for which **every canonical track** has a verified one-to-one Spotify mapping are offered for selection; no bonuses are added to the Spotify queue. The picker scans up to four editions per page, retaining the configured preferred countries and audio formats. Use **Check more editions** to search the remaining candidates. Choosing an edition saves its Spotify mapping along with the new shelf entry, and you still review the canonical tracklist before playing it. Changing an existing album's tracklist uses the same safe, fully matched selection. Cover-art choices are not affected. Spotify must be connected to verify playback editions; browse and cover selection remain possible without Spotify.

## Playback startup feedback

When Spotify is unavailable on your selected phone, AudioShelf shows the chosen album as STARTING while the Android helper wakes it. During the first 15 seconds of the handoff, the visible AudioShelf page checks its own playback job twice per second, including immediately on return from the helper. The waiting dialog reports when Spotify Connect finds the phone and when Play is submitted. The wake dialog closes when Spotify accepts Play, allowing normal browsing while server confirmation continues. The turntable remains STARTING until the server verifies the intended phone and first track, then switches immediately to PLAYING without waiting for a redundant Spotify player request. Playback failures are shown rather than silently reported as success, with no page reload or manual refresh. Long-running jobs taper to one-second then two-second polling; cancelling the dialog still cancels the pending playback job.

## Android Spotify handover helper

An optional [Android companion helper](android-helper/README.md) can wake Spotify and return to the installed web app when the preferred phone is unavailable. Normal playback stays in the web app. The helper includes a GitHub Actions APK build and Spotify developer setup instructions.
