package uk.co.justcop.audioshelf.helper;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import java.util.function.Consumer;

/**
 * Temporary opt-in permission for Spotify's bound service to launch its
 * authorisation screen on Android 14+. Never sends binder messages or Play.
 * Keep the binding until the SDK callback/timeout, not merely until bind success.
 */
final class SpotifyAuthGrant {
    private static final String SPOTIFY_PACKAGE = "com.spotify.music";
    private final Activity activity;
    private final Handler handler;
    private final Consumer<String> log;
    private ServiceConnection connection;
    private boolean bound, active, waiting;
    private Runnable deadline;

    SpotifyAuthGrant(Activity activity, Handler handler, Consumer<String> log) {
        this.activity = activity;
        this.handler = handler;
        this.log = log;
    }

    void start(Runnable onReady, Consumer<String> onFailure) {
        close();
        if (!SpotifyAuthorizationMode.needsActivityLaunchGrant(Build.VERSION.SDK_INT)) {
            log.accept("Android before API 34; using the original foreground pairing connection.");
            onReady.run();
            return;
        }
        if (!activity.hasWindowFocus() || activity.isFinishing() || activity.isDestroyed()) {
            onFailure.accept("Pairing must start while the helper is in the foreground");
            return;
        }

        ServiceInfo selected = null;
        try {
            PackageInfo installed = activity.getPackageManager().getPackageInfo(
                SPOTIFY_PACKAGE, PackageManager.GET_SERVICES);
            for (ServiceInfo service : installed.services == null ? new ServiceInfo[0] : installed.services) {
                if (!SpotifyServicePolicy.candidate(service.name)
                    || !service.exported || service.permission != null) continue;
                if (selected != null) {
                    onFailure.accept("Multiple Spotify App Remote services found");
                    return;
                }
                selected = service;
            }
        } catch (PackageManager.NameNotFoundException error) {
            onFailure.accept("Spotify package not visible");
            return;
        } catch (RuntimeException error) {
            onFailure.accept("Spotify service inspection failed: " + WakeDiagnostics.failure(error));
            return;
        }
        if (selected == null) {
            onFailure.accept("No accessible Spotify App Remote protocol service found");
            return;
        }

        String serviceName = selected.name;
        active = true;
        waiting = true;
        connection = new ServiceConnection() {
            @Override public void onServiceConnected(ComponentName name, IBinder binder) {
                if (!active || !waiting) return;
                waiting = false;
                if (deadline != null) handler.removeCallbacks(deadline);
                deadline = null;
                log.accept("Spotify protocol service connected. Temporary authorisation-screen grant active.");
                onReady.run();
            }
            @Override public void onServiceDisconnected(ComponentName name) {
                if (active) log.accept("Spotify protocol service disconnected during pairing.");
            }
            @Override public void onBindingDied(ComponentName name) {
                if (active && waiting) fail("Spotify service binding died before SDK connect", onFailure);
            }
            @Override public void onNullBinding(ComponentName name) {
                if (active && waiting) fail("Spotify service returned a null binder", onFailure);
            }
        };
        try {
            log.accept("Binding " + serviceName + " with BIND_ALLOW_ACTIVITY_STARTS for interactive authorisation.");
            bound = activity.bindService(new Intent().setComponent(
                new ComponentName(SPOTIFY_PACKAGE, serviceName)),
                connection, Context.BIND_AUTO_CREATE | Context.BIND_ALLOW_ACTIVITY_STARTS);
            log.accept("Android bindService accepted=" + bound + ".");
            if (!bound) {
                fail("Android rejected the grant-enabled service binding", onFailure);
                return;
            }
            if (waiting) {
                deadline = () -> {
                    if (active && waiting) fail("No Spotify binder callback after five seconds", onFailure);
                };
                handler.postDelayed(deadline, 5000);
            }
        } catch (RuntimeException error) {
            fail("Grant-enabled service binding failed: " + WakeDiagnostics.failure(error), onFailure);
        }
    }

    private void fail(String reason, Consumer<String> callback) {
        close();
        callback.accept(reason);
    }

    void close() {
        if (deadline != null) handler.removeCallbacks(deadline);
        deadline = null;
        active = false;
        waiting = false;
        if (bound && connection != null) {
            try { activity.unbindService(connection); }
            catch (RuntimeException error) {
                log.accept("Could not release Spotify pairing grant: " + WakeDiagnostics.failure(error));
            }
        }
        bound = false;
        connection = null;
    }
}
