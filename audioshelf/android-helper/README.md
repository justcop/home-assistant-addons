# AudioShelf Spotify Helper, Android prototype

Small companion app for the installed AudioShelf web app. It uses the Spotify App Remote service connection to wake Spotify without showing its foreground activity, while independently watching the chosen AudioShelf playback job over HTTPS. It returns when the authenticated AudioShelf server first finds the selected Spotify Connect phone ready, rather than waiting to send Play or confirm playback. Play acceptance, verified playback, optional local detection and the independent 20-second maximum remain fallbacks. Missing SDK callbacks and failed network requests cannot leave it waiting indefinitely. A timeout is never reported as playback success. It never sends play commands, selects a device, changes the album, or receives AudioShelf cookies or Spotify access tokens.

AudioShelf's existing server handoff continues waiting for the preferred device and sends the canonical album/disc tracklist exactly as before. An available preferred device needs no helper. AudioShelf automatically launches the helper from Play when the preferred phone is unavailable, on Android HTTPS, when the browser-specific helper setting is enabled and the preferred device type is Smartphone. Speakers and computers retain the manual Open Spotify route.

## Build and install

Run **AudioShelf Android helper APK** from GitHub Actions after merging, or download its APK artifact from the pull request build. On helper 0.1.15, if App Remote fails to authorise and Spotify never appears as a Connect device, the helper reveals **Open Spotify to restore connection**. This deliberately opens Spotify only when you tap it, never automatically. Approve any Spotify request, press Android Back to return to the helper, and the helper will retry the SDK connection while AudioShelf keeps the original phone-pinned playback job. If Spotify has already appeared, the helper closes normally before Play as before. Spotify App Remote authorisation is separate from the AudioShelf Web API token or rate limits. Unzip it and install `app-debug.apk`, allowing installation from your browser/file manager when Android asks. Builds run on demand and for changes to this helper in a pull request, not for every backend commit. Temporary APK artifacts expire after seven days.

Before using it, edit the same Spotify developer application whose client ID is configured in AudioShelf:

1. Enable Android integration.
2. Add package name `uk.co.justcop.audioshelf.helper`.
3. Add the SHA-1 signing fingerprint shown in the GitHub build summary and included in `signing-fingerprint.txt` alongside the APK.
4. Add redirect URI `audioshelf-helper://spotify-callback`. Keep the existing web callback too.
5. Install Spotify and log in with the Spotify account connected to AudioShelf.
6. In AudioShelf Settings on that phone, enable **Use the installed Android Spotify helper on this phone** and select the phone as the preferred playback device.

## Pair Spotify App Remote separately (0.1.19)

Spotify's Android App Remote authorisation is separate from the Web API connection used by AudioShelf. On recent Android versions the Spotify background service can be blocked from opening its own authorisation screen during an automatic wake. Complete authorisation deliberately while setting up the helper:

1. Launch one Play attempt from AudioShelf to establish the trusted server and Spotify client ID, even if the attempt cannot wake Spotify.
2. Open **AudioShelf Spotify Helper** directly from the Android app drawer and press **Authorise Spotify App Remote**. Accept any Spotify approval prompt. This pairing action uses the configured client ID and the registered `audioshelf-helper://spotify-callback` redirect; it never issues Play.
3. The helper must report **Spotify App Remote authorised and connected successfully** before treating pairing as successful. If it reports `UserNotAuthorizedException` or stalls, check that package `uk.co.justcop.audioshelf.helper`, the APK's SHA-1 fingerprint and the redirect URI are registered under the **same** client ID in the Spotify Developer Dashboard. If Android did not show an approval prompt, open Spotify itself, return to the helper and retry pairing.
4. When pairing succeeds, use Play from AudioShelf as usual with Spotify closed. The normal helper wake now uses a **noninteractive** Spotify SDK connection (`showAuthView(false)`); it does not attempt to display the authorisation UI. The existing one-tap foreground fallback remains available if Android still will not start Spotify's background service.

