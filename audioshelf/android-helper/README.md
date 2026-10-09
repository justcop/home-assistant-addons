# AudioShelf Spotify Helper, Android prototype

Small companion app for the installed AudioShelf web app. It uses the Spotify App Remote service connection to wake Spotify without showing its foreground activity, while independently watching the chosen AudioShelf playback job over HTTPS. It returns when the authenticated AudioShelf server first finds the selected Spotify Connect phone ready, rather than waiting to send Play or confirm playback. Play acceptance, verified playback, optional local detection and the independent 20-second maximum remain fallbacks. Missing SDK callbacks and failed network requests cannot leave it waiting indefinitely. A timeout is never reported as playback success. It never sends play commands, selects a device, changes the album, or receives AudioShelf cookies or Spotify access tokens.

AudioShelf's existing server handoff continues waiting for the preferred device and sends the canonical album/disc tracklist exactly as before. An available preferred device needs no helper. AudioShelf automatically launches the helper from Play when the preferred phone is unavailable, on Android HTTPS, when the browser-specific helper setting is enabled and the preferred device type is Smartphone. Speakers and computers retain the manual Open Spotify route.

## Build and install

Run **AudioShelf Android helper APK** from GitHub Actions after merging, or download its APK artifact from the pull request build. Unzip it and install `app-debug.apk`, allowing installation from your browser/file manager when Android asks. Builds run on demand and for changes to this helper in a pull request, not for every backend commit. Temporary APK artifacts expire after seven days.

Before using it, edit the same Spotify developer application whose client ID is configured in AudioShelf:

1. Enable Android integration.
2. Add package name `uk.co.justcop.audioshelf.helper`.
3. Add the SHA-1 signing fingerprint shown in the GitHub build summary and included in `signing-fingerprint.txt` alongside the APK.
4. Add redirect URI `audioshelf-helper://spotify-callback`. Keep the existing web callback too.
5. Install Spotify and log in with the Spotify account connected to AudioShelf.
6. In AudioShelf Settings on that phone, enable **Use the installed Android Spotify helper on this phone** and select the phone as the preferred playback device.

With the helper setting enabled, press Play as usual. When the phone is unavailable, AudioShelf automatically launches the helper. On first use, confirm the AudioShelf origin. The original SDK service connection wakes Spotify in the background. An SDK error does not mean Spotify failed to wake, so it never triggers a Spotify launcher intent. Any saved foreground-recovery setting from 0.1.10 is ignored. Spotify itself may request first-use authorisation; automatic return waits until the helper is visible again.

## AudioShelf playback feedback

Helper 0.1.14 with AudioShelf 0.6.15 requests immediate authenticated job snapshots, repeating every 750 ms while searching for the phone and every 350 ms while preparing playback. Notification access is not required for this path. Server feedback distinguishes asking Spotify for devices, waiting for the preferred phone, preparing playback, sending Play, and confirming the correct phone and track. Unconfirmed player states distinguish wrong device, wrong track, paused and no player state yet. Once the server reports `preparing_playback` or `sending_play` for the uniquely selected, unrestricted phone, the helper returns immediately. The server continues preparing the canonical queue, sending Play and verifying the exact phone and first track. Device discovery is not playback success and any later failure is still reported in AudioShelf.

The helper logs server-relative times for completed device checks, the duration of each Spotify device request, first device discovery and Play acceptance, along with each HTTPS request and HTTP response, then **Connected to AudioShelf. Playback-job access accepted.** on the first valid response. DNS, TLS, HTTP errors and timeouts are distinguished without logging URLs or credentials. If the 20-second fallback expires, the log includes the last server progress or last network step. Immediate snapshots remain readable while a Spotify Play request is in progress. Older long-poll callers remain compatible, but helper 0.1.14 requires AudioShelf 0.6.15 or later for device-ready return.

## Optional local playback detection

Open the helper directly, tap **Enable Spotify playback detection**, then allow **AudioShelf Spotify playback detection** in Android's notification-access settings. Return to AudioShelf and press Play. This is a one-time grant required by Android to inspect Spotify's media session. The service reads no notification contents. The helper samples only Spotify's media session while a wake attempt is active; it requires a local route, a playing state and active music output. It ignores playback already running at the start unless the track changes. Track identity is compared only in memory, never saved or transmitted.

