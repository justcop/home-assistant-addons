# AudioShelf Android companion

AudioShelf is the source of truth for albums, the selected Spotify Connect device and playback. The Android companion has one purpose: when the selected phone is not available, make Spotify's App Remote service available so AudioShelf can send its canonical album/disc queue.

## What the phone shows

When AudioShelf launches the companion, it displays a quiet AudioShelf logo and a **Connecting to Spotify** status. It does not show a wall of logs or playback controls. It automatically returns to the AudioShelf PWA when the selected phone is ready, Spotify has accepted Play, or the job has completed; otherwise it returns after a bounded 20-second wake window. The server continues its job independently. The companion **never sends Play, changes device, or rewrites the tracklist**.

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