Helper 0.1.20 immediately displays **Pairing started** and a separate timestamped **pairing log** underneath the button. The log records whether Spotify actually called back. At ten seconds of silence it reports a missing SDK callback; at thirty seconds it marks the attempt timed out without claiming authorisation. Use **Copy pairing log** to share this evidence. The launcher separately retains the last wake log, with **Copy wake log** and **Clear previous wake log** available, so an old wake does not get confused with a new pairing. New wake requests still start an independent clean log.

Spotify authorisation may later be revoked or need renewal; in that case pair again from the helper's launcher. Do not disconnect AudioShelf's Web API account, reinstall Spotify, or grant notification access merely to complete pairing. **Successful pairing is not proof that every cold background service launch will work:** real phone verification is needed.

With the helper setting enabled, press Play as usual. When the phone is unavailable, AudioShelf automatically launches the helper. On first use, confirm the AudioShelf origin. The routine SDK connection attempts to wake Spotify silently, without asking its background service to start an Android authorisation screen. An SDK error does not mean Spotify failed to wake, so it never automatically triggers a Spotify launcher intent. Any saved foreground-recovery setting from 0.1.10 is ignored.

## Test Android 14+ authorisation-screen launch grant (0.1.23)

Your Android API 37 phone confirmed a direct connection to Spotify's exported `AppProtocolRemoteService` in approximately 120 ms, but the normal SDK `connect()` still timed out without either callback. Android 14 and later restrict a bound background service from launching an Activity unless its foreground client opts into `Context.BIND_ALLOW_ACTIVITY_STARTS`. A [Spotify SDK issue](https://github.com/spotify/android-sdk/issues/377) reports a similar silent authorisation hang involving Android blocking Spotify's authorisation activity.

Open **Isolated Spotify SDK diagnostics**, then select **Test SDK with Android 14+ authorisation-screen grant**. This explicitly binds only the identified Spotify App Remote protocol service with `BIND_AUTO_CREATE | BIND_ALLOW_ACTIVITY_STARTS`, waits for `onServiceConnected`, and invokes the **same** SDK `connect()` and `showAuthView(true)` already used by the isolated baseline test. It keeps the temporary permission-grant bind alive while that test is active, and releases it on the callback, 30-second timeout or Activity destruction. If the binding itself does not connect within five seconds, it stops without starting the SDK test. It never calls the raw binder, sends playback commands or changes the selected Spotify Connect device.

This is a **diagnostic experiment**, not proof that the SDK needs this flag: a second bind to the same Spotify service may or may not affect how Spotify launches its internal authorisation UI. The initial screen remains unaffected, and the normal helper wake path is unchanged. An authorisation prompt or successful SDK callback after the grant would strongly support the Android background-activity restriction hypothesis. Compare the baseline and grant-enabled logs; share only the copyable app log, never unfiltered SDK logcat.

## Spotify SDK service and lifecycle diagnostics (0.1.21)

If pairing produces neither success nor failure callback, open the helper from your Android app drawer and tap **Isolated Spotify SDK diagnostics**. This opens an independent Activity using Spotify's documented App Remote sample connection pattern with the *same* application ID, signing key, saved client ID and registered Android redirect as the helper. It is a sample-style reproduction, not an installation of Spotify's unmodified sample APK. It sends no playback commands, selects no device and does not contact AudioShelf's playback job.

