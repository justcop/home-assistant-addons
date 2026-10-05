# Changelog

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
