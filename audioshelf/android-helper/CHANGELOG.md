# 0.1.20

- Fix the **Authorise Spotify App Remote** launcher button looking inert. The pairing path is not a playback wake, so the existing `record()` intentionally discards its messages. Give pairing its own live, timestamped log directly below the button, saved separately from the last wake log and copied with **Copy pairing log**.
- Show pairing status prominently as soon as the button is tapped, record validation, SDK connect start, authorisation callback success/error, Activity foreground/background transitions, 10-second no-callback notice and explicit 30-second expiry. Never claim authorisation was confirmed on timeout.
- Guard late callbacks from a previous timed-out attempt, disconnect any late connected session, and provide a specific SDK error rather than silently doing nothing. Keep the helper's existing independent wake and playback logic intact.
- Distinguish **Copy wake log** from **Copy pairing log** and add **Clear previous wake log** on the direct-launch screen without erasing pairing diagnostics or requiring reinstall.
- Add a JVM regression proving independent log streams, reset on a new pairing attempt and unchanged previous-wake history. No change to Home Assistant, Spotify Web API, selected device or canonical playback.

# 0.1.19

- Separate explicit Spotify App Remote authorisation from background playback. Open the helper from the app drawer and use **Authorise Spotify App Remote** to approve the native SDK scope in a deliberate foreground setup session. The existing trusted Spotify client ID is reused and never displayed or stored as a new secret; no playback commands are issued.
- Routine Play handoffs use `showAuthView(false)`: don't ask Spotify's background service to launch a potentially blocked Android authorisation activity. Continue the existing server-owned Spotify Connect polling, selected-device safeguards, canonical queue and 20-second return deadline. The explicit recovery button remains available for cold starts that still fail.
- Pairing reports successful native SDK connection only after `onConnected`, or describes the SDK exception and directs the user to verify the Spotify developer Android package and signing SHA-1. Do not treat Web API connection or an issued Play command as proof of native App Remote authorisation.
- This targets a documented Android/Spotify background-activity authorisation failure. It is not a guarantee of silent cold starts on Android 17; complete real-device verification is still required. Add JVM regressions for interactive vs noninteractive modes.

# 0.1.18

- Add diagnostic settings to compare **Previous wake method** (0.1.14 initial SDK timing, immediately after request validation and server approval) with **Current wake method** (0.1.17 timing, after the helper becomes visible). The default remains Current. Both methods use the same SDK call, playback monitoring, recovery and return policy; this comparison does not restore the entire old app.
- Choosing a method enables **Keep open for diagnostics**, so both tests can observe the full server job. Settings are changed from the launcher between attempts; the active method is frozen and named in each log, along with whether the Activity had resumed at SDK startup.
- Condense successful polling into phase changes and five-second snapshots with counters and HTTP status. Keep first-connection details, connection errors/recovery, SDK events and final outcomes. Long logs retain startup plus recent entries instead of deleting the beginning. Copy log and saved logs show the same retained text.
- Add regressions for both lifecycle sequences, trust gating, duplicate prevention, polling summaries and log retention. No server update, notification permission or Spotify foreground launch is required for comparison.

# 0.1.17

- Start the Spotify App Remote connection only after the helper Activity becomes visible/resumed, rather than during onCreate before onResume. This aligns more closely with Spotify's foreground/activity lifecycle guidance and may avoid the silent no-callback authorization startup seen on Android 17.
- If the user deliberately taps **Open Spotify to restore connection**, extend the helper's active monitoring by 15 seconds from that action, even if the button was pressed at 19.5 seconds. Keep the server's original independent playback job and its own expiry unchanged. Normal background-only wake still returns on its original 20-second deadline.
- Distinguish the recovery grace deadline clearly in logs. After returning from foreground Spotify, continue using the live original job and existing safeguards for SDK retry, device pinning and avoiding duplicate playback.
- Continue to never open Spotify in the foreground automatically or send Play commands from the helper. Add JVM regressions for visibility-gated initial SDK connection and bounded recovery window.

# 0.1.16

- Fix missing Spotify recovery button when App Remote silently stalls without invoking either SDK callback. After three seconds without a callback, display **Open Spotify to restore connection** while AudioShelf keeps its existing device-pinned playback job running. Retain the existing 1.2-second recovery option on an explicit SDK failure.
- This is strictly a user-initiated fallback: never automatically bring Spotify to the foreground. Healthy SDK connections and normal phone-discovery returns remain unchanged.
- If Spotify was manually opened while an App Remote request is still pending, do not start a duplicate connection when returning to the helper. Once a prior failed attempt has finished, retry safely, with a fresh no-callback timeout.
- Android regression tests cover callback silence, early successes and completed jobs. No changes to AudioShelf server, Spotify queue, tracklist, connected device or Android notification permissions.

# 0.1.15

- When Spotify App Remote fails early with `UserNotAuthorizedException`, diagnose it as an App Remote authorisation failure, separate from AudioShelf Web API rate limits. The server continues looking for the specifically selected phone for the normal 20-second window, so a successful background wake still returns automatically as before.
- Add an explicit, user-controlled **Open Spotify to restore connection** recovery button when any SDK connection fails. The button is hidden until a short settling window has passed. Never launch Spotify automatically and never send playback commands or select another device.
- When the user taps recovery, launch only Spotify's installed launcher activity; continue monitoring the authenticated AudioShelf job. After the user returns to the helper, retry the App Remote authorisation while the server continues the original device-pinned job. Automatic return to AudioShelf is still governed only by preferred-phone discovery, accepted Play, verified playback or the original wake deadline.
- Preserve the existing helper behaviour on successful wakes and the optional diagnostic hold. A manual recovery may require returning via Android Back; modern Android restricts silent app-to-app focus switching.

