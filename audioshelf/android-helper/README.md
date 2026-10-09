# AudioShelf Spotify Helper, Android prototype

Small companion app for the installed AudioShelf web app. It connects to the local Spotify app through Spotify App Remote, then closes its own task to reveal the existing AudioShelf browser/PWA task. It never sends play commands, selects a device, changes the album, or receives AudioShelf cookies or Spotify access tokens.

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

When the phone is unavailable, press **Wake Spotify and return** in the waiting dialog. On first use, the helper asks you to confirm the AudioShelf origin. Spotify may also ask for authorisation. After the connection attempt finishes, the helper automatically closes. AudioShelf checks whether the preferred device actually appeared and whether playback started. A failed App Remote session can still wake Spotify successfully. The regular **Open Spotify** link remains available if the helper fails or is absent.

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

Spotify App Remote is a beta SDK. Real-device verification is required, particularly when Spotify is not running and when AudioShelf is installed as a PWA. Closing the helper reveals the existing task; it does not reopen a URL in a potentially different browser or try to force an activity launch from the background. There is no delayed blind switch to Spotify and back. If the SDK connection fails or times out, the helper automatically returns to AudioShelf, whose existing waiting dialog checks the selected device and shows any actual playback failure. If Spotify is not installed or the launch request is invalid, the helper shows an error and a return button. It disconnects on exit and does not keep a background service running.

Acceptance checks on the phone:

- Preferred phone available: normal playback, no helper.
- Spotify not running: helper connects, returns to the existing AudioShelf page, correct album/disc plays once on the phone.
- Another speaker available: preferred phone remains selected, no playback on the speaker.
- Selected speaker unavailable: no phone helper offered.
- Authorisation refused or Spotify absent: visible error and manual fallback.
- Cancel waiting request or switch albums: no stale playback from waking Spotify.
