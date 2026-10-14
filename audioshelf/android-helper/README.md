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
