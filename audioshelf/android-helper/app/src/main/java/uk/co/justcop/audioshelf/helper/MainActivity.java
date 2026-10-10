package uk.co.justcop.audioshelf.helper;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.provider.Settings;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ActivityNotFoundException;
import android.net.Uri;
import android.os.Bundle;
import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.ScrollView;
import android.widget.Toast;
import android.widget.LinearLayout;
import android.widget.TextView;
import com.spotify.android.appremote.api.ConnectionParams;
import com.spotify.android.appremote.api.Connector;
import com.spotify.android.appremote.api.SpotifyAppRemote;

/** Wakes the local Spotify service. Never selects a device or sends playback commands. */
public final class MainActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService statusExecutor = Executors.newSingleThreadExecutor();
    private SpotifyAppRemote remote;
    private SpotifyAuthGrant pairingGrant;
    private HelperUpdater updater;
    private HelperScreen screen;
    private SpotifyPlaybackMonitor playbackMonitor;
    private Button playbackAccess, spotifyRecovery, spotifyPair;
    private TextView pairingStatus, pairingDiagnostics;
    private boolean localMonitorError;
    private String lastLocalStatus;
    private boolean checkUpdatesWhenVisible;
    private TextView message, diagnostics;
    private final WakeLog diagnosticLog = new WakeLog();
    private WakeLog pairingLog = new WakeLog();
    private long pairingStartedAt;
    private int pairingAttemptId;
    private final WakeProgressLog progressLog = new WakeProgressLog();
    private long diagnosticStartedAt;
    private boolean wakeRequest, keepOpen;
    private String lastPlayerStatus;
    private boolean completed, connecting, resumed, sdkFailed;
    private boolean foregroundRecoveryOpened, sdkCallbackReceived;
    private boolean sdkWakeStarted, manualRecoveryGrace, pairingSpotify;
    private String wakeClientId;
    private final WakeReturnState wakeReturn = new WakeReturnState();
    private String lastStatusError, lastServerProgress;
    private String transportStage = "not started";
    private boolean serverContacted;
    private boolean playAcceptanceLogged;
    private boolean deviceReadinessLogged;
    private int statusAttempts;
    private String origin, accountId, jobId, helperToken, lastServerState;
    private Uri returnUri;
    private final Runnable pollStatus = this::readPlaybackStatus;
    private final Runnable silentSdkRecovery = () -> {
        if (!wakeRequest || completed || isFinishing() || isDestroyed()) return;
        boolean active = wakeReturn.shouldPoll(keepOpen);
        if (SpotifyWakeRecovery.shouldOfferManualRecovery(
                sdkCallbackReceived, deviceReadinessLogged || playAcceptanceLogged,
                active, SystemClock.elapsedRealtime() - diagnosticStartedAt)) {
            // Android/Spotify can fail to deliver either SDK callback. The old
            // button only appeared after onFailure and was invisible in this case.
            if (spotifyRecovery != null && spotifyRecovery.getVisibility() != View.VISIBLE) {
                record("Spotify SDK has returned neither success nor failure after three seconds. Manual recovery is available.");
                spotifyRecovery.setVisibility(View.VISIBLE);
                message.setText("Spotify has not responded to its background connection. You can open Spotify to restore it, or continue waiting for AudioShelf to find the selected phone.");
            }
        }
    };
    private final Runnable pollLocalPlayback = this::readLocalPlayback;
    private final Runnable returnDeadline = () -> {
        if (completed || isFinishing() || isDestroyed()) return;
        wakeReturn.onDeadline();
        record(serverContacted ? "Last AudioShelf progress: " + lastServerProgress :
            "No authenticated AudioShelf status received before return. Last network step: " + transportStage);
        record(manualRecoveryGrace
            ? "User-initiated Spotify recovery grace ended. Play was not yet confirmed accepted; AudioShelf continues checking."
            : "20-second wake deadline reached. Play was not confirmed accepted; AudioShelf continues checking.");
        message.setText(keepOpen ? "Wake window finished. Diagnostic hold keeps this log open." :
            "Returning to AudioShelf to check playback…");
        if (keepOpen) record("Diagnostic hold enabled; status monitoring continues until the job finishes.");
        returnWhenVisible();
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        diagnosticStartedAt = SystemClock.elapsedRealtime();
        Uri data = getIntent().getData();
        screen = new HelperScreen(this, data != null, this::pairSpotifyRemote,
            () -> { if (updater != null) updater.check(); },
            () -> { if (returnUri != null) returnToAudioShelf(); else screen.showHome(); },
            checked -> {
                keepOpen = checked;
                if (wakeRequest) record(checked
                    ? "Diagnostic hold enabled for this wake." : "Diagnostic hold disabled.");
                if (!checked) returnWhenVisible();
            });
        message = screen.message;
        diagnostics = screen.wakeLog;
        pairingDiagnostics = screen.pairingLog;
        spotifyPair = screen.pairButton;
        updater = new HelperUpdater(this, screen.updateStatus);
        if (data == null) {
            diagnostics.setText(getPreferences(MODE_PRIVATE).getString(
                "last_wake_log", "No wake attempt recorded yet."));
            pairingDiagnostics.setText(getPreferences(MODE_PRIVATE).getString(
                "last_pairing_log", "No pairing attempt recorded yet."));
            boolean trusted = SpotifyRemoteAuthPolicy.validClientId(
                getPreferences(MODE_PRIVATE).getString("trusted_client", ""));
            boolean authorised = getPreferences(MODE_PRIVATE).getBoolean(
                "spotify_authorized", false);
            screen.setSetupState(trusted, authorised, false);
            return;
        }
        wakeRequest = true;
        record("AudioShelf helper " + BuildConfig.VERSION_NAME + " (" + BuildConfig.VERSION_CODE + ")");
        record("Wake request received. " + (keepOpen ? "Diagnostic hold enabled." : "Automatic return enabled."));
        record("Foreground-visible Spotify SDK wake policy selected.");
        String clientId = data.getQueryParameter("client_id");
        origin = data.getQueryParameter("origin");
        accountId = data.getQueryParameter("account_id");
        jobId = data.getQueryParameter("job_id");
        helperToken = data.getQueryParameter("helper_token");
        String returnUrl = data.getQueryParameter("return_url");
        if (!"audioshelf-helper".equals(data.getScheme()) || !"wake".equals(data.getHost())
                || clientId == null || !clientId.matches("[a-fA-F0-9]{32}") || !validOrigin(origin)
                || !validReturnUrl(returnUrl, origin) || !validJobCredentials()) {
            fail("Invalid AudioShelf helper request.");
            return;
        }
        wakeClientId = clientId;
        returnUri = Uri.parse(returnUrl == null ? origin : returnUrl);
        record("Request and HTTPS return address validated. No playback commands are sent by this helper.");
        String trusted = getPreferences(MODE_PRIVATE).getString("trusted_origin", "");
        String trustedClient = getPreferences(MODE_PRIVATE).getString("trusted_client", "");
        if (origin.equals(trusted) && clientId.equals(trustedClient)) {
            record("Saved AudioShelf server trusted.");
            watchAndConnect(clientId);
        } else {
            message.setText("Confirm your AudioShelf server.");
            new AlertDialog.Builder(this)
                .setTitle("Allow AudioShelf to wake Spotify?")
                .setMessage(origin + "\n\nThis helper wakes Spotify in the background, then returns to this AudioShelf server. AudioShelf controls playback on your saved device.")
                .setPositiveButton("Allow", (dialog, which) -> {
                    getPreferences(MODE_PRIVATE).edit().putString("trusted_origin", origin)
                        .putString("trusted_client", clientId).apply();
                    record("AudioShelf server approved.");
                    watchAndConnect(clientId);
                })
                .setNegativeButton("Cancel", (dialog, which) -> finishHelper())
                .setOnCancelListener(dialog -> finishHelper()).show();
        }
    }

    private boolean validOrigin(String value) {
        if (value == null || value.length() > 2048) return false;
        Uri uri = Uri.parse(value);
        return "https".equals(uri.getScheme()) && uri.getHost() != null
            && uri.getUserInfo() == null && uri.getQuery() == null && uri.getFragment() == null
            && (uri.getPath() == null || uri.getPath().isEmpty());
    }

    private boolean validReturnUrl(String value, String origin) {
        if (value == null) return true; // Older AudioShelf versions return to their origin.
        if (value.length() > 8192) return false;
        Uri uri = Uri.parse(value);
        Uri base = Uri.parse(origin);
        return "https".equals(uri.getScheme()) && uri.getUserInfo() == null
            && base.getHost().equalsIgnoreCase(uri.getHost()) && base.getPort() == uri.getPort();
    }

    private boolean validJobCredentials() {
        return accountId != null && (accountId.equals("owner") || accountId.matches("[a-f0-9]{32}"))
            && jobId != null && jobId.matches("[A-Za-z0-9_-]{20,128}")
            && helperToken != null && helperToken.matches("[A-Za-z0-9_-]{32,128}");
    }

    private void watchAndConnect(String clientId) {
        // The deadline is independent of SDK callbacks and network status requests.
        handler.postDelayed(returnDeadline, WakeReturnState.WAKE_WINDOW_MS);
        record("Background-only wake. Return when AudioShelf finds the selected Spotify Connect phone, otherwise after 20 seconds.");
        playbackMonitor = new SpotifyPlaybackMonitor(this);
        record(SpotifyPlaybackMonitor.enabled(this) ? "Local Spotify playback detection enabled." :
            "Optional local playback detection disabled. Using AudioShelf server feedback.");
        // Establish a baseline before waking Spotify so existing playback is ignored.
        readLocalPlayback();
        // Only start from a visible Activity after request validation and trust.
        // The legacy pre-resume timing experiment has been retired.
        startInitialSdkWake();
        if (!sdkWakeStarted) record("Waiting for the helper to become visible before attempting Spotify App Remote.");
        if (completed) return;
        record("AudioShelf job status monitoring started.");
        readPlaybackStatus();
    }

    private void readLocalPlayback() {
        if (completed || playbackMonitor == null || !wakeReturn.shouldPoll(keepOpen)) return;
        try {
            boolean started = playbackMonitor.sample();
            if (!playbackMonitor.status.equals(lastLocalStatus)) {
                lastLocalStatus = playbackMonitor.status;
                record("Android playback: " + lastLocalStatus + ".");
            }
            if (started) {
                wakeReturn.onLocalPlayback();
                handler.removeCallbacks(returnDeadline);
                record("Android confirms Spotify started playing locally. Returning without waiting for SDK or server.");
                message.setText("Spotify is playing on this phone. Returning to AudioShelf…");
                returnWhenVisible();
                return;
            }
        } catch (SecurityException error) {
            if (!localMonitorError) record("Local playback access not ready or revoked. Server status and return deadline remain active.");
            localMonitorError = true;
        }
        handler.postDelayed(pollLocalPlayback, 500);
    }

    private void readPlaybackStatus() {
        if (completed || isFinishing() || isDestroyed() || !wakeReturn.shouldPoll(keepOpen)) return;
        int attempt = ++statusAttempts;
        boolean traceRequest = attempt == 1 || lastStatusError != null;
        if (traceRequest) record("AudioShelf status request " + attempt + " started (immediate response requested).");
        statusExecutor.execute(() -> {
            PlaybackStatusClient.Result response = null;
            IOException failure = null;
            try {
                response = PlaybackStatusClient.poll(origin, accountId, jobId, helperToken, stage -> handler.post(() -> {
                    if (completed || isFinishing() || isDestroyed()) return;
                    transportStage = stage;
                    if (traceRequest) record("AudioShelf request " + attempt + ": " + stage + ".");
                }));
            } catch (IOException exception) {
                failure = exception;
            }
            final PlaybackStatusClient.Result result = response;
            final IOException error = failure;
            handler.post(() -> {
                if (completed || isFinishing() || isDestroyed() || !wakeReturn.shouldPoll(keepOpen)) return;
                if (error != null) {
                    String reason = PlaybackStatusClient.describe(error);
                    if (!reason.equals(lastStatusError)) {
                        lastStatusError = reason;
                        record("AudioShelf request " + attempt + " failed: " + reason
                            + ". Last network step: " + transportStage + ". Retrying until the wake window ends.");
                    }
                    message.setText("AudioShelf status connection unavailable. Background wake continues; automatic return has a time limit.");
                    handler.postDelayed(pollStatus, 500);
                    return;
                }
                if (!serverContacted && result.httpStatus == 200) {
                    serverContacted = true;
                    record("Connected to AudioShelf. Playback-job access accepted.");
                }
                if (lastStatusError != null) {
                    record("AudioShelf status connection restored.");
                    lastStatusError = null;
                }
                String progress = result.progress();
                lastServerProgress = progress;
                if (progressLog.shouldRecord(result.state, result.phase, result.playAccepted,
                        result.deviceSeenMs, SystemClock.elapsedRealtime() - diagnosticStartedAt)) {
                    record("AudioShelf progress: " + progress + " [status request " + attempt
                        + ", HTTP " + result.httpStatus + "].");
                }
                if (!result.state.equals(lastServerState)) {
                    lastServerState = result.state;
                    record("AudioShelf playback: " + result.state + ".");
                }
                if (result.state.equals("waiting")) {
                    // The server has already selected exactly one unrestricted matching
                    // Spotify Connect phone. Its worker continues Play and verification
                    // independently of this helper and of the PWA foreground state.
                    boolean selectedPhoneFound = "preparing_playback".equals(result.phase)
                        || "sending_play".equals(result.phase);
                    if (selectedPhoneFound) {
                        wakeReturn.onDeviceReady();
                        handler.removeCallbacks(returnDeadline);
                        if (!deviceReadinessLogged) {
                            deviceReadinessLogged = true;
                            record(keepOpen
                                ? "Selected phone found on Spotify Connect. Diagnostic hold continues through playback confirmation."
                                : "Selected phone found on Spotify Connect. Returning before Play; AudioShelf's server continues playback independently.");
                        }
                        message.setText(keepOpen ? "Phone found. Watching server playback for diagnostics…" :
                            "Phone found. Returning while AudioShelf starts playback…");
                        if (keepOpen) {
                            handler.postDelayed(pollStatus, 350);
                        } else {
                            returnWhenVisible();
                        }
                        return;
                    }
                    if (result.playAccepted) {
                        wakeReturn.onPlayAccepted();
                        handler.removeCallbacks(returnDeadline);
                        if (!playAcceptanceLogged) {
                            playAcceptanceLogged = true;
                            record("Spotify accepted Play. AudioShelf will verify the requested phone and track independently.");
                        }
                        message.setText(keepOpen ? "Play accepted. Watching final confirmation (diagnostics)…" :
                            "Play command accepted. Returning to AudioShelf while it confirms playback…");
                        if (keepOpen) {
                            handler.postDelayed(pollStatus, 350);
                        } else {
                            returnWhenVisible();
                        }
                        return;
                    }
                    message.setText(sdkFailed ? "Spotify SDK session failed, but background waking may still work. Waiting for AudioShelf…" :
                        "Waiting for Spotify to accept Play on your phone…");
                    handler.postDelayed(pollStatus,
                        ("preparing_playback".equals(result.phase) || "sending_play".equals(result.phase)
                            || "play_accepted".equals(result.phase) || "confirming_playback".equals(result.phase))
                            ? 350 : 750);
                    return;
                }
                wakeReturn.onServerState(result.state);
                handler.removeCallbacks(returnDeadline);
                record("AudioShelf job finished: " + result.state + ". "
                    + (keepOpen ? "Diagnostic hold keeps this result open." : "Returning to AudioShelf if visible."));
                message.setText((result.state.equals("started") ? "Playback confirmed." : "AudioShelf reports: " + result.message)
                    + (keepOpen ? "\nCopy the log, then return manually." : "\nReturning to AudioShelf…"));
                if (keepOpen) record("Diagnostic hold enabled; waiting for manual return.");
                returnWhenVisible();
            });
        });
    }

    private void startInitialSdkWake() {
        if (!WakeStartupPolicy.shouldStart(resumed, wakeRequest,
                sdkWakeStarted, !completed && !isFinishing() && !isDestroyed()
                    && wakeClientId != null && playbackMonitor != null)) return;
        sdkWakeStarted = true;
        record("Starting foreground-visible SDK wake; helper resumed=" + resumed + ".");
        connect(wakeClientId);
        handler.removeCallbacks(silentSdkRecovery);
        handler.postDelayed(silentSdkRecovery, SpotifyWakeRecovery.NO_CALLBACK_GRACE_MS);
    }

    private void openSpotifyRecovery() {
        // A failed App Remote authorisation is not evidence that playback
        // commands were sent or that Spotify has even started. Opening the
        // foreground Spotify UI is ONLY ever a deliberate user gesture.
        if (!wakeRequest || completed || returnUri == null || !wakeReturn.shouldPoll(keepOpen)) return;
        Intent launch = getPackageManager().getLaunchIntentForPackage("com.spotify.music");
        if (launch == null) {
            record("Cannot open Spotify: launcher activity unavailable.");
            message.setText("Spotify cannot be opened. Start it manually, then return to AudioShelf.");
            return;
        }
        try {
            foregroundRecoveryOpened = true;
            record("User requested Spotify foreground recovery; no playback commands or device switching were sent.");
            message.setText("Open Spotify and, if asked, approve access. Then press Back to return here; AudioShelf continues waiting for your selected phone.");
            spotifyRecovery.setVisibility(View.GONE);
            startActivity(launch);
            // The normal 20s deadline is appropriate for silent background
            // wake, but insufficient when Spotify is deliberately opened at
            // 19.5s. Give it an additional bounded registration window.
            handler.removeCallbacks(returnDeadline);
            handler.postDelayed(returnDeadline, SpotifyWakeRecovery.USER_RECOVERY_GRACE_MS);
            manualRecoveryGrace = true;
            record("User-requested Spotify recovery granted 15 seconds for the selected phone to register on Connect.");
        } catch (ActivityNotFoundException | SecurityException error) {
            foregroundRecoveryOpened = false;
            spotifyRecovery.setVisibility(View.VISIBLE);
            record("Unable to open Spotify: " + WakeDiagnostics.failure(error));
            message.setText("Open Spotify manually and return here to continue the original playback request.");
        }
    }

    private void pairingRecord(String event) {
        if (pairingDiagnostics == null) return;
        pairingLog.append(WakeDiagnostics.line(SystemClock.elapsedRealtime() - pairingStartedAt, event));
        String log = pairingLog.toString();
        pairingDiagnostics.setText(log);
        getPreferences(MODE_PRIVATE).edit().putString("last_pairing_log", log).apply();
    }

    private void showPairingStatus(String detail) {
        if (pairingStatus != null) pairingStatus.setText(detail);
        if (message != null) message.setText("AudioShelf Spotify Helper " + BuildConfig.VERSION_NAME + "\n" + detail);
    }

    /**
     * Explicit pairing uses the Android 14+ activity-start grant established by
     * the successful isolated test. Routine AudioShelf playback wakes are unchanged.
     */
    private void pairSpotifyRemote() {
        if (wakeRequest || pairingSpotify || completed || isFinishing() || isDestroyed()) {
            showPairingStatus("Pairing cannot start during an active wake or another pairing request.");
            return;
        }
        pairingLog = new WakeLog();
        pairingStartedAt = SystemClock.elapsedRealtime();
        final int attempt = ++pairingAttemptId;
        pairingRecord("AudioShelf helper " + BuildConfig.VERSION_NAME
            + ": explicit Spotify App Remote pairing button pressed.");
        String clientId = getPreferences(MODE_PRIVATE).getString("trusted_client", "");
        if (!SpotifyRemoteAuthPolicy.validClientId(clientId)) {
            pairingRecord("Blocked: no valid trusted Spotify client ID. Launch Play from AudioShelf first.");
            showPairingStatus("Cannot pair yet: first launch Play from AudioShelf to trust its Spotify client ID.");
            return;
        }
        if (!SpotifyAppRemote.isSpotifyInstalled(this)) {
            pairingRecord("Blocked: Spotify is not installed on this phone.");
            showPairingStatus("Install and sign in to Spotify before pairing.");
            return;
        }

        pairingSpotify = true;
        spotifyPair.setEnabled(false);
        showPairingStatus("Preparing Spotify authorisation…");
        pairingRecord("Spotify installed. Foreground user-initiated pairing; no Play command.");
        pairingGrant = new SpotifyAuthGrant(this, handler, this::pairingRecord);
        pairingGrant.start(() -> {
            if (!pairingSpotify || attempt != pairingAttemptId || isFinishing() || isDestroyed()) return;
            beginPairingSdk(clientId, attempt);
        }, reason -> {
            if (!pairingSpotify || attempt != pairingAttemptId || isFinishing() || isDestroyed()) return;
            pairingRecord("Cannot prepare Spotify authorisation: " + reason);
            finishPairing();
            showPairingStatus("Cannot start Spotify authorisation: " + reason
                + ". The Spotify service may be unavailable.");
        });
    }

    private void finishPairing() {
        pairingSpotify = false;
        SpotifyAppRemote.setDebugMode(false);
        if (pairingGrant != null) {
            pairingGrant.close();
            pairingGrant = null;
        }
        if (spotifyPair != null) spotifyPair.setEnabled(true);
    }

    private void beginPairingSdk(String clientId, final int attempt) {
        showPairingStatus("Permission granted. Waiting for Spotify authorisation…");
        pairingRecord("Starting SDK connection with trusted client ID, registered redirect and showAuthView=true.");
        SpotifyAppRemote.setDebugMode(true);
        pairingRecord("Verbose SDK diagnostics enabled in Android logcat; no OAuth payload included in copied log.");
        try {
            ConnectionParams params = new ConnectionParams.Builder(clientId)
                .setRedirectUri(SpotifyRemoteAuthPolicy.REDIRECT_URI)
                .showAuthView(SpotifyRemoteAuthPolicy.showAuthView(true)).build();
            SpotifyAppRemote.connect(this, params, new Connector.ConnectionListener() {
                @Override public void onConnected(SpotifyAppRemote appRemote) {
                    handler.post(() -> {
                        if (isFinishing() || isDestroyed() || attempt != pairingAttemptId || !pairingSpotify) {
                            SpotifyAppRemote.disconnect(appRemote);
                            return;
                        }
                        pairingRecord("Spotify App Remote onConnected callback received. Authorisation confirmed.");
                        finishPairing();
                        showPairingStatus("Spotify App Remote authorised successfully. Try Play from AudioShelf with Spotify closed.");
                        SpotifyAppRemote.disconnect(appRemote);
                    });
                }
                @Override public void onFailure(Throwable error) {
                    handler.post(() -> {
                        if (isFinishing() || isDestroyed() || attempt != pairingAttemptId || !pairingSpotify) return;
                        String reason = WakeDiagnostics.failure(error);
                        pairingRecord("Spotify App Remote pairing failed: " + reason);
                        finishPairing();
                        showPairingStatus("Pairing failed: " + reason
                            + ". Check your Spotify Android registration if this persists.");
                    });
                }
            });
        } catch (RuntimeException error) {
            String reason = WakeDiagnostics.failure(error);
            pairingRecord("Spotify SDK threw while requesting connection: " + reason);
            finishPairing();
            showPairingStatus("Pairing could not start: " + reason);
            return;
        }

        handler.postDelayed(() -> {
            if (pairingSpotify && attempt == pairingAttemptId && !isFinishing() && !isDestroyed()) {
                pairingRecord("No Spotify SDK success or failure callback after 10 seconds.");
                showPairingStatus("Spotify has not responded for 10 seconds. Check the authorisation screen.");
            }
        }, 10000);
        handler.postDelayed(() -> {
            if (pairingSpotify && attempt == pairingAttemptId && !isFinishing() && !isDestroyed()) {
                pairingRecord("Pairing timed out after 30 seconds without SDK callback. No authorisation confirmed.");
                finishPairing();
                showPairingStatus("Pairing timed out without a Spotify SDK callback. Use isolated diagnostics if necessary.");
            }
        }, 30000);
    }

    private void connect(String clientId) {
        if (completed || connecting) return;
        if (!SpotifyAppRemote.isSpotifyInstalled(this)) {
            fail("Install Spotify and log in on this phone first.");
            return;
        }
        record("Spotify installed. Starting background SDK connection.");
        try {
            record("Spotify version " + getPackageManager().getPackageInfo("com.spotify.music", 0).versionName
                + "; Android API " + android.os.Build.VERSION.SDK_INT + ".");
        } catch (android.content.pm.PackageManager.NameNotFoundException ignored) {
            record("Spotify version unavailable.");
        }
        connecting = true;
        // Spotify SDK wakes its service; AudioShelf confirms real playback.
        message.setText("Waking Spotify…\nPlease wait here. AudioShelf will keep your selected device and tracklist.");
        // Restore the original SDK service wake, with no Spotify launcher intent.
        // Keep the helper alive while the service/device settles, including when
        // the SDK session fails after successfully waking the Spotify process.
        // Spotify can wake and play without establishing an App Remote session.
        // Only an AudioShelf job result or the independent wake deadline triggers return.
        ConnectionParams params = new ConnectionParams.Builder(clientId)
            .setRedirectUri(SpotifyRemoteAuthPolicy.REDIRECT_URI)
            // The normal Play path must never cause Spotify to launch an
            // authorisation activity from its background service. Authorise
            // separately from the helper's visible launcher screen.
            .showAuthView(SpotifyRemoteAuthPolicy.showAuthView(false)).build();
        SpotifyAppRemote.connect(this, params, new Connector.ConnectionListener() {
            @Override public void onConnected(SpotifyAppRemote appRemote) {
                handler.post(() -> {
                    sdkCallbackReceived = true;
                    if (completed || isFinishing() || isDestroyed()) {
                        SpotifyAppRemote.disconnect(appRemote);
                        return;
                    }
                    remote = appRemote;
                    record("Spotify SDK connected. This does not confirm Spotify Connect device readiness or playback.");
                    appRemote.getPlayerApi().subscribeToPlayerState().setEventCallback(state -> handler.post(() -> {
                        if (completed || isFinishing() || isDestroyed()) return;
                        String status = state.isPaused ? "paused" : "playing";
                        status += state.track == null ? "; no track" : "; track present";
                        if (!status.equals(lastPlayerStatus)) {
                            lastPlayerStatus = status;
                            record("Spotify player reports " + status + ".");
                        }
                    })).setErrorCallback(error -> handler.post(() -> {
                        if (!completed) record("Player status unavailable: " + WakeDiagnostics.failure(error));
                    }));
                    // SDK connection is diagnostic; server device readiness governs return.
                });
            }
            @Override public void onFailure(Throwable error) {
                Log.w("AudioShelfHelper", "Spotify SDK connection failed: " + WakeDiagnostics.failure(error));
                handler.post(() -> {
                    sdkCallbackReceived = true;
                    if (completed || isFinishing() || isDestroyed()) return;
                    connecting = false;
                    sdkFailed = true;
                    boolean authorization = WakeDiagnostics.isAuthorizationFailure(error);
                    record("Spotify SDK failed: " + WakeDiagnostics.failure(error));
                    record(authorization
                        ? "Spotify App Remote authorisation rejected. This is distinct from Web API rate limits."
                        : "Spotify SDK session failure does not confirm wake failure.");
                    record("Continuing background wake; no automatic foreground Spotify launch.");
                    message.setText(authorization
                        ? "Spotify App Remote is not authorised. Open the helper directly after this attempt and use Authorise Spotify App Remote. AudioShelf is still checking for your phone."
                        : "Spotify SDK connection failed. Waiting for AudioShelf; you can open Spotify manually if needed.");
                    // Show a user-triggered escape hatch, but leave Spotify in
                    // the background if this wake succeeded despite SDK failure.
                    handler.postDelayed(() -> {
                        if (!completed && wakeRequest && wakeReturn.shouldPoll(keepOpen)
                                && spotifyRecovery != null) spotifyRecovery.setVisibility(View.VISIBLE);
                    }, 1200);
                });
            }
        });
    }

    private void returnWhenVisible() {
        // Do not steal focus from Spotify's first-use authorisation screen. Normal
        // background waking leaves this helper visible throughout the wait.
        if (!completed && wakeReturn.shouldReturn(resumed, keepOpen)) returnToAudioShelf();
    }

    private void returnToAudioShelf() {
        if (completed || isFinishing() || isDestroyed()) return;
        Intent back = new Intent(Intent.ACTION_VIEW, returnUri);
        back.addCategory(Intent.CATEGORY_BROWSABLE);
        back.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            // Resolve the trusted HTTPS URL normally, including an installed PWA.
            // The automatic return runs while the helper is visible. SDK service
            // waking does not put Spotify in the foreground.
            record("Opening AudioShelf return address.");
            startActivity(back);
            finishHelper();
        } catch (ActivityNotFoundException | SecurityException error) {
            fail("Spotify was opened. Return to AudioShelf to check playback.");
        }
    }
    private void fail(String reason) {
        if (completed || isDestroyed()) return;
        completed = true;
        handler.removeCallbacksAndMessages(null);
        if (remote != null) SpotifyAppRemote.disconnect(remote);
        remote = null;
        statusExecutor.shutdownNow();
        record(reason);
        message.setText(reason);
    }
    private void finishHelper() {
        record(remote != null ? "Disconnecting Spotify SDK session and closing helper." : "Closing helper; no connected Spotify SDK session.");
        completed = true;
        handler.removeCallbacksAndMessages(null);
        if (remote != null) SpotifyAppRemote.disconnect(remote);
        remote = null;
        statusExecutor.shutdownNow();
        finishAndRemoveTask();
    }
    @Override protected void onStart() {
        super.onStart();
        if (pairingSpotify) pairingRecord("Helper Activity onStart during pairing.");
    }
    @Override protected void onStop() {
        if (pairingSpotify) pairingRecord("Helper Activity onStop during pairing.");
        super.onStop();
    }
    @Override protected void onResume() {
        super.onResume();
        resumed = true;
        if (playbackAccess != null) playbackAccess.setText(SpotifyPlaybackMonitor.enabled(this) ?
            "Spotify playback detection enabled" : "Enable Spotify playback detection");
        if (wakeRequest) record("Helper visible.");
        if (pairingSpotify) pairingRecord("Helper resumed while Spotify App Remote pairing is pending.");
        startInitialSdkWake();
        returnWhenVisible();
        if (foregroundRecoveryOpened && !completed && wakeClientId != null
                && wakeReturn.shouldPoll(keepOpen)) {
            foregroundRecoveryOpened = false;
            // A foreground Spotify visit can complete App Remote authorisation.
            // Never start a duplicate SDK connection if the original call is
            // still awaiting a callback. Server device discovery remains live.
            if (connecting) {
                record("Returned from Spotify; original SDK connection is still pending. AudioShelf continues checking the phone.");
            } else {
                sdkFailed = false;
                sdkCallbackReceived = false;
                record("Returned from Spotify; retrying App Remote while AudioShelf checks the selected phone.");
                connect(wakeClientId);
                handler.postDelayed(silentSdkRecovery, SpotifyWakeRecovery.NO_CALLBACK_GRACE_MS);
            }
        }
    }
    @Override protected void onPause() {
        resumed = false;
        if (wakeRequest) record("Helper left foreground.");
        if (pairingSpotify) pairingRecord("Helper left foreground during pairing; Spotify authorisation may be visible.");
        super.onPause();
    }
    @Override public void onWindowFocusChanged(boolean focused) {
        super.onWindowFocusChanged(focused);
        if (pairingSpotify) pairingRecord("Helper window focus=" + focused + " during pairing.");
        if (focused && updater != null) {
            if (checkUpdatesWhenVisible) { checkUpdatesWhenVisible = false; updater.check(); }
            updater.onVisible();
        }
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (updater != null) updater.activityResult(request);
        if (pairingSpotify) pairingRecord("Helper received Activity result (request code " + request + "); payload not saved.");
    }
    @Override protected void onDestroy() {
        if (updater != null) updater.close();
        pairingAttemptId++;
        if (pairingSpotify || pairingGrant != null) finishPairing();
        completed = true;
        handler.removeCallbacksAndMessages(null);
        if (remote != null) SpotifyAppRemote.disconnect(remote);
        statusExecutor.shutdownNow();
        super.onDestroy();
    }
    private void record(String event) {
        if (!wakeRequest || diagnostics == null) return;
        diagnosticLog.append(WakeDiagnostics.line(SystemClock.elapsedRealtime() - diagnosticStartedAt, event));
        String text = diagnosticLog.toString();
        diagnostics.setText(text);
        getPreferences(MODE_PRIVATE).edit().putString("last_wake_log", text).apply();
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        // A launcher instance used to inspect logs can receive the next wake intent.
        if (!wakeRequest || completed) {
            setIntent(intent);
            recreate();
            return;
        }
        // Ignore duplicate launches during an active wake without replacing its log.
        record("Duplicate launch ignored while current helper task is open.");
    }
}
