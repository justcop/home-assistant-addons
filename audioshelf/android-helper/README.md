# AudioShelf Android companion

AudioShelf is the source of truth for albums, the selected Spotify Connect device and playback. The Android companion has one purpose: when the selected phone is not available, make Spotify's App Remote service available so AudioShelf can send its canonical album/disc queue.

## What the phone shows

When AudioShelf launches the companion, it displays a quiet AudioShelf logo and a **Connecting to Spotify** status. It does not show a wall of logs or playback controls. It automatically returns to the AudioShelf PWA once Spotify accepts Play or the job completes; otherwise it returns after a bounded 20-second wake window. Merely discovering the phone is not enough: keeping the Spotify App Remote connection alive during the server's playback preparation prevents an early-disconnect race. The server continues its job independently. The companion **never sends Play, changes device, or rewrites the tracklist**.

Launch the companion directly from the Android app drawer to see setup status and a **Connect Spotify** or **Reconnect Spotify** button. Pairing is an ordinary setup action, not a diagnostic.

A discreet upper-right **⋮** menu opens **Diagnostics & maintenance**, with an update check, copyable wake and pairing logs, and an opt-in hold for troubleshooting an active wake. Long-pressing the AudioShelf logo opens the same panel. The diagnostic hold defaults to off on every launch.

## Initial setup and reauthorisation

