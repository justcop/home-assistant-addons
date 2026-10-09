package uk.co.justcop.audioshelf.helper;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.ActivityNotFoundException;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import com.spotify.android.appremote.api.ConnectionParams;
import com.spotify.android.appremote.api.Connector;
import com.spotify.android.appremote.api.SpotifyAppRemote;

/** Wakes the local Spotify service. Never selects a device or sends playback commands. */
public final class MainActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private SpotifyAppRemote remote;
    private HelperUpdater updater;
    private boolean checkUpdatesWhenVisible;
    private TextView message;
    private boolean completed, connecting, resumed, returnRequested;
    private long wakeStartedAt;
    private Uri returnUri;
    private final Runnable timeout = this::scheduleReturn;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
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
        setContentView(layout);
        Uri data = getIntent().getData();
        if (data == null) {
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
            message.setText("Open this helper from AudioShelf's waiting-for-device dialog.\n\nEnable the Android Spotify helper in AudioShelf Settings after configuring the Spotify developer app. Setup instructions are in the Android helper README.");
            return;
        }
        String clientId = data.getQueryParameter("client_id");
        String origin = data.getQueryParameter("origin");
        String returnUrl = data.getQueryParameter("return_url");
        if (!"audioshelf-helper".equals(data.getScheme()) || !"wake".equals(data.getHost())
                || clientId == null || !clientId.matches("[a-fA-F0-9]{32}") || !validOrigin(origin)
                || !validReturnUrl(returnUrl, origin)) {
            fail("Invalid AudioShelf helper request.");
            return;
        }
        returnUri = Uri.parse(returnUrl == null ? origin : returnUrl);
        String trusted = getPreferences(MODE_PRIVATE).getString("trusted_origin", "");
        String trustedClient = getPreferences(MODE_PRIVATE).getString("trusted_client", "");
        if (origin.equals(trusted) && clientId.equals(trustedClient)) {
            connect(clientId);
        } else {
            message.setText("Confirm your AudioShelf server.");
            new AlertDialog.Builder(this)
                .setTitle("Allow AudioShelf to wake Spotify?")
                .setMessage(origin + "\n\nThis helper wakes Spotify in the background, then returns to this AudioShelf server. AudioShelf controls playback on your saved device.")
                .setPositiveButton("Allow", (dialog, which) -> {
                    getPreferences(MODE_PRIVATE).edit().putString("trusted_origin", origin)
                        .putString("trusted_client", clientId).apply();
                    connect(clientId);
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

    private void connect(String clientId) {
        if (completed || connecting) return;
        if (!SpotifyAppRemote.isSpotifyInstalled(this)) {
            fail("Install Spotify and log in on this phone first.");
            return;
        }
        connecting = true;
        wakeStartedAt = SystemClock.elapsedRealtime();
        message.setText("Waking Spotify…\nPlease wait here. AudioShelf will keep your selected device and tracklist.");
        // Restore the original SDK service wake, with no Spotify launcher intent.
        // Keep the helper alive while the service/device settles, including when
        // the SDK session fails after successfully waking the Spotify process.
        handler.postDelayed(timeout, 45000);
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
                    scheduleReturn();
                });
            }
            @Override public void onFailure(Throwable error) {
                // A failed SDK session can still wake Spotify. Allow the full
                // warm-up in the helper before AudioShelf checks device readiness.
                Log.w("AudioShelfHelper", "Spotify SDK connection failed: " + error.getClass().getSimpleName());
                handler.post(() -> scheduleReturn());
            }
        });
    }

    private void scheduleReturn() {
        if (completed || isFinishing() || isDestroyed()) return;
        handler.removeCallbacks(timeout);
        long remaining = Math.max(0, 5000 - (SystemClock.elapsedRealtime() - wakeStartedAt));
        handler.postDelayed(() -> {
            if (completed || isFinishing() || isDestroyed()) return;
            returnRequested = true;
            message.setText("Returning to AudioShelf to check Spotify…");
            returnWhenVisible();
        }, remaining);
    }

    private void returnWhenVisible() {
        // Do not steal focus from Spotify's first-use authorisation screen. Normal
        // background waking leaves this helper visible throughout the wait.
        if (resumed && returnRequested && !completed) returnToAudioShelf();
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
        message.setText(reason);
    }
    private void finishHelper() {
        completed = true;
        handler.removeCallbacksAndMessages(null);
        if (remote != null) SpotifyAppRemote.disconnect(remote);
        remote = null;
        finishAndRemoveTask();
    }
    @Override protected void onResume() { super.onResume(); resumed = true; returnWhenVisible(); }
    @Override protected void onPause() { resumed = false; super.onPause(); }
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
        super.onDestroy();
    }
    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        // Ignore duplicate launches while a connection is pending. Retry uses a fresh task.
    }
}
