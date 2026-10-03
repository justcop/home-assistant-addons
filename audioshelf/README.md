# AudioShelf

An album-first collection for your phone. MusicBrainz supplies artists, studio albums and original tracklists. Spotify supplies playback. Your shelf belongs to AudioShelf and starts empty.

Browse artists, explore their studio albums in release order, add records to your shelf, and play their original tracks. Generic catalogue rules exclude singles, compilations, live albums and remixes; a curated series or explicit album override can include an original soundtrack album. Reissues within the same MusicBrainz release group appear as one album. Genuine collaborative albums appear under each credited artist.

## Install

1. In Home Assistant, refresh the add-on store for the existing repository: `https://github.com/justcop/home-assistant-addons`.
2. Install **AudioShelf**, version **0.3.0**. Enable the sidebar entry if wanted, then start it.
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

External HTTPS and access control are managed by your existing system. An optional `web_password` protects standalone API access. Home Assistant ingress uses HA authentication and bypasses that password only for the Supervisor ingress address, not for a spoofed header. If `web_password` is empty, standalone access relies on your network/proxy controls. The app provides its UI at the root of its routed address. Ingress prefixes are handled automatically.

## Play an original album

1. Open an album. Check its displayed original tracklist once and tap **This tracklist is correct**. If it includes bonus tracks or misses tracks, choose **Album settings → Change original tracklist**, and pick the standard MusicBrainz edition instead.
2. Tap **Match Spotify tracks**. The best complete candidate is saved automatically, preferring the newest explicitly labelled remaster or dated studio mix, and other editions are offered. Recent release dates alone do not prove a new remaster. Spotify search is checked across up to three pages (30 editions), so an unlisted edition can still be selected manually. Existing mappings are kept on upgrade; tap **Find another edition** to rematch an already playable album. Verified manual mappings remain unchanged. This may take several seconds. Matching compares artist, normalized track title, recording-version labels, duration and sequence. A remaster suffix can be ignored, but live, demo, acoustic, generic remix, instrumental and edit labels are preserved. Explicit dated production suffixes such as “2022 Mix” or “2022 Stereo Mix” are allowed, including the type of new mix used on Revolver. Choosing a Spotify edition never changes the original album tracklist.
3. If necessary, paste a Spotify album link in Album settings. If a track still needs review, use its **Edit** button, paste the correct Spotify track link, and confirm the recording. Individual tracks can come from different Spotify albums. Manual corrections survive automatic rematching.
4. Open Spotify on your phone or speaker, play a few seconds so the device is active, then return and tap **Play album**. AudioShelf sends an ordered list of verified track URIs, never an album context. Missing or unverified tracks block playback rather than quietly playing a different album.

Original albums with multiple audio discs keep all those discs. Video discs are excluded. Spotify bonus tracks can occur anywhere in its edition and are skipped by ordered matching. A deluxe release can therefore supply the original songs without its extras becoming part of your shelf.

Shuffle and Repeat are switched off and checked before the playback command. Turn **Autoplay off in Spotify** if you want playback to end in silence. Spotify controls Autoplay, Smart Shuffle and device behaviour; AudioShelf cannot prevent you or another client changing the queue/settings after playback starts. The MVP plays the currently active device and replaces its current playback queue; it does not append an album to an existing queue. Albums over 100 tracks are not supported for playback in this release.

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
docker build --build-arg BUILD_VERSION=0.3.0 --build-arg BUILD_ARCH=amd64 -t audioshelf .
```

## Artwork and replaceable cache

Covers now load through the add-on, rather than relying on the phone browser following remote artwork redirects. AudioShelf tries the selected MusicBrainz edition’s front cover, the release-group cover, then Spotify artwork when connected. Spotify fallback works before track matching as well as afterwards. Downloads are validated as JPEG, PNG or WebP, limited to 5 MB, and follow redirects only to known artwork hosts. Missing artwork shows a record placeholder and is retried after five minutes, or immediately when Spotify becomes connected or the album gains a mapping.

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

The edition picker loads one MusicBrainz page at a time. Use **Load more editions** to inspect later pages. A failed page offers **Retry edition search** and keeps editions already loaded. If no edition matches a strict restriction or allowed format, adjust Settings. Saving preferences keeps all existing tracklists and manual Spotify corrections. **Album settings → Change original tracklist** explicitly replaces an edition and clears its mappings after confirmation. **Country preference for this album** can override the global country order; Magical Mystery Tour starts with the US LP preference, and can be changed to use global preferences.

Artist discography membership is independent of edition countries and formats. The Beatles use [MusicBrainz's Core Catalogue series](https://musicbrainz.org/series/255a357a-909a-4437-9b7a-bfbb814bde77): the 13 original albums, including A Hard Day’s Night, Help!, Magical Mystery Tour and Yellow Submarine. Past Masters stays excluded as a compilation. A verified snapshot keeps this catalogue usable when the series endpoint is temporarily unavailable. Artist Record Store pages have **Manage catalogue**: choose a MusicBrainz release-group series for any artist, or include/exclude individual release-group IDs. Clearing the series restores the generic studio-album rules. Explicit album overrides take precedence and do not remove anything already on your shelf. MusicBrainz standardises series entities and relationships, but does not require every artist to have a canonical discography series.

## Themes

**Settings → Appearance** offers ten themes: Record Store, Midnight, Paper, Forest, Ocean, Sunset, Plum, Monochrome, Amber and High Contrast. Each has a preview. The choice is stored in the collection database and reused on other devices. A browser-local copy applies it promptly on reopening. Colour palettes include light and dark choices; typography and corner styles vary. Browser checks cover all ten palettes, text/button contrast and phone layout.

## Problem reports and known matching corrections

Use **Album settings → Download diagnostic report** after reproducing an issue. Send that JSON file with a description of what went wrong. It includes build information, the saved tracklist and mapping methods, country/format settings, edition decisions, the latest Spotify candidates and matching explanations, artwork source and recent album errors. It excludes OAuth tokens, account credentials, cookies and private configuration. The database retains at most 200 diagnostic events across albums and one latest detailed automatic/manual matching assessment per album. The download includes that album's latest 30 events. API response caches and downloaded artwork remain in the separate replaceable cache folder.

Amnesiac's documented printed cassette-title variations are recognised without broadly trusting fuzzy titles: “Pull Pulk Revolving Doors”, “The Morning Bell Amnesiac” and “Dollars & Cents” can match the corresponding Radiohead recordings when artist and duration agree. Kid A's different Morning Bell remains distinct. Dated generic remixes remain rejected; dated Mix labels and explicit Stereo/Mono Mix labels can match the original studio recording. Both structured and fallback Spotify searches inspect up to 30 editions. Existing manual corrections remain intact.

Legacy cassette tracklists are flagged for review rather than silently replaced. Automatic artwork tries an eligible edition for these albums, while keeping the saved tracklist and matches. **Choose cover from another edition** changes only the image; covers download into the replaceable cache and the chosen edition ID is saved with the collection. A failed cover download preserves a working custom upload. **Restore automatic cover** clears both edition-cover and uploaded-cover overrides. The selected tracklist's front cover is otherwise preferred before the release-group cover and Spotify fallback, and cached artwork is refreshed when the selected edition changes.