A fresh local playing transition returns to AudioShelf immediately and records **Android confirms Spotify started playing locally**. This confirms local Spotify playback, not that the exact requested album/disc was accepted; AudioShelf still owns and verifies its canonical queue. The primary server signal reports discovery of the selected Spotify Connect phone using a short-lived, read-only playback-job token; the same server independently sends Play and verifies the exact phone and first track. Device readiness, accepted Play, verified playback or optional local playback detection may trigger early return. Notification access is unnecessary: authenticated server status and the 20-second fallback work independently. If neither signal arrives, the helper returns without claiming success, and AudioShelf continues its one-minute playback job. The helper never opens Spotify in the foreground as recovery; the existing manual Open Spotify link remains in AudioShelf.

AudioShelf attempts one automatic helper launch per Play request. Some browsers can block an external app launch after asynchronous network requests; **Wake Spotify and return** stays available as a manual fallback without reloading or cancelling the pending playback. No additional tap is needed when playback has been confirmed. Closing the waiting dialog cancels playback; waking Spotify does not override that cancellation.

## Wake diagnostics

The helper displays elapsed timestamps for request validation, background connection, SDK success/failure, read-only player status, AudioShelf job state, return and disconnection. Error logs contain exception types and cause types, never raw SDK error payloads, client IDs, access tokens, URLs or track names. The last wake log is saved locally and appears when opening the helper from the app drawer. Tap **Copy log** to share it when troubleshooting.

For a failed attempt, open the helper directly and enable **Keep open for diagnostics (return manually)** before pressing Play in AudioShelf. The helper then stays visible after the wake attempt, and any Spotify player status changes can be observed while AudioShelf's server continues its one-minute playback handoff. Copy the log and tap **Return to AudioShelf**. Turn the option off to restore automatic return. The helper reads server-confirmed device discovery, Play acceptance, ongoing verification and final job state and, with notification access, local Spotify playback state. AudioShelf still reports exact-track confirmation, playback failure or expiry.

## Updating the helper

Ensure AudioShelf is at v0.6.15 or later, then install helper 0.1.14 over the existing helper. Both versions are required for this handshake. Afterwards, open **AudioShelf Spotify Helper** from the phone's app drawer. It checks for a published update automatically, and **Check for updates** retries manually. Accept **Download**, then confirm Android's installation screen. On the first update Android may ask you to allow this helper to install apps; enable that setting and return. Installation still needs Android's confirmation. The signing fingerprint stays the same while the repository uses the same private signing key.

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

Spotify App Remote is a beta SDK. Real-device verification is required, particularly when Spotify is not running and when AudioShelf is installed as a PWA. Android resolves the return HTTPS link to the installed PWA or browser according to the phone's link settings. The original page is normally reused, but Android/browser task handling can reload it. The helper does not launch Spotify's main activity or switch tasks back from Spotify. It returns to AudioShelf while visible. SDK behaviour and manufacturer service restrictions may still affect whether Spotify becomes a Connect device. Local Spotify playback detection or the server's verified playback result returns early; an 20-second deadline returns without claiming success if neither signal is available. The playback job has a one-minute failure deadline to prevent indefinite waiting. If Spotify is not installed or the request is invalid, the helper shows an error and a return button. Media-session sampling and SDK connections stop on exit. Android may keep the notification-access permission bridge bound, but it does not inspect notification contents or run playback monitoring outside a wake attempt.

Acceptance checks on the phone:

- Preferred phone available: normal playback, no helper.
- Spotify not running: Spotify wakes while the helper shows the wait, helper returns to AudioShelf, correct album/disc plays once on the phone.
- Another speaker available: preferred phone remains selected, no playback on the speaker.
- Selected speaker unavailable: no phone helper offered.
- SDK callback never arrives: return as soon as server confirms playback; SDK connection fails: return on real server result; authorisation requested: complete or cancel it, then return on job result; Spotify absent: visible error and manual fallback.
- Cancel waiting request or switch albums: no stale playback from waking Spotify.
