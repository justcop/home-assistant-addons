# AudioShelf

An album-first collection for your phone. MusicBrainz supplies artists, studio albums and original tracklists. Spotify supplies playback. Your shelf belongs to AudioShelf and starts empty.

Browse artists, explore their studio albums in release order, add records to your shelf, and play their original tracks. Singles, compilations, live albums, remixes and soundtracks are excluded. Reissues within the same MusicBrainz release group appear as one album. Genuine collaborative albums appear under each credited artist.

## Install

1. In Home Assistant, refresh the add-on store for the existing repository: `https://github.com/justcop/home-assistant-addons`.
2. Install **AudioShelf**, version **0.1.0**. Enable the sidebar entry if wanted, then start it.
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
2. Tap **Match Spotify tracks**. The best candidate is saved automatically, and other editions are offered. This may take several seconds. Matching compares artist, normalized track title, recording-version labels, duration and sequence. A remaster suffix can be ignored, but live, demo, acoustic, remix, instrumental and edit labels are preserved.
3. If necessary, paste a Spotify album link in Album settings. If a track still needs review, use its **Edit** button, paste the correct Spotify track link, and confirm the recording. Individual tracks can come from different Spotify albums. Manual corrections survive automatic rematching.
4. Open Spotify on your phone or speaker, play a few seconds so the device is active, then return and tap **Play album**. AudioShelf sends an ordered list of verified track URIs, never an album context. Missing or unverified tracks block playback rather than quietly playing a different album.

Original albums with multiple audio discs keep all those discs. Video discs are excluded. Spotify bonus tracks can occur anywhere in its edition and are skipped by ordered matching. A deluxe release can therefore supply the original songs without its extras becoming part of your shelf.

Shuffle and Repeat are switched off and checked before the playback command. Turn **Autoplay off in Spotify** if you want playback to end in silence. Spotify controls Autoplay, Smart Shuffle and device behaviour; AudioShelf cannot prevent you or another client changing the queue/settings after playback starts. The MVP plays the currently active device and replaces its current playback queue; it does not append an album to an existing queue. Albums over 100 tracks are not supported for playback in this release.

## Persistence and backups

The SQLite database lives at `/share/audioshelf/audioshelf.db` by default. It contains your shelf, MusicBrainz catalogue cache, canonical editions and track mappings. Updates and container rebuilds preserve this folder. Changing `data_directory` selects a different database; copy the existing folder first if moving your collection.

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
docker build --build-arg BUILD_VERSION=0.1.0 --build-arg BUILD_ARCH=amd64 -t audioshelf .
```

The automated suite uses deterministic metadata and player fixtures. Real account authorization, live MusicBrainz catalogue completeness and playback on your own Spotify Connect device must be checked after installation. No real tokens or audio are needed by CI. `/health` checks database connectivity. Settings shows the package version, delivery channel and revision marker; `build.json` records these values. This MVP deliberately has one published main-channel add-on, with no runtime branch-switching code. Development branches can be tested locally before publishing a version bump.

MusicBrainz requests have a shared one-request-per-second limiter, persistent metadata caching, pagination and bounded retries. Cover images come from the Cover Art Archive release-group endpoint and fall back visually when unavailable. MusicBrainz cannot guarantee every artist is perfectly classified or every edition correctly annotated. That is why the original tracklist is visible, editable by release selection and reviewed before its first playback.

## API reference sources

- <https://musicbrainz.org/doc/MusicBrainz_API>
- <https://musicbrainz.org/doc/Cover_Art_Archive/API>
- <https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow>
- <https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide>
- <https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback>
- <https://developers.home-assistant.io/docs/apps/configuration/>
