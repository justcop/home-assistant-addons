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
    private Button sdkButton;
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

    private void connect() {
        if (pending || closed) return;
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
        int current = ++attempt;
        status.setText("Waiting up to 30 seconds for Spotify App Remote callback…");
        event("Isolated SDK test started. Spotify installed; client ID validated; "
            + "registered redirect configured; showAuthView=true; no Play.");
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
                        pending = false;
                        sdkButton.setEnabled(true);
                        remote = appRemote;
                        event("SDK onConnected callback! Isolated connection succeeded.");
                        status.setText("App Remote authorised and connected.");
                        disconnect();
                        SpotifyAppRemote.setDebugMode(false);
                    });
                }
                @Override public void onFailure(Throwable error) {
                    handler.post(() -> {
                        if (closed || !pending || current != attempt) return;
                        pending = false;
                        sdkButton.setEnabled(true);
                        event("SDK onFailure: " + WakeDiagnostics.failure(error));
                        status.setText("SDK returned a connection failure.");
                        SpotifyAppRemote.setDebugMode(false);
                    });
                }
            });
        } catch (RuntimeException error) {
            pending = false;
            sdkButton.setEnabled(true);
            event("SDK connect call threw: " + WakeDiagnostics.failure(error));
            status.setText("SDK exception.");
            SpotifyAppRemote.setDebugMode(false);
            return;
        }
        handler.postDelayed(() -> {
            if (!closed && pending && current == attempt)
                event("10 seconds without SDK success/failure callback.");
        }, 10000);
        handler.postDelayed(() -> {
            if (!closed && pending && current == attempt) {
                pending = false;
                sdkButton.setEnabled(true);
                event("30-second local watchdog expired with no SDK callback; not a Spotify response.");
                status.setText("No SDK callback. Compare binding and lifecycle diagnostics.");
                SpotifyAppRemote.setDebugMode(false);
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
        handler.removeCallbacksAndMessages(null);
        releaseBinding();
        disconnect();
        SpotifyAppRemote.setDebugMode(false);
        super.onDestroy();
    }
}