1. Tap **Inspect Spotify services and test binding**. Android reports whether the Spotify package is visible, its version and candidate exported remote services. Only when exactly one plausible unprotected, exported Spotify App Remote protocol service is discovered will the helper attempt a four-second direct bind. `bindService(true)` means only that Android accepted the request; `onServiceConnected` confirms a binder was reached. No callback, no exported candidate, or a rejected bind is diagnostic evidence, but *not* definitive proof about the SDK's private service selection. The helper never communicates with the raw binder.
2. After binding finishes, tap **Run isolated SDK sample-style connection** and leave the diagnostic screen open for at least 30 seconds so the result or explicit local timeout appears in the copied log. Spotify's SDK debug mode is enabled, `showAuthView(true)` is used, and the app logs explicit SDK callbacks and Android lifecycle/window focus transitions. The 10- and 30-second warnings are local watchdogs, **not** Spotify responses. Observe whether an authorisation screen appears.
3. Tap **Copy SDK diagnostic log** and share the sanitised log from this screen. The detailed Spotify SDK logcat output is separate and may contain sensitive material, so do not share unfiltered `adb logcat` captures.

Helper 0.1.22 narrows the discovery filter: AndroidX's unrelated `GlanceRemoteViewsService` and `RemoteViewsCompatService` are no longer considered candidates. Spotify 9.1.88 exposes `com.spotify.interapp.service.service.AppProtocolRemoteService`, so the Android binding test can now proceed. A successful bind is evidence that the *service* is reachable, not evidence that authorisation or SDK callbacks work.

The original **Authorise Spotify App Remote** pairing button also enables SDK debug-mode logcat during an explicit pairing attempt and now records Activity start/stop, focus and result events. It never copies the SDK's raw internal log messages. If the isolated screen succeeds while the helper's normal pairing still stalls, that points towards the original Activity/SDK lifecycle integration. If both stall, focus on Spotify/Android service connection or authorisation. Neither the isolated test nor JVM tests can verify the result on a real device automatically.

## AudioShelf playback feedback

Helper 0.1.20 with AudioShelf 0.6.15 or later requests immediate authenticated job snapshots, repeating every 750 ms while searching for the phone and every 350 ms while preparing playback. Notification access is not required for this path. Server feedback distinguishes asking Spotify for devices, waiting for the preferred phone, preparing playback, sending Play, and confirming the correct phone and track. Unconfirmed player states distinguish wrong device, wrong track, paused and no player state yet. Once the server reports `preparing_playback` or `sending_play` for the uniquely selected, unrestricted phone, the helper returns immediately. The server continues preparing the canonical queue, sending Play and verifying the exact phone and first track. Device discovery is not playback success and any later failure is still reported in AudioShelf.

The helper logs server-relative times for completed device checks, the duration of each Spotify device request, first device discovery and Play acceptance. It logs the first HTTPS exchange in detail, then **Connected to AudioShelf. Playback-job access accepted.** on the first valid response. Routine polls are summarized at phase changes and every five seconds, with request number, HTTP status and the latest device/playback counters. DNS, TLS, HTTP errors and timeouts are distinguished without logging URLs or credentials. If the 20-second fallback expires, the log includes the last server progress or last network step. Immediate snapshots remain readable while a Spotify Play request is in progress. Older long-poll callers remain compatible, but helper 0.1.14 requires AudioShelf 0.6.15 or later for device-ready return.

## Optional local playback detection

Open the helper directly, tap **Enable Spotify playback detection**, then allow **AudioShelf Spotify playback detection** in Android's notification-access settings. Return to AudioShelf and press Play. This is a one-time grant required by Android to inspect Spotify's media session. The service reads no notification contents. The helper samples only Spotify's media session while a wake attempt is active; it requires a local route, a playing state and active music output. It ignores playback already running at the start unless the track changes. Track identity is compared only in memory, never saved or transmitted.

