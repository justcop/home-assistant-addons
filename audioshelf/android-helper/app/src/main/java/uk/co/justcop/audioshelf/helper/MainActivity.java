package uk.co.justcop.audioshelf.helper;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
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
    private TextView message;
    private boolean resumed, returnRequested, completed, connecting;
    private final Runnable timeout = () -> returnToAudioShelf("Returning to AudioShelf to check whether Spotify is ready…");

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
        back.setOnClickListener(v -> finishHelper());
        layout.addView(back);
        setContentView(layout);
        Uri data = getIntent().getData();
        if (data == null) {
            message.setText("Open this helper from AudioShelf's waiting-for-device dialog.\n\nEnable the Android Spotify helper in AudioShelf Settings after configuring the Spotify developer app. Setup instructions are in the Android helper README.");
            return;
        }
        String clientId = data.getQueryParameter("client_id");
        String origin = data.getQueryParameter("origin");
        if (!"audioshelf-helper".equals(data.getScheme()) || !"wake".equals(data.getHost())
                || clientId == null || !clientId.matches("[a-fA-F0-9]{32}") || !validOrigin(origin)) {
            fail("Invalid AudioShelf helper request.");
            return;
        }
        String trusted = getPreferences(MODE_PRIVATE).getString("trusted_origin", "");
        String trustedClient = getPreferences(MODE_PRIVATE).getString("trusted_client", "");
        if (origin.equals(trusted) && clientId.equals(trustedClient)) {
            connect(clientId);
        } else {
            message.setText("Confirm your AudioShelf server.");
            new AlertDialog.Builder(this)
                .setTitle("Allow AudioShelf to wake Spotify?")
                .setMessage(origin + "\n\nThis helper only connects to Spotify. AudioShelf controls playback on your saved device.")
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

    private void connect(String clientId) {
        if (completed || connecting) return;
        if (!SpotifyAppRemote.isSpotifyInstalled(this)) {
            fail("Install Spotify and log in on this phone first.");
            return;
        }
        connecting = true;
        message.setText("Waking Spotify…\nAudioShelf will keep your selected device and tracklist.");
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
                    returnToAudioShelf("Returning to AudioShelf…");
                });
            }
            @Override public void onFailure(Throwable error) {
                // Binding to Spotify can wake it even when the App Remote session
                // fails. The server handoff, not this SDK callback, decides whether
                // the preferred device is ready and whether playback has started.
                Log.w("AudioShelfHelper", "Spotify SDK connection failed: " + error.getClass().getSimpleName());
                handler.post(() -> returnToAudioShelf("Returning to AudioShelf to check Spotify…"));
            }
        });
    }

    // Do not launch another activity from the background. Finishing this helper reveals
    // the browser/PWA task which launched it, preserving its page and pending request.
    private void returnToAudioShelf(String text) {
        if (completed || isFinishing() || isDestroyed()) return;
        returnRequested = true;
        handler.removeCallbacks(timeout);
        message.setText(text);
        returnWhenVisible();
    }
    private void returnWhenVisible() {
        if (resumed && returnRequested && !completed) handler.postDelayed(() -> {
            if (resumed && returnRequested && !completed) finishHelper();
        }, 1200);
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
    @Override protected void onDestroy() {
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
