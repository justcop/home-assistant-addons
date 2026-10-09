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
    private HelperUpdater updater;
    private SpotifyPlaybackMonitor playbackMonitor;
    private Button playbackAccess;
    private boolean localMonitorError;
    private String lastLocalStatus;
    private boolean checkUpdatesWhenVisible;
    private TextView message, diagnostics;
    private final StringBuilder diagnosticLog = new StringBuilder();
    private long diagnosticStartedAt;
    private boolean wakeRequest, keepOpen;
    private String lastPlayerStatus;
    private boolean completed, connecting, resumed, sdkFailed;
    private final WakeReturnState wakeReturn = new WakeReturnState();
    private String lastStatusError, lastServerProgress;
    private String transportStage = "not started";
    private boolean serverContacted;
    private int statusAttempts;
    private String origin, accountId, jobId, helperToken, lastServerState;
    private Uri returnUri;
    private final Runnable pollStatus = this::readPlaybackStatus;
    private final Runnable pollLocalPlayback = this::readLocalPlayback;
    private final Runnable returnDeadline = () -> {
        if (completed || isFinishing() || isDestroyed()) return;
        wakeReturn.onDeadline();
        record(serverContacted ? "Last AudioShelf progress: " + lastServerProgress :
            "No authenticated AudioShelf status received before return. Last network step: " + transportStage);
        record("Eight-second wake window elapsed. Playback is not confirmed here; AudioShelf continues checking.");
        message.setText(keepOpen ? "Wake window finished. Diagnostic hold keeps this log open." :
            "Returning to AudioShelf to check playback…");
        if (keepOpen) record("Diagnostic hold enabled; status monitoring continues until the job finishes.");
        returnWhenVisible();
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        diagnosticStartedAt = SystemClock.elapsedRealtime();
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER);
        int padding = (int) (24 * getResources().getDisplayMetrics().density);
        layout.setPadding(padding, padding, padding, padding);
        message = new TextView(this);
        message.setTextSize(20);
        message.setGravity(Gravity.CENTER);
        layout.addView(message);
        Button back = new Button(this);
        back.setText("Return to AudioShelf");
        back.setOnClickListener(v -> {
            if (returnUri != null) { completed = false; returnToAudioShelf(); }
            else finishHelper();
        });
        layout.addView(back);
        playbackAccess = new Button(this);
        playbackAccess.setText("Enable Spotify playback detection");
        playbackAccess.setOnClickListener(v -> new AlertDialog.Builder(this)
            .setTitle("Detect Spotify playing on this phone")
            .setMessage("Enable notification access for AudioShelf Spotify Helper in Android settings. The helper uses it only to read Spotify's media-session playback state while waking Spotify. It does not read or save notification text.")
            .setPositiveButton("Open Android settings", (dialog, which) -> startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)))
            .setNegativeButton("Cancel", null).show());
        layout.addView(playbackAccess);
        keepOpen = getPreferences(MODE_PRIVATE).getBoolean("keep_open_diagnostics", false);
        CheckBox hold = new CheckBox(this);
        hold.setText("Keep open for diagnostics (return manually)");
        hold.setChecked(keepOpen);
        hold.setOnCheckedChangeListener((button, checked) -> {
            keepOpen = checked;
            getPreferences(MODE_PRIVATE).edit().putBoolean("keep_open_diagnostics", checked).apply();
            if (wakeRequest) record(checked ? "Diagnostic hold enabled. Automatic return paused." : "Diagnostic hold disabled.");
            if (!checked) returnWhenVisible();
        });
        layout.addView(hold);
        Button copy = new Button(this);
        copy.setText("Copy log");
        copy.setOnClickListener(v -> {
            ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            clipboard.setPrimaryClip(ClipData.newPlainText("AudioShelf helper diagnostics", diagnosticLog.toString()));
            Toast.makeText(this, "Log copied", Toast.LENGTH_SHORT).show();
        });
        layout.addView(copy);
        diagnostics = new TextView(this);
        diagnostics.setTextSize(13);
        diagnostics.setTextIsSelectable(true);
        diagnostics.setTypeface(android.graphics.Typeface.MONOSPACE);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(diagnostics);
        layout.addView(scroll, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(layout);
        Uri data = getIntent().getData();
        if (data == null) {
            diagnosticLog.append(getPreferences(MODE_PRIVATE).getString("last_wake_log", "No wake attempt recorded yet."));
            diagnostics.setText(diagnosticLog.toString());
            TextView updateStatus = new TextView(this);
            updateStatus.setGravity(Gravity.CENTER);
            updateStatus.setPadding(0, padding, 0, padding);
            layout.addView(updateStatus);
            updater = new HelperUpdater(this, updateStatus);
            Button update = new Button(this);
            update.setText("Check for updates");
            update.setOnClickListener(v -> updater.check());
            layout.addView(update);
            checkUpdatesWhenVisible = true;
            message.setText("AudioShelf helper " + BuildConfig.VERSION_NAME + "\nPress Play in AudioShelf to wake Spotify.\nLast wake log below.");
            return;
        }
        wakeRequest = true;
        record("AudioShelf helper " + BuildConfig.VERSION_NAME + " (" + BuildConfig.VERSION_CODE + ")");
        record("Wake request received. " + (keepOpen ? "Diagnostic hold enabled." : "Automatic return enabled."));
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
        record("Background-only wake. Return on AudioShelf result, otherwise after eight seconds.");
        playbackMonitor = new SpotifyPlaybackMonitor(this);
        record(SpotifyPlaybackMonitor.enabled(this) ? "Local Spotify playback detection enabled." :
            "Optional local playback detection disabled. Using AudioShelf server feedback.");
        // Establish a baseline before waking Spotify so existing playback is ignored.
        readLocalPlayback();
        connect(clientId);
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
        record("AudioShelf status request " + attempt + " started (immediate response requested).");
        statusExecutor.execute(() -> {
            PlaybackStatusClient.Result response = null;
            IOException failure = null;
            try {
                response = PlaybackStatusClient.poll(origin, accountId, jobId, helperToken, stage -> handler.post(() -> {
                    if (completed || isFinishing() || isDestroyed()) return;
                    transportStage = stage;
                    record("AudioShelf request " + attempt + ": " + stage + ".");
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
                        record("AudioShelf connection unavailable: " + reason + ". Retrying until the wake window ends.");
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
                if (!progress.equals(lastServerProgress)) {
                    lastServerProgress = progress;
                    record("AudioShelf progress: " + progress + ".");
                }
                if (!result.state.equals(lastServerState)) {
                    lastServerState = result.state;
                    record("AudioShelf playback: " + result.state + ".");
                }
                if (result.state.equals("waiting")) {
                    message.setText(sdkFailed ? "Spotify SDK session failed, but background waking may still work. Waiting for AudioShelf…" :
                        "Waiting for AudioShelf to confirm playback on your phone…");
                    handler.postDelayed(pollStatus, 1000);
                    return;
                }
                wakeReturn.onServerState(result.state);
                handler.removeCallbacks(returnDeadline);
                record("AudioShelf job finished: " + result.state + ". Returning before the wake deadline if possible.");
                message.setText(result.state.equals("started") ? "Playback confirmed. Returning to AudioShelf…" :
                    "AudioShelf reports: " + result.message + "\nReturning to show the result…");
                if (keepOpen) record("Diagnostic hold enabled; waiting for manual return.");
                returnWhenVisible();
            });
        });
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
            .setRedirectUri("audioshelf-helper://spotify-callback")
            .showAuthView(true).build();
        SpotifyAppRemote.connect(this, params, new Connector.ConnectionListener() {
            @Override public void onConnected(SpotifyAppRemote appRemote) {
                handler.post(() -> {
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
                    // SDK connection is diagnostic; job status governs return.
                });
            }
            @Override public void onFailure(Throwable error) {
                Log.w("AudioShelfHelper", "Spotify SDK connection failed: " + WakeDiagnostics.failure(error));
                handler.post(() -> {
                    if (completed || isFinishing() || isDestroyed()) return;
                    sdkFailed = true;
                    record("Spotify SDK failed: " + WakeDiagnostics.failure(error));
                    record("SDK session failure does not confirm wake failure. Keeping Spotify in the background.");
                    message.setText("Spotify SDK session failed. Background waking may still work; waiting for AudioShelf or the return deadline.");
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
    @Override protected void onResume() {
        super.onResume();
        resumed = true;
        if (playbackAccess != null) playbackAccess.setText(SpotifyPlaybackMonitor.enabled(this) ?
            "Spotify playback detection enabled" : "Enable Spotify playback detection");
        if (wakeRequest) record("Helper visible.");
        returnWhenVisible();
    }
    @Override protected void onPause() { resumed = false; if (wakeRequest) record("Helper left foreground."); super.onPause(); }
    @Override public void onWindowFocusChanged(boolean focused) {
        super.onWindowFocusChanged(focused);
        if (focused && updater != null) {
            if (checkUpdatesWhenVisible) { checkUpdatesWhenVisible = false; updater.check(); }
            updater.onVisible();
        }
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (updater != null) updater.activityResult(request);
    }
    @Override protected void onDestroy() {
        if (updater != null) updater.close();
        completed = true;
        handler.removeCallbacksAndMessages(null);
        if (remote != null) SpotifyAppRemote.disconnect(remote);
        statusExecutor.shutdownNow();
        super.onDestroy();
    }
    private void record(String event) {
        if (!wakeRequest || diagnostics == null) return;
        diagnosticLog.append(WakeDiagnostics.line(SystemClock.elapsedRealtime() - diagnosticStartedAt, event)).append('\n');
        if (diagnosticLog.length() > 12000) {
            int cut = diagnosticLog.indexOf("\n", diagnosticLog.length() - 10000);
            diagnosticLog.delete(0, cut >= 0 ? cut + 1 : diagnosticLog.length() - 10000);
        }
        diagnostics.setText(diagnosticLog.toString());
        getPreferences(MODE_PRIVATE).edit().putString("last_wake_log", diagnosticLog.toString()).apply();
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
