package uk.co.justcop.audioshelf.helper;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import com.spotify.android.appremote.api.ConnectionParams;
import com.spotify.android.appremote.api.Connector;
import com.spotify.android.appremote.api.SpotifyAppRemote;
import java.util.ArrayList;
import java.util.List;

/**
 * Isolated foreground reproduction of the Spotify SDK sample connect pattern.
 * This Activity never uses AudioShelf playback jobs or issues a Play command.
 */
public final class SpotifyDiagnosticActivity extends Activity {
    private static final String SPOTIFY = "com.spotify.music";
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final WakeLog log = new WakeLog();
    private TextView output, status;
    private Button sdkButton, grantButton;
    private boolean grantWaiting, grantActive;
    private long started;
    private int attempt;
    private boolean pending, closed, bound;
    private SpotifyAppRemote remote;
    private ServiceConnection binder;
    private Runnable bindDeadline;

    private void event(String text) {
        String line = WakeDiagnostics.line(SystemClock.elapsedRealtime() - started, text);
        log.append(line);
        Log.i("AudioShelfProbe", line);
        if (output != null) output.setText(log.toString());
    }

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        started = SystemClock.elapsedRealtime();
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int p = (int) (20 * getResources().getDisplayMetrics().density);
        layout.setPadding(p, p, p, p);
        status = new TextView(this);
        status.setTextSize(18);
        status.setText("Spotify SDK diagnostics. No playback commands.");
        layout.addView(status);

        Button binderButton = new Button(this);
        binderButton.setText("Inspect Spotify services and test binding");
        binderButton.setOnClickListener(v -> inspectAndBind());
        layout.addView(binderButton);

        sdkButton = new Button(this);
        sdkButton.setText("Run isolated SDK sample-style connection");
        sdkButton.setOnClickListener(v -> connect());
        layout.addView(sdkButton);

        grantButton = new Button(this);
        grantButton.setText("Test SDK with Android 14+ authorisation-screen grant");
        grantButton.setOnClickListener(v -> connectWithActivityGrant());
        layout.addView(grantButton);

        Button copy = new Button(this);
        copy.setText("Copy SDK diagnostic log");
        copy.setOnClickListener(v -> {
            ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            clipboard.setPrimaryClip(ClipData.newPlainText("AudioShelf SDK diagnostic", output.getText()));
            Toast.makeText(this, "Diagnostic log copied", Toast.LENGTH_SHORT).show();
        });
        layout.addView(copy);

        Button back = new Button(this);
        back.setText("Back to helper");
        back.setOnClickListener(v -> finish());
        layout.addView(back);

