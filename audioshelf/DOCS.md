# AudioShelf setup

The shelf and Record Store work immediately, without Spotify credentials.

For playback, set your Spotify **client ID** and **exact HTTPS redirect URI** in add-on configuration, then restart. Register that same redirect in your Spotify developer dashboard. The callback ends in `/auth/spotify/callback`. No client secret is needed because AudioShelf uses PKCE.

Open Settings in the AudioShelf UI to connect Spotify. Review the original MusicBrainz tracklist, match the Spotify tracks, and open Spotify on your chosen device before pressing Play Album.

Your collection is stored under `/share/audioshelf`. Spotify tokens are stored separately in persistent add-on `/data`. The standalone UI is on port 8098 by default; ingress is also supported. Your existing proxy supplies external HTTPS and access control. You can additionally set a `web_password` for standalone access.

See [the full README](https://github.com/justcop/home-assistant-addons/blob/main/audioshelf/README.md) for edition corrections, backups, phone installation, testing and troubleshooting.
