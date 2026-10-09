# AudioShelf Spotify Helper, Android prototype

Small companion app for the installed AudioShelf web app. It explicitly opens the local Spotify app, keeps the helper in front during the five-second Connect device warm-up, then opens the trusted AudioShelf HTTPS page and closes its own task. An optional Spotify App Remote connection can also wake the service but is not required for the launch or return. It never sends play commands, selects a device, changes the album, or receives AudioShelf cookies or Spotify access tokens.

AudioShelf's existing server handoff continues waiting for the preferred device and sends the canonical album/disc tracklist exactly as before. An available preferred device needs no helper. The helper button appears only in the unavailable-device waiting dialog, on Android HTTPS, when the browser-specific helper setting is enabled and the preferred device type is Smartphone. Speakers and computers retain the manual Open Spotify route.

## Build and install

Run **AudioShelf Android helper APK** from GitHub Actions after merging, or download its APK artifact from the pull request build. Unzip it and install `app-debug.apk`, allowing installation from your browser/file manager when Android asks. Builds run on demand and for changes to this helper in a pull request, not for every backend commit. Temporary APK artifacts expire after seven days.

Before using it, edit the same Spotify developer application whose client ID is configured in AudioShelf:

1. Enable Android integration.
2. Add package name `uk.co.justcop.audioshelf.helper`.
3. Add the SHA-1 signing fingerprint shown in the GitHub build summary and included in `signing-fingerprint.txt` alongside the APK.
4. Add redirect URI `audioshelf-helper://spotify-callback`. Keep the existing web callback too.
5. Install Spotify and log in with the Spotify account connected to AudioShelf.
6. In AudioShelf Settings on that phone, enable **Use the installed Android Spotify helper on this phone** and select the phone as the preferred playback device.

When the phone is unavailable, press **Wake Spotify and return** in the waiting dialog. On first use, the helper asks you to confirm the AudioShelf origin. The helper requests a background Spotify launch for compatible Spotify activities and restores its own existing task promptly. You wait in the helper, which returns automatically after five seconds, even if its optional SDK connection fails. A brief Spotify flash is possible when a foreground launch is needed. If Android restricts task movement, Spotify can remain visible during the wait, but the automatic AudioShelf return remains scheduled. AudioShelf checks whether the preferred device actually appeared and whether playback started. The regular **Open Spotify** link remains available if the helper fails or is absent. AudioShelf 0.6.10 sends the exact page URL; older versions return to the server origin.

The launch needs a deliberate tap because browsers restrict automatic app launches after asynchronous network requests. No additional tap is needed to return after a successful connection. Closing the waiting dialog cancels playback; waking Spotify does not override that cancellation.

## Local build

Use Java 17 and Android SDK 35. Generate a local test signing key first:

```sh
keytool -genkeypair -keystore prototype.keystore -storepass audioshelf-prototype -keypass audioshelf-prototype -alias prototype -keyalg RSA -keysize 2048 -validity 3650 -dname 'CN=AudioShelf Test, O=justcop, C=GB'
./prepare-sdk.sh
./gradlew :app:assembleDebug :app:lintDebug
```

The SDK AAR is fetched from a pinned Spotify commit and checked against its SHA-256. It remains subject to Spotify's SDK terms and notices: https://github.com/spotify/android-sdk/tree/5aa4d62465f61a0677081ae9a3108177d0365fc3.

## Signing and limitations

No signing key is committed or uploaded as a build artifact. By default, GitHub generates a temporary test key. Each such build has a different fingerprint: add its fingerprint to Spotify and uninstall the previous test APK before installing it. Android uninstalling the helper does not affect your AudioShelf library.

For consistent updates, generate a private keystore once with alias `prototype`, using the same password for the store and key. Add its base64 content to repository secret `AUDIOSHELF_ANDROID_KEYSTORE_BASE64` and its password to `AUDIOSHELF_ANDROID_KEYSTORE_PASSWORD`. The workflow will reuse it. Keep a secure backup of the key. Builds from fork pull requests do not receive these secrets and use a temporary test key instead.

Spotify App Remote is a beta SDK. Real-device verification is required, particularly when Spotify is not running and when AudioShelf is installed as a PWA. Android resolves the return HTTPS link to the installed PWA or browser according to the phone's link settings. The original page is normally reused, but Android/browser task handling can reload it. Restoring helper focus uses only the helper's own Android AppTask. Automatic return uses Android's recent-foreground activity launch allowance; manufacturer restrictions may affect this. Five seconds is a warm-up interval, not a claim that playback succeeded: AudioShelf's existing server handoff remains responsible for readiness and playback. If Spotify is not installed or the request is invalid, the helper shows an error and a return button. It disconnects on exit and does not keep a background service running.

Acceptance checks on the phone:

- Preferred phone available: normal playback, no helper.
- Spotify not running: Spotify wakes while the helper shows the wait, helper returns to AudioShelf, correct album/disc plays once on the phone.
- Another speaker available: preferred phone remains selected, no playback on the speaker.
- Selected speaker unavailable: no phone helper offered.
- SDK authorisation unavailable: Spotify still opens and returns; Spotify absent: visible error and manual fallback.
- Cancel waiting request or switch albums: no stale playback from waking Spotify.