A fresh local playing transition returns to AudioShelf immediately and records **Android confirms Spotify started playing locally**. This confirms local Spotify playback, not that the exact requested album/disc was accepted; AudioShelf still owns and verifies its canonical queue. The primary server signal reports discovery of the selected Spotify Connect phone using a short-lived, read-only playback-job token; the same server independently sends Play and verifies the exact phone and first track. Device readiness, accepted Play, verified playback or optional local playback detection may trigger early return. Notification access is unnecessary: authenticated server status and the 20-second fallback work independently. If neither signal arrives, the helper returns without claiming success, and AudioShelf continues its one-minute playback job. The helper never opens Spotify in the foreground automatically. If the Spotify App Remote SDK explicitly fails, a one-tap **Open Spotify to restore connection** action becomes available after roughly 1.2 seconds. If the SDK instead remains completely silent, the same manual action becomes available after three seconds. The initial background wake is deferred until the helper activity is resumed/visible. A user-initiated foreground Spotify recovery extends helper status monitoring for 15 seconds after tapping, so opening Spotify near the normal 20-second deadline does not cause an immediate premature return. The server still independently owns the exact device and tracklist. The helper still waits for the independently authenticated selected-phone discovery, so normal successful wakes remain background-only.

AudioShelf attempts one automatic helper launch per Play request. Some browsers can block an external app launch after asynchronous network requests; **Wake Spotify and return** stays available as a manual fallback without reloading or cancelling the pending playback. No additional tap is needed when playback has been confirmed. Closing the waiting dialog cancels playback; waking Spotify does not override that cancellation.

## Wake diagnostics

The helper displays elapsed timestamps for request validation, background connection, SDK success/failure, read-only player status, AudioShelf job state, return and disconnection. Error logs contain exception types and cause types, never raw SDK error payloads, client IDs, access tokens, URLs or track names. The last wake log is saved locally and appears when opening the helper from the app drawer. Tap **Copy log** to share it when troubleshooting.

For a failed attempt, open the helper directly and enable **Keep open for diagnostics (return manually)** before pressing Play in AudioShelf. The helper then stays visible after the wake attempt, and any Spotify player status changes can be observed while AudioShelf's server continues its one-minute playback handoff. Copy the log and tap **Return to AudioShelf**. Turn the option off to restore automatic return. The helper reads server-confirmed device discovery, Play acceptance, ongoing verification and final job state and, with notification access, local Spotify playback state. AudioShelf still reports exact-track confirmation, playback failure or expiry.

### Compare wake methods

Helper 0.1.18 can compare initial SDK timing in the same installed app:

1. Open the helper from the app drawer and tap **Diagnostic settings**.
2. Select **Previous wake method** or **Current wake method**, then **Save for diagnostic test**. This also enables diagnostic hold.
3. Close Spotify in your usual way, return to AudioShelf and press Play on the same album and preferred phone. Keep the server, Spotify version and other settings unchanged between tests.
4. Leave the helper open until the job finishes, copy its log and note whether you actually heard the requested album. Return manually. Avoid manual Spotify recovery during the comparison; if used, the log records that intervention.
5. Alternate the two methods for several attempts. The method cannot change during an active attempt. After testing, choose **Current wake method**, then turn off **Keep open for diagnostics** to restore the default automatic return.

Previous starts the initial background connection immediately after request validation and server approval, as 0.1.14 did. Current waits until the Activity is resumed, as 0.1.17 did. Both use the same SDK version, connection parameters, selected device, playback monitoring and return policy. This isolates initial wake timing rather than reinstalling all of 0.1.14. On a first-use approval prompt both may start after resume, so the log also records the actual resumed state at SDK startup.

Logs retain the beginning and latest events if their size limit is reached, marking any omitted middle entries. Periodic polling summaries prevent the normal one-minute test from burying its startup and SDK result. The screen, copied log and saved last-attempt log contain the same text. Opening settings does not overwrite the previous attempt's log.

## Updating the helper

Ensure AudioShelf is at v0.6.15 or later, then install helper 0.1.20 over the existing helper. No new server update is needed for App Remote pairing. Afterwards, open **AudioShelf Spotify Helper** from the phone's app drawer. It checks for a published update automatically, and **Check for updates** retries manually. Accept **Download**, then confirm Android's installation screen. On the first update Android may ask you to allow this helper to install apps; enable that setting and return. Installation still needs Android's confirmation. The signing fingerprint stays the same while the repository uses the same private signing key.

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