        output = new TextView(this);
        output.setTextSize(12);
        output.setTypeface(android.graphics.Typeface.MONOSPACE);
        output.setTextIsSelectable(true);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(output);
        layout.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(layout);
        event("Isolated test Activity opened. Helper " + BuildConfig.VERSION_NAME
            + "; Android API " + Build.VERSION.SDK_INT + ".");
        event("SDK sample-style call; not the separate, unmodified official sample APK.");
        event("SDK internal verbose debug logs go to Android logcat, not this sanitised copyable log.");
    }

    private void inspectAndBind() {
        if (pending || grantWaiting) {
            event("Service probe unavailable while SDK test is active.");
            return;
        }
        releaseBinding();
        PackageInfo info;
        try {
            info = getPackageManager().getPackageInfo(SPOTIFY, PackageManager.GET_SERVICES);
        } catch (PackageManager.NameNotFoundException error) {
            event("Spotify package not discoverable by Android.");
            return;
        } catch (RuntimeException error) {
            event("Cannot inspect Spotify services: " + WakeDiagnostics.failure(error));
            return;
        }
        ServiceInfo[] services = info.services;
        event("Spotify package visible; version " + info.versionName
            + "; declared service count " + (services == null ? 0 : services.length) + ".");
        List<ServiceInfo> candidates = new ArrayList<>();
        if (services != null) for (ServiceInfo s : services) {
            if (!SpotifyServicePolicy.candidate(s.name)) continue;
            event("Candidate " + s.name + "; exported=" + s.exported
                + "; permission required=" + (s.permission != null) + ".");
            if (s.exported && s.permission == null) candidates.add(s);
        }
        if (candidates.size() != 1) {
            event("Unprotected, exported Spotify App Remote protocol candidates=" + candidates.size()
                + ". No unambiguous direct bind target. This does not prove SDK binding is broken.");
            return;
        }
        String name = candidates.get(0).name;
        event("Identified Spotify App Remote protocol service: " + name
            + ". Testing Android reachability only, not authorisation.");
        binder = new ServiceConnection() {
            @Override public void onServiceConnected(ComponentName target, IBinder service) {
                event("onServiceConnected: binder reachable for " + name
                    + ". This does not establish App Remote authorisation.");
            }
            @Override public void onServiceDisconnected(ComponentName target) {
                event("onServiceDisconnected for " + name + ".");
            }
            @Override public void onBindingDied(ComponentName target) {
                event("onBindingDied for " + name + ".");
            }
            @Override public void onNullBinding(ComponentName target) {
                event("onNullBinding for " + name + ".");
            }
        };
        try {
            bound = bindService(new Intent().setComponent(new ComponentName(SPOTIFY, name)),
                binder, Context.BIND_AUTO_CREATE);
            event("Android bindService returned " + bound
                + ". True is only an accepted request; waiting up to four seconds for callback.");
            if (bound) {
                bindDeadline = () -> {
                    event("Four-second bind observation finished; unbinding.");
                    releaseBinding();
                };
                handler.postDelayed(bindDeadline, 4000);
            }
        } catch (RuntimeException error) {
            event("Android bind rejected: " + WakeDiagnostics.failure(error));
            releaseBinding();
        }
    }

    private void releaseBinding() {
        if (bindDeadline != null) handler.removeCallbacks(bindDeadline);
        bindDeadline = null;
        if (bound && binder != null) {
            try { unbindService(binder); }
            catch (RuntimeException error) {
                event("Error unbinding probe: " + WakeDiagnostics.failure(error));
            }
        }
        bound = false;
        binder = null;
    }

    /**
     * Android 14+ normally prevents a service bound from a foreground app from
     * launching its own auth Activity unless the client explicitly allows it.
     * This is an opt-in experiment: bind the *already identified Spotify service*
     * with BIND_ALLOW_ACTIVITY_STARTS, then invoke the unchanged SDK connection.
     */
    private void connectWithActivityGrant() {
        if (pending || grantWaiting || closed) return;
        if (Build.VERSION.SDK_INT < 34) {
            event("Activity launch grant requires Android API 34 or later.");
            return;
        }
        if (!SpotifyAppRemote.isSpotifyInstalled(this)) {
            event("Cannot grant: Spotify SDK reports Spotify not installed.");
            return;
        }
        String trusted = getSharedPreferences(MainActivity.class.getSimpleName(), MODE_PRIVATE)
            .getString("trusted_client", "");
        if (!SpotifyRemoteAuthPolicy.validClientId(trusted)) {
            event("Cannot grant: missing trusted client ID. Trigger Play from AudioShelf first.");
            return;
        }
        releaseBinding();
        ServiceInfo target = null;
        try {
            PackageInfo info = getPackageManager().getPackageInfo(
                SPOTIFY, PackageManager.GET_SERVICES);
            for (ServiceInfo service : info.services == null ? new ServiceInfo[0] : info.services) {
                if (!SpotifyServicePolicy.candidate(service.name) || !service.exported
                    || service.permission != null) continue;
                if (target != null) {
                    event("Multiple candidate services. Grant test blocked for safety.");
                    return;
                }
                target = service;
            }
        } catch (PackageManager.NameNotFoundException error) {
            event("Spotify package not discoverable. Grant test blocked.");
            return;
        } catch (RuntimeException error) {
            event("Could not inspect grant target: " + WakeDiagnostics.failure(error));
            return;
        }
        if (target == null) {
            event("No suitable exported Spotify protocol service. Grant test blocked.");
            return;
        }
        String name = target.name;
        grantWaiting = true;
        grantActive = true;
        sdkButton.setEnabled(false);
        grantButton.setEnabled(false);
        status.setText("Granting Spotify temporary authorisation-screen launch permission…");
        event("Opt-in Android 14+ test: binding " + name
            + " with BIND_ALLOW_ACTIVITY_STARTS. Binding held only during this test.");
        binder = new ServiceConnection() {
            @Override public void onServiceConnected(ComponentName component, IBinder service) {
                if (!grantWaiting || closed) return;
                grantWaiting = false;
                event("Permission-grant bind onServiceConnected. Invoking identical App Remote SDK connect now.");
                connect();
            }
            @Override public void onServiceDisconnected(ComponentName component) {
                event("Grant-test service disconnected.");
            }
            @Override public void onBindingDied(ComponentName component) {
                event("Grant-test service binding died.");
                if (grantWaiting) abortGrant("Grant-test service binding died before SDK started.");
            }
            @Override public void onNullBinding(ComponentName component) {
                event("Grant-test service returned a null binder.");
                if (grantWaiting) abortGrant("Grant-test service returned a null binder.");
            }
        };
        try {
            bound = bindService(new Intent().setComponent(new ComponentName(SPOTIFY, name)),
                binder, Context.BIND_AUTO_CREATE | Context.BIND_ALLOW_ACTIVITY_STARTS);
            event("Android bindService with activity-start grant returned " + bound + ".");
            if (!bound) {
                abortGrant("Android rejected activity-grant service binding.");
                return;
            }
            // Do not invoke SDK until the grant-enabled binder actually connects.
            handler.postDelayed(() -> {
                if (grantWaiting && !closed) {
                    abortGrant("Five seconds without grant-enabled onServiceConnected; no SDK test started.");
                }
            }, 5000);
        } catch (RuntimeException error) {
            abortGrant("Grant-enabled binding threw: " + WakeDiagnostics.failure(error));
        }
    }

    private void abortGrant(String reason) {
        event(reason);
        grantWaiting = false;
        grantActive = false;
        releaseBinding();
        sdkButton.setEnabled(true);
        grantButton.setEnabled(true);
        status.setText("Grant-enabled binding failed before SDK connection.");
    }

    private void finishSdkTest() {
        pending = false;
        sdkButton.setEnabled(true);
        grantButton.setEnabled(true);
        SpotifyAppRemote.setDebugMode(false);
        if (grantActive) {
            event("Releasing temporary Android authorisation-screen launch grant.");
            grantActive = false;
            releaseBinding();
        }
    }

    private void connect() {
        if (pending || grantWaiting || closed) return;
        if (!grantActive) releaseBinding();
        String clientId = getSharedPreferences(MainActivity.class.getSimpleName(), MODE_PRIVATE)
            .getString("trusted_client", "");
        if (!SpotifyRemoteAuthPolicy.validClientId(clientId)) {
            event("No trusted Spotify client ID. Trigger Play from AudioShelf once first.");
            status.setText("No trusted client ID.");
            return;
        }
        if (!SpotifyAppRemote.isSpotifyInstalled(this)) {
            event("SDK reports Spotify not installed/visible.");
            status.setText("Spotify not installed.");
            return;
        }
        pending = true;
        sdkButton.setEnabled(false);
        grantButton.setEnabled(false);
        int current = ++attempt;
        status.setText("Waiting up to 30 seconds for Spotify App Remote callback…");
        event("Isolated SDK test started. Spotify installed; client ID validated; "
            + "registered redirect configured; showAuthView=true; no Play.");
        event(grantActive
            ? "Mode: temporary Android BIND_ALLOW_ACTIVITY_STARTS grant held during SDK connect."
            : "Mode: ordinary SDK connection, no Android activity-start grant.");
        event("Leave this diagnostic screen open for 30 seconds to capture an SDK callback or the local timeout.");
        try {
            SpotifyAppRemote.setDebugMode(true);
            event("Verbose Spotify SDK debugging enabled in Android logcat.");
            ConnectionParams params = new ConnectionParams.Builder(clientId)
                .setRedirectUri(SpotifyRemoteAuthPolicy.REDIRECT_URI)
                .showAuthView(true).build();
            SpotifyAppRemote.connect(this, params, new Connector.ConnectionListener() {
                @Override public void onConnected(SpotifyAppRemote appRemote) {
                    handler.post(() -> {
                        if (closed || !pending || current != attempt) {
                            SpotifyAppRemote.disconnect(appRemote);
                            return;
                        }
                        remote = appRemote;
                        finishSdkTest();
                        event("SDK onConnected callback! Isolated connection succeeded.");
                        status.setText("App Remote authorised and connected.");
                        disconnect();
                    });
                }
                @Override public void onFailure(Throwable error) {
                    handler.post(() -> {
                        if (closed || !pending || current != attempt) return;
                        event("SDK onFailure: " + WakeDiagnostics.failure(error));
                        finishSdkTest();
                        status.setText("SDK returned a connection failure.");
                    });
                }
            });
        } catch (RuntimeException error) {
            event("SDK connect call threw: " + WakeDiagnostics.failure(error));
            finishSdkTest();
            status.setText("SDK exception.");
            return;
        }
        handler.postDelayed(() -> {
            if (!closed && pending && current == attempt)
                event("10 seconds without SDK success/failure callback.");
        }, 10000);
        handler.postDelayed(() -> {
            if (!closed && pending && current == attempt) {
                event("30-second local watchdog expired with no SDK callback; not a Spotify response.");
                finishSdkTest();
                status.setText("No SDK callback. Compare binding and lifecycle diagnostics.");
            }
        }, 30000);
    }

    private void disconnect() {
        if (remote != null) SpotifyAppRemote.disconnect(remote);
        remote = null;
    }

    @Override protected void onStart() { super.onStart(); event("Activity onStart."); }
    @Override protected void onResume() { super.onResume(); event("Activity onResume."); }
    @Override protected void onPause() { event("Activity onPause. Authorisation screen may be opening."); super.onPause(); }
    @Override protected void onStop() { event("Activity onStop."); super.onStop(); }
    @Override public void onWindowFocusChanged(boolean focus) {
        super.onWindowFocusChanged(focus);
        event("Window focus=" + focus + ".");
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        event("Activity result request=" + request + ", result=" + result + "; payload not recorded.");
    }
    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        event("onNewIntent received; payload not recorded.");
    }
    @Override protected void onDestroy() {
        closed = true;
        attempt++;
        pending = false;
        grantWaiting = false;
        grantActive = false;
        handler.removeCallbacksAndMessages(null);
        releaseBinding();
        disconnect();
        SpotifyAppRemote.setDebugMode(false);
        super.onDestroy();
    }
}
