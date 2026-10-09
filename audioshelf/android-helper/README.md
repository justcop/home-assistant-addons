# AudioShelf Spotify Helper, Android prototype

Small companion app for the installed AudioShelf web app. It uses the original Spotify App Remote service connection to wake Spotify without launching its foreground activity. You wait in the helper for at least five seconds, then it opens the trusted AudioShelf HTTPS page and closes its own task. It never sends play commands, selects a device, changes the album, or receives AudioShelf cookies or Spotify access tokens.

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

When the phone is unavailable, press **Wake Spotify and return** in the waiting dialog. On first use, the helper asks you to confirm the AudioShelf origin. It makes the same SDK connection as the original prototype, keeping the helper visible while Spotify wakes in the background. After success or failure it allows at least five seconds from the start of the attempt before returning. A pending SDK connection gets up to 45 seconds. Spotify may show authorisation on first use; the helper waits until you return from that screen before completing its own return. AudioShelf checks whether the preferred device actually appeared and whether playback started, since an SDK session can fail after waking Spotify. There is no automatic foreground launcher fallback. Use the regular **Open Spotify** link if background waking fails. AudioShelf 0.6.10 sends the exact page URL; older versions return to the server origin.

The launch needs a deliberate tap because browsers restrict automatic app launches after asynchronous network requests. No additional tap is needed to return after a successful connection. Closing the waiting dialog cancels playback; waking Spotify does not override that cancellation.

## Updating the helper

Install helper 0.1.5 once over the existing helper. Afterwards, open **AudioShelf Spotify Helper** from the phone's app drawer. It checks for a published update automatically, and **Check for updates** retries manually. Accept **Download**, then confirm Android's installation screen. On the first update Android may ask you to allow this helper to install apps; enable that setting and return. Installation still needs Android's confirmation. The signing fingerprint stays the same while the repository uses the same private signing key.

Update checks and prompts run only when opening the helper directly, never during the wake-and-return flow. Downloads must match the fixed GitHub helper channel, declared checksum and size, helper package, a newer version, and the installed signing certificate. A failed verification leaves the installed helper unchanged. The downloaded APK is shared only with the Android installer through a private FileProvider.

Merging a helper change into main builds it and publishes the APK plus update metadata to the repository's **audioshelf-helper** GitHub release. Main releases require both signing secrets; a temporary key is never published as a self-update. **Run workflow** on main can publish the current helper too. Pull request APKs are previews and do not replace the published update channel. Before the first merge there may be no published update to check.

The Android icon uses the web app's original record and shelf design, with a small green helper badge to distinguish the two installed apps.

## Local build

Use Java 17 and Android SDK 35. Generate a local test signing key first:

```sh
keytool -genkeypair -keystore prototype.keystore -storepass audioshelf-prototype -keypass audioshelf-prototype -alias prototype -keyalg RSA -keysize 2048 -validity 3650 -dname 'CN=AudioShelf Test, O=justcop, C=GB'
./prepare-sdk.sh
./gradlew :app:testDebugUnitTest :app:assembleDebug :app:lintDebug
```

The SDK AAR is fetched from a pinned Spotify commit and checked against its SHA-256. It remains subject to Spotify's SDK terms and notices: https://github.com/spotify/android-sdk/tree/5aa4d62465f61a0677081ae9a3108177d0365fc3.

## Signing and limitations

No signing key is committed or uploaded as a build artifact. By default, GitHub generates a temporary test key. Each such build has a different fingerprint: add its fingerprint to Spotify and uninstall the previous test APK before installing it. Android uninstalling the helper does not affect your AudioShelf library.

For consistent updates, generate a private keystore once with alias `prototype`, using the same password for the store and key. Add its base64 content to repository secret `AUDIOSHELF_ANDROID_KEYSTORE_BASE64` and its password to `AUDIOSHELF_ANDROID_KEYSTORE_PASSWORD`. The workflow will reuse it. Keep a secure backup of the key. Builds from fork pull requests do not receive these secrets and use a temporary test key instead.

Spotify App Remote is a beta SDK. Real-device verification is required, particularly when Spotify is not running and when AudioShelf is installed as a PWA. Android resolves the return HTTPS link to the installed PWA or browser according to the phone's link settings. The original page is normally reused, but Android/browser task handling can reload it. The helper does not launch Spotify's main activity or switch tasks back from Spotify. It returns to AudioShelf while visible. SDK behaviour and manufacturer service restrictions may still affect whether Spotify becomes a Connect device. Five seconds is a warm-up interval, not a claim that playback succeeded: AudioShelf's existing server handoff remains responsible for readiness and playback. If Spotify is not installed or the request is invalid, the helper shows an error and a return button. It disconnects on exit and does not keep a background service running.

Acceptance checks on the phone:

- Preferred phone available: normal playback, no helper.
- Spotify not running: Spotify wakes while the helper shows the wait, helper returns to AudioShelf, correct album/disc plays once on the phone.
- Another speaker available: preferred phone remains selected, no playback on the speaker.
- Selected speaker unavailable: no phone helper offered.
- SDK connection failed: wait in helper, automatic return and server readiness check; authorisation requested: complete or cancel it, then automatic return; Spotify absent: visible error and manual fallback.
- Cancel waiting request or switch albums: no stale playback from waking Spotify.
