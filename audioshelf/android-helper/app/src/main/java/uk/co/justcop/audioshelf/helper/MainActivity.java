package uk.co.justcop.audioshelf.helper;

import android.app.Activity;
import android.app.ActivityManager;
import android.app.ActivityOptions;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.ActivityNotFoundException;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.os.Build;
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
    private boolean completed, connecting;
    private Uri returnUri;
    private final Runnable timeout = this::returnToAudioShelf;

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
                .setMessage(origin + "\n\nThis helper opens Spotify, then returns to this AudioShelf server. AudioShelf controls playback on your saved device.")
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
        Intent spotify = getPackageManager().getLaunchIntentForPackage("com.spotify.music");
        if (spotify == null) {
            fail("Install Spotify and log in on this phone first.");
            return;
        }
        connecting = true;
        message.setText("Waking Spotify…\nPlease wait here. AudioShelf will keep your selected device and tracklist.");
        ActivityManager.AppTask helperTask = currentHelperTask();
        try {
            // Keep the explicit wake independently of SDK authentication. Android's
            // launch-behind option only works with compatible target launch modes.
            if (supportsLaunchBehind(spotify)) {
                spotify.addFlags(Intent.FLAG_ACTIVITY_NEW_DOCUMENT);
                startActivity(spotify, ActivityOptions.makeTaskLaunchBehind().toBundle());
            } else {
                startActivity(spotify);
            }
            // Restore only our own existing task, never launch a second helper or
            // reach into Spotify's task. This also handles targets that ignore the
            // launch-behind request. The five-second warm-up still runs in full.
            handler.post(() -> restoreHelper(helperTask));
        } catch (ActivityNotFoundException | SecurityException error) {
            fail("Spotify could not open. Open Spotify manually and return to AudioShelf.");
            return;
        }
        // Give Spotify time to register its Connect device while the helper waits. SDK failure
        // must not shorten this interval. AudioShelf's server checks actual readiness.
        handler.postDelayed(timeout, 5000);
        ConnectionParams params = new ConnectionParams.Builder(clientId)
            .setRedirectUri("audioshelf-helper://spotify-callback")
            .showAuthView(false).build();
        SpotifyAppRemote.connect(this, params, new Connector.ConnectionListener() {
            @Override public void onConnected(SpotifyAppRemote appRemote) {
                handler.post(() -> {
                    if (completed || isFinishing() || isDestroyed()) {
                        SpotifyAppRemote.disconnect(appRemote);
                        return;
                    }
                    remote = appRemote;
                });
            }
            @Override public void onFailure(Throwable error) {
                // The explicit launch and scheduled return remain active.
                Log.w("AudioShelfHelper", "Spotify SDK connection failed: " + error.getClass().getSimpleName());
            }
        });
    }

    private boolean supportsLaunchBehind(Intent spotify) {
        try {
            ActivityInfo info = getPackageManager().getActivityInfo(spotify.getComponent(), 0);
            return (info.launchMode == ActivityInfo.LAUNCH_MULTIPLE
                || info.launchMode == ActivityInfo.LAUNCH_SINGLE_TOP)
                && info.documentLaunchMode != ActivityInfo.DOCUMENT_LAUNCH_NEVER;
        } catch (PackageManager.NameNotFoundException error) {
            return false;
        }
    }

    private ActivityManager.AppTask currentHelperTask() {
        ActivityManager manager = (ActivityManager) getSystemService(ACTIVITY_SERVICE);
        if (manager == null) return null;
        for (ActivityManager.AppTask task : manager.getAppTasks()) {
            ActivityManager.RecentTaskInfo info = task.getTaskInfo();
            int id = Build.VERSION.SDK_INT >= 29 ? info.taskId : info.id;
            if (id == getTaskId()) return task;
        }
        return null;
    }

    private void restoreHelper(ActivityManager.AppTask task) {
        if (completed || isFinishing() || isDestroyed() || task == null) return;
        try {
            task.moveToFront();
        } catch (IllegalArgumentException | SecurityException error) {
            // Some phones restrict task movement. Keep the proven automatic return
            // rather than abandoning the handoff or keeping the user in Spotify.
            Log.w("AudioShelfHelper", "Helper focus could not be restored: " + error.getClass().getSimpleName());
        }
    }

    private void returnToAudioShelf() {
        if (completed || isFinishing() || isDestroyed()) return;
        Intent back = new Intent(Intent.ACTION_VIEW, returnUri);
        back.addCategory(Intent.CATEGORY_BROWSABLE);
        back.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            // Resolve the trusted HTTPS URL normally, including an installed PWA.
            // Returning shortly after our explicit launch is deliberate: waiting
            // for onResume would trap the user in Spotify until they press Back.
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
