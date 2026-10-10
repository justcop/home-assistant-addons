# Changelog

## 0.2.19

- When choosing estimated album listens, immediately show that the view is updating instead of appearing unresponsive.
- Display live album-tracklist processing progress, including completed, queued, unprocessed, unresolved and short albums. Refresh the progress automatically while metadata is being imported.
- Differentiate genuinely short albums (fewer than six tracks) from albums whose metadata could not be identified, without inventing an estimated listen count.
- Add API and browser regression checks for estimated-listens controls and processing status.


## 0.2.18

- Add estimated album listens using the play count of the third least played track from an identified complete tracklist (at least six songs). Unplayed tracks count as zero; albums with unknown tracklists do not receive guessed estimates.
- Find canonical audio tracklists through paced MusicBrainz metadata lookups, with a Last.fm fallback, and persist the metadata locally. Identify existing albums in the background without reimporting scrobbles.
- Add estimated album listens to global and artist-level album analysis, sortable album rankings, and album details with a track-by-track explanation. Retain total track scrobbles alongside the new measure.
- Respect time periods, vinyl-only listening and existing grouping rules. Update cached analytical results as metadata becomes available.

## 0.2.17

- Automatically combine artist names that differ only by a leading “The”, including renamed artists whose old and new recordings do not overlap. Conflicting artist MusicBrainz IDs prevent this merge; accents and punctuation still require matching recording evidence.
- Reconcile existing scrobbles when upgrading and new evidence as it arrives, retaining originals, song and album groups, the normal merge history and the ability to undo automatic merges without immediately reapplying them.
- Keep uncertain name similarities in artist merge suggestions rather than combining unrelated artists.

## 0.2.12

- Accept TheAudioDB's R2 image CDN in metadata validation and browser image policy. This fixes photos and logos that were previously rejected as unavailable.
- Add TheAudioDB album covers as another fallback, match recognised remaster/deluxe album suffixes conservatively, and retain artist identity checks.
- Display photos and covers as soon as they arrive while other lookups continue. Preserve usable images if another image fails.
- Retry previously cached misses after upgrading, preserve successful URL metadata, allow slower provider responses and pace each provider request. Cache provider outages briefly rather than treating them as day-long missing artwork.

## 0.2.11

- Link Listening Analytics to owned albums and artists in AudioShelf, with separate record store search links.

## 0.2.10

- Artist detail pages show artist photos and transparent logos from TheAudioDB when available. Background lookups cache remote URLs and retain album cover fallback.

## 0.2.9

- Add a Spotify action to artist, album and song detail pages. It opens a Spotify search already scoped to the selected artist, album or track and does not require Spotify credentials.
- Fall back to Deezer's public album metadata when Last.fm has no usable cover image. Keep remote URL-only caching, trusted image hosts and the existing paced background lookup.

## 0.2.8

- Prevent an in-flight background read from restoring the browser session after sign-out. Direct browser sessions now expire seven days after sign-in.

- Find covers in older scrobbles and more of an artist’s albums. When imported images are missing, retrieve small Last.fm album thumbnail URLs in a paced background worker. Store URL metadata only and show a loading or unavailable status.
- Make statistic totals clickable in Overview, Trends and detail pages. Open artist, album, song or scrobble lists with the current item, dates, source and version display retained. Show the item filter in rankings and preserve Back navigation to the detail view.

## 0.2.7

- Save Overview/Trends, detail analyses and artist/album/song rankings across restarts. Show saved results immediately while a single background worker refreshes changed data.
- Refresh saved views nightly at 03:00 in the configured timezone, even when no browser is open. Keep up to 24 recent views per account, including separate date/source/version filters.
- Show when saved results were updated and replace changed results automatically, preserving timeline zoom and scroll. Failed refreshes retain saved results.

## 0.2.6

- Add a Listening Analytics record-and-chart logo to the Home Assistant add-on, web header, login, browser favicon and installed app icons.

## 0.2.5

- Add remote Last.fm album thumbnails to artist, album and song details without storing image files or fetching metadata during page load. Artist/song artwork is labelled with its representative album. Missing or failed covers leave the statistics usable.

## 0.2.4


- Move combined/original version display into Settings and remember the preference.
- Offer selected version names as merge-name choices, preferring names without brackets then the highest scrobble count, with free text editing retained.
- Default to all-time statistics and add Reset to clear date, source, search and item filters.
- Replace the prominent source dropdown with a small highlighted Vinyl only record toggle, and enlarge the statistics icons.
- Widen the diary song column and retain table scrolling on phones.
- Add menu and detail navigation history so browser/Android Back and Forward restore the previous view and filters.

## 0.2.3

- Choose individual versions from merge suggestions, including a subset of already grouped versions, and edit their combined display name before merging. Undo restores previous names and grouping.
- Move sign-out and theme controls into Settings. Put a compact Sync button beside Settings.
- Replace the graph zoom slider with pinch zoom and accessible zoom/reset buttons. Keep one-finger scrolling within the graph.
- Tighten the year/month heatmap gutter and spacing to fit small phone screens.

## 0.2.2

- Identify invalid startup settings by name without printing credentials, including web password length errors.
- Treat null optional credentials and passwords as blank, keeping direct browser access disabled.
- Report storage access failures separately from configuration errors.

## 0.2.1

- Add contained horizontal timeline scrolling and zoom, calendar-year selection, and year/month heatmaps in the library and entity details.
- Add five persistent colour palettes, full-screen phone detail sheets and background scroll locking.
- Replace the full unmerged-song list with suggestions, explained skipped candidates and existing merges, with remembered dismissals and optional artist-specific suffix learning.
- Add loading feedback and revision-aware analysis caching for faster repeated views.
- Add an installable network-only web app with an offline connection notice; private data is never cached in the service worker.

- Improve phone layouts with persistent bottom navigation, larger touch targets, safe-area spacing and readable date controls.
- Add web app manifest and home-screen icons, retaining Home Assistant authentication.
- Add a configurable web password, secure login cookies, CSRF checks, login rate limiting and sign out for an optional direct HTTPS web app.
- Keep Home Assistant Ingress access and disable direct browser access unless a password is configured.

## 0.2.0

- Store authenticated vinyl source reports in the analyser's account database, independently of history sync and reconciliation.
- Add All listening, Vinyl only and Unknown source filters throughout analytics and detail views.
- Add optional local port and shared connection token for Vinyl Guardian. Show connection status and received-report count.

## 0.1.0

- Add a responsive light and dark Last.fm analytics dashboard with date filters, rankings, discovery, rediscovery, artist concentration and listening heatmaps.
- Add resumable history import and periodic sync with transactional storage, duplicate preservation and checked recent-history reconciliation.
- Add reversible song and album grouping, raw-versus-combined totals and version drill-down.
- Add Home Assistant Ingress packaging for amd64 and aarch64, persistent account databases, safe setup, health monitoring and a fictional demo.
