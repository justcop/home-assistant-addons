# AudioShelf setup

The shelf and Record Store work immediately, without Spotify credentials.

For playback, set your Spotify **client ID** and **exact HTTPS redirect URI** in add-on configuration, then restart. Register that same redirect in your Spotify developer dashboard. The callback ends in `/auth/spotify/callback`. No client secret is needed because AudioShelf uses PKCE.

Open Settings in the AudioShelf UI to connect Spotify. Review the original MusicBrainz tracklist, match the Spotify tracks, and open Spotify on your chosen device before pressing Play Album.

Your collection is stored under `/share/audioshelf`. Spotify tokens are stored separately in persistent add-on `/data`. The standalone UI is on port 8098 by default; ingress is also supported. Your existing proxy supplies external HTTPS and access control. Set a long unique `web_password` for standalone access, which is locked without one. Use HTTPS. Optional authenticator setup, trusted browsers and temporary support access are under Settings → Security; see README for setup and recovery.

See [the full README](https://github.com/justcop/home-assistant-addons/blob/main/audioshelf/README.md) for edition corrections, backups, phone installation, testing and troubleshooting.

Artwork and reproducible API data are stored separately in `/share/audioshelf-cache`, configurable with `cache_directory`. You manage backup exclusions. Your uploaded covers remain in `/share/audioshelf/custom-artwork`. Automatic playback matching prefers newer labelled remasters or dated studio mixes; tap Find another edition to update existing automatic mappings.

MusicBrainz country preferences default to GB, US, worldwide and Europe, with other countries available as fallbacks. Vinyl, CD and digital are enabled in that order; cassette is excluded. Change priority and optional strict country restrictions under **Settings → MusicBrainz releases**. Artist Record Store pages offer **Manage catalogue → Find catalogues** to discover and choose a curated MusicBrainz series for any artist, with individual inclusion/exclusion overrides. Successful series snapshots are retained for temporary outages. All artists use the same rules; no artist or album has a built-in exception.

Choose from ten themes under **Settings → Appearance**. In **Album settings**, choose artwork from another eligible edition or download a diagnostic report after reproducing an issue. Existing tracklists and manual mappings survive settings changes; changing the original tracklist explicitly clears its mappings after confirmation.

Lost authenticator: in Home Assistant AudioShelf Configuration, set **2FA recovery request (advanced)** to a new value (for example `reset-1`), save and restart. Then log in with your normal password. The request is processed once; use a different value for a future reset. Re-enrol 2FA in AudioShelf Security settings.


## Links between Listening Analytics and AudioShelf

Update both add-ons. Set `audioshelf_url` in Listening Analytics to the HTTPS AudioShelf address, for example `https://audioshelf.justcop.co.uk`. In AudioShelf, set `listening_analytics_origin` to the origin of the Listening Analytics address (scheme and hostname, with port if needed, without a path). Sign in to AudioShelf in the same browser using the account whose shelf you want to browse.

“On your shelf” appears only after an authenticated check finds matching owned records. Artist links open the full shelf and scroll to that artist. Album links open the saved album page. Song links use saved tracklists, preferring the album from listening history. Multiple matching albums show separate links. “Find in record store” remains available regardless of ownership and opens a search. No credentials or shelf data are copied to Listening Analytics.

The browser must permit the AudioShelf session cookie for the check. Using HTTPS subdomains of the same domain works with the existing cookie settings. Across unrelated domains, browser cookie restrictions may prevent shelf checks; record store links still work. In Home Assistant ingress, use the origin of the Home Assistant page as `listening_analytics_origin`.