1. Install the signed Android helper APK. Its stable release channel is [audioshelf-helper](https://github.com/justcop/home-assistant-addons/releases/tag/audioshelf-helper). Allow installation from the browser/file manager when Android asks.
2. In your Spotify Developer Dashboard, enable Android integration for **the same Spotify client ID** used by AudioShelf. Register package `uk.co.justcop.audioshelf.helper`, the APK's **SHA-1 signing fingerprint** from the GitHub Actions build summary, and redirect `audioshelf-helper://spotify-callback` (in addition to the AudioShelf web redirect).
3. Install Spotify and sign in to the Spotify account linked to AudioShelf.
4. In AudioShelf Settings on the Android phone, enable the helper and select this phone as the preferred device. Start Play once to establish the trusted AudioShelf origin and Spotify client ID.
5. Open the helper from the Android launcher, press **Connect Spotify**, and approve Spotify's authorisation request when asked. Once it reports **Spotify connected**, Play from AudioShelf works even when Spotify is closed.

If Spotify authorisation expires, reopen the companion from the app launcher and use **Reconnect Spotify**. On Android 14+, this foreground pairing uses a short-lived `BIND_ALLOW_ACTIVITY_STARTS` bind to Spotify's exported App Remote protocol service. It is released after the SDK callback or timeout. Normal background wake never opens an interactive authorisation view.

The helper checks for new APKs via **⋮ → Diagnostics & maintenance → Check for updates**. The updater verifies size, SHA-256 digest, package name, version and signing certificate, then delegates installation to Android for explicit confirmation. APK self-updates cannot silently change the app's identity or signature.

## Playback and recovery behaviour

The helper uses the pinned Spotify App Remote 0.8.0 SDK. A normal wake calls `SpotifyAppRemote.connect(... showAuthView(false))` from the foreground-visible activity. Its success callback means only that the SDK connected, not that Spotify Connect or Play is ready.

Meanwhile, AudioShelf's server independently polls for the selected phone, queues canonical tracks and verifies playback. If the SDK connection fails or never calls back, the server job and bounded helper return remain active. The companion can show a brief error status; it never launches Spotify automatically or switches to a speaker.

Optional notification-listener playback detection, legacy wake timing experiments, manual foreground recovery shortcuts and the isolated SDK authorisation test screen were removed after the normal pairing path became reliable. Debug traces remain available in the maintenance menu.

## Build and release

This Gradle/Java Android application is built by `.github/workflows/audioshelf-android-helper.yml` on helper changes. The workflow runs JVM tests, Android lint, and the debug APK assembly, then publishes its **stably signed** update package on `main`. The same signing key must be used for subsequent releases.

Key components: `MainActivity` (trusted wake and paired SDK orchestration); `HelperScreen` (branded setup/diagnostics UI); `SpotifyAuthGrant` (temporary Android 14+ foreground authorisation grant); `PlaybackStatusClient` (server job polling); `HelperUpdater` (verified, user-approved updates). No private login credentials are stored in APK source.

## Integrated mode (Android 0.2.0)

The installed app now offers **Use AudioShelf as full Android app** from its companion home screen. This is opt-in. It embeds the existing HTTPS AudioShelf site in a locked-down Android WebView; it does not ship an independent copy of AudioShelf or change the standalone PWA. Tap the native ⋮ icon to return to companion mode, refresh AudioShelf, enable the optional Spotify media-session listener or turn on playback notifications.

The embedded page receives an origin-limited AndroidX WebMessageListener named `AudioShelfAndroid`. Only a trusted HTTPS main frame can request a short-lived, server-created playback job. The Android SDK stays connected until Spotify accepts Play or a bounded timeout expires; the server alone selects the device, sends the exact tracklist and verifies playback. An incoming external link cannot load into the trusted frame. Spotify authorisation remains explicitly user-initiated from companion mode.

With notification access explicitly granted in Android settings, the listener reads only Spotify's media session. It signals the web UI to refresh its authoritative Now Playing data. If enabled separately, a low-priority playback notification opens the same AudioShelf app; no competing native Spotify transport controls are installed. Long-press the launcher icon for My shelf or Now Playing shortcuts.

To browse offline, use **Settings → Offline shelf → Save my shelf offline** while connected. This reuses the existing artwork cache and stores a private, read-only, per-account snapshot of your shelf, artists and album tracklists in WebView or PWA storage. It does not cache Spotify API responses, tracks, authentication secrets or record-store searches. Changes/playback require a server connection. Sign out or use Clear offline shelf to erase the local snapshot. Android WebView and Chrome PWA have separate storage and require separate logins/snapshot downloads.

In integrated mode 0.2.2, the Android ⋮ menu also provides Check for Android updates and Pair or reconnect Spotify. The embedded shelf runs in its own Android Recents task, independently of the transient helper activity.

Integrated mode 0.2.3 also respects Android's status-bar, camera-cutout, gesture-navigation and keyboard insets, keeping web content below the system icons and above the gesture area.

Android 0.2.4 restores edge-to-edge drawing behind the status bar. The wrapper uses the actual Android top inset as the masthead padding instead of stacking two rows of top spacing. Branding, Settings and the native menu stay below the status icons. The standalone PWA does not change, and no Home Assistant add-on update is required.

In integrated Android 0.2.5, **⋮ → Check for Android updates** opens a single update dialog. When a newer signed APK is available, its **Download** button becomes active; after tapping it, the dialog closes and Android handles the verified installation. This fixes the previous focus-dependent prompt that could show an update without offering installation. The original companion-mode updater remains unchanged.

Android 0.2.6 corrects the remaining top-logo overlap: the WebView now has Android-enforced status-bar clearance even if the page has not finished loading. The separate AudioShelf masthead uses compact 8px/12px padding, so this protection does not add an entire second blank row. No Home Assistant update is required.

Android 0.2.7 uses a sticky masthead in integrated mode. The Android WebView initially loads beneath the system icons, then expands behind the status bar only after a padded, opaque, sticky header is installed. This keeps the complete logo and Settings visible even after a small scroll without sacrificing the screen's top area. The web PWA retains its existing independent header.

Android 0.2.8 keeps the sticky edge-to-edge interface from 0.2.7 unchanged and lowers the logo/Settings row by 36 CSS pixels, correcting the Pixel status-icon overlap. The separate native ⋮ menu, remainder of the page and standalone PWA are unchanged.