# 0.1.14

- Return to AudioShelf as soon as the authenticated server identifies the unique, unrestricted preferred phone on Spotify Connect, without waiting for playback preparation or for Spotify to accept Play.
- Continue the existing server-owned canonical album/disc queue and exact-device/track confirmation after the helper closes. A return at discovery never claims Play succeeded; the PWA continues monitoring success or failure.
- Keep Play-accepted and verified-playback early-return fallbacks, the 20-second maximum for slow discovery, first-use authorisation safety and optional diagnostic hold. In diagnostic hold, continue reading final job state after discovery.
- Requires AudioShelf 0.6.15 or later for immediate status snapshots. Test on a real phone because disconnecting Spotify App Remote sooner could affect certain device wake-ups.

# 0.1.13

- Return to AudioShelf immediately once the server reports Spotify accepted the Play command, without waiting for exact-track verification. Keep normal final confirmation and failure reporting in AudioShelf.
- Extend the independent fallback from eight seconds to 20 seconds for slower cold starts. It remains a maximum rather than a compulsory wait, and never implies playback success.
- Increase read-only AudioShelf status polling to 350 ms once device preparation begins and 750 ms otherwise. Show server-relative device check, HTTP probe, discovery and Play acceptance times in the diagnostic log.
- Requires AudioShelf 0.6.15 to support Play-accepted return; older servers still work through their verified result or the fallback.

# 0.1.12

- Request immediate AudioShelf job snapshots every second instead of starting with a 20-second long poll. Use shorter connection/read timeouts so failures are visible inside the wake window.
- Log HTTPS request/HTTP response, accepted job access, server playback phase and check counts. Distinguish DNS, TLS, HTTP and timeout failures, and summarize the last known status at timed return.
- Clarify that notification access is optional; AudioShelf server feedback is the normal permission-free path. Requires AudioShelf 0.6.14 for immediate snapshots and progress diagnostics.

# 0.1.11

- Remove foreground Spotify recovery and its saved opt-in. SDK failure no longer launches Spotify or implies the background wake failed.
- Add local Spotify playback detection using Android media sessions, with a one-time notification-access setup button. Detect a fresh local playing transition independently of the Spotify SDK and AudioShelf network connection; ignore existing playback, other apps and remote playback routes.
- Return early on local playback detection or an authenticated AudioShelf job result. Restore the independent eight-second return deadline when neither signal arrives. The deadline is logged as unconfirmed, not playback success.
- Keep diagnostic hold, saved logs and server monitoring. Android media observation reads no notification text and sends no playback commands; transient track identity is kept only in memory to reject stale playback.

# 0.1.10

- Start the Spotify SDK wake before the AudioShelf long-poll rather than racing their initialisation.
- Recover from an explicit Spotify SDK failure with a foreground Spotify launch (on by default, disabled during diagnostic hold). The original phone, canonical album and playback job remain unchanged. A manual **Open Spotify now** action is available without waiting.
- Distinguish Spotify SDK failures from network/DNS errors and suppress duplicate network-error logs. Actual playback is still confirmed by AudioShelf, never by a timer.

# 0.1.9

- Return on authenticated AudioShelf playback-job result rather than SDK callback/timeout. Diagnose SDK and network failures separately; preserve manual and diagnostic hold.

# 0.1.8

- Return automatically after eight seconds if Spotify's App Remote SDK never calls back, even if background playback has already begun; the previous 45-second wait left the helper visible unnecessarily.
- Preserve at least five seconds for Spotify to wake, the longer authorisation flow when its screen is in front, and diagnostic hold when enabled.
- Add unit tests for the settling and pending-callback deadlines.

# 0.1.7

- Restore the five-second settling window before disconnecting a successful Spotify SDK session. SDK connection alone does not confirm Spotify Connect readiness.
- Show a timestamped wake log, safe SDK error and cause types, Spotify/Android/helper versions, read-only player status, and return/disconnection events. Save the last wake log for viewing and copying after return.
- Add an optional Keep open for diagnostics setting to pause automatic return while inspecting a wake attempt.

# 0.1.6

- Return immediately after Spotify App Remote connects successfully. Keep the five-second settling window after connection failure and the 45-second pending connection timeout.

# 0.1.5

- Add automatic update checks when opening the helper directly, a Check for updates button, verified APK downloads, and Android install confirmation. Updates never interrupt Spotify handover.
- Publish the signed helper update channel on main helper changes; published builds require the saved signing key.
- Match the AudioShelf web icon with the same wine background, record, grooves, cream label and shelf, plus a small green helper badge. Include an adaptive Android icon.

# 0.1.4

- Restore the original Spotify App Remote service wake without any launcher intent or foreground task switching. Wait in the helper for at least five seconds before returning, including after an SDK failure.
- Keep the trusted AudioShelf return link. A pending connection gets up to 45 seconds; first-use Spotify authorisation is allowed to finish before returning.
- Keep the working foreground route available through AudioShelf's manual Open Spotify link.

# 0.1.2

- Explicitly launch Spotify before attempting the optional App Remote connection. SDK failure no longer skips the Spotify launch or shortens its five-second warm-up.
- Return to the trusted AudioShelf HTTPS page automatically, including its album route, instead of waiting for the helper to become visible again.
- Keep playback and device readiness checks in AudioShelf.

# 0.1.1

- Return automatically to AudioShelf when the Spotify SDK connection fails or times out. The SDK can wake Spotify and allow the existing server handoff to start playback even if its own remote session fails.
- Keep actual playback status with AudioShelf, which still waits for the preferred device and preserves the selected tracklist.
- Preserve foreground-only return, cancellation and late-callback cleanup.
