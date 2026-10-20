package uk.co.justcop.audioshelf.helper;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.PendingIntent;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.Settings;
import android.view.Gravity;
import android.view.MenuItem;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.PopupMenu;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.webkit.JavaScriptReplyProxy;
import androidx.webkit.WebMessageCompat;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;
import com.spotify.android.appremote.api.ConnectionParams;
import com.spotify.android.appremote.api.Connector;
import com.spotify.android.appremote.api.SpotifyAppRemote;
import java.io.IOException;
import java.util.Collections;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

/** Optional full AudioShelf app: remote web UI + local Spotify wake only.
 * The AudioShelf server remains the sole playback/tracklist authority. */
public final class ShelfActivity extends Activity {
    static final String ACTION_SHELF = "uk.co.justcop.audioshelf.SHELF";
    static final String ACTION_NOW_PLAYING = "uk.co.justcop.audioshelf.NOW_PLAYING";
    static final String PREFS = "MainActivity";
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private WebView web;
    private TextView options;
    private TextView updaterStatus;
    private HelperUpdater updater;
    private String trustedOrigin;
    private SpotifyAppRemote remote;
    private int sequence;
    private boolean wakeConnecting;
    private boolean loaded;
    private boolean scrollSafeHeaderReady;
    private FrameLayout shelfRoot;
    private int statusBarPx;
    private int bottomSafePx;
    private int leftSafePx, rightSafePx;
    private String jobId, accountId, token;
    private long wakeDeadline;
    private long integratedWakeStartedAt;
    private String integratedSessionId, lastWakePhase, lastWakeState, lastWakeError;
    private String wakeSdkState = "not started";
    private boolean lastAcceptedPlay;
    private int wakePollCount;
    private WakeLog integratedWakeLog = new WakeLog();
    private final WakeProgressLog integratedProgressLog = new WakeProgressLog();
    private final Runnable poll = this::pollWake;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        trustedOrigin = getSharedPreferences(PREFS, MODE_PRIVATE).getString("trusted_origin", "");
        if (!isTrustedOrigin(trustedOrigin)) {
            Toast.makeText(this, "Trust your AudioShelf server in companion mode first.", Toast.LENGTH_LONG).show();
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putBoolean("integrated_mode", false).apply();
            startActivity(new Intent(this, MainActivity.class));
            finish();
            return;
        }
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            Toast.makeText(this, "Update Android System WebView to use integrated AudioShelf.", Toast.LENGTH_LONG).show();
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putBoolean("integrated_mode", false).apply();
            startActivity(new Intent(this, MainActivity.class));
            finish();
            return;
        }

        // Load with native clearance, then put the measured status inset into
        // scrollable document space. After verifying the logo and Settings are
        // clear of system icons, let the WebView draw behind the status bar.
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        FrameLayout root = new FrameLayout(this);
        shelfRoot = root;
        root.setBackgroundColor(Color.rgb(245, 240, 230));
        ViewCompat.setOnApplyWindowInsetsListener(root, (view, windowInsets) -> {
            Insets safe = windowInsets.getInsets(
                WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            Insets keyboard = windowInsets.getInsets(WindowInsetsCompat.Type.ime());
            statusBarPx = safe.top;
            leftSafePx = safe.left;
            rightSafePx = safe.right;
            bottomSafePx = Math.max(safe.bottom, keyboard.bottom);
            updateNativeSafeArea();
            if (scrollSafeHeaderReady) applyScrollableHeader();
            return WindowInsetsCompat.CONSUMED;
        });
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(250,248,245));
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setDomStorageEnabled(true);
        web.getSettings().setCacheMode(WebSettings.LOAD_DEFAULT);
        web.getSettings().setAllowFileAccess(false);
        web.getSettings().setAllowContentAccess(false);
        web.getSettings().setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        web.getSettings().setSupportMultipleWindows(false);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                loaded = false;
                scrollSafeHeaderReady = false;
                updateNativeSafeArea();
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                if (sameOrigin(url)) return false;
                if (request.isForMainFrame()) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, url)); }
                    catch (ActivityNotFoundException ignored) { Toast.makeText(ShelfActivity.this, "No app can open this link.", Toast.LENGTH_SHORT).show(); }
                }
                return true;
            }
            @Override public void onPageFinished(WebView view, String url) {
                loaded = sameOrigin(Uri.parse(url));
                applyScrollableHeader();
                if (loaded && ACTION_NOW_PLAYING.equals(getIntent().getAction()))
                    view.evaluateJavascript("window.dispatchEvent(new CustomEvent(\'audioshelf-native-playback\'));", null);
            }
        });
        WebViewCompat.addWebMessageListener(web, "AudioShelfAndroid", Collections.singleton(trustedOrigin),
            (view, message, sourceOrigin, isMainFrame, replyProxy) -> {
                if (!isMainFrame || !sameOrigin(sourceOrigin) || !sameOrigin(Uri.parse(view.getUrl() == null ? "" : view.getUrl()))) return;
                final String raw = message.getData();
                if (raw == null || raw.length() > 2048) return;
                try {
                    JSONObject cmd = new JSONObject(raw);
                    if ("wake".equals(cmd.optString("type"))) startWake(cmd);
                    else if ("settings".equals(cmd.optString("type"))) showNativeMenu();
                    else if ("media-permission".equals(cmd.optString("type"))) openMediaPermission();
                } catch (Exception ignored) { }
            });
        root.addView(web, new FrameLayout.LayoutParams(-1,-1));
        options = new TextView(this);
        options.setText("⋮");
        options.setTextSize(25);
        options.setTextColor(Color.rgb(116,47,56));
        options.setGravity(Gravity.CENTER);
        options.setContentDescription("AudioShelf Android options");
        options.setOnClickListener(v -> showNativeMenu());
        FrameLayout.LayoutParams button = new FrameLayout.LayoutParams(dp(34),dp(42),Gravity.TOP|Gravity.RIGHT);
        button.topMargin = dp(3);
        button.rightMargin = dp(4);
        root.addView(options,button);
        setContentView(root);
        WindowCompat.getInsetsController(getWindow(), root).setAppearanceLightStatusBars(true);
        WindowCompat.getInsetsController(getWindow(), root).setAppearanceLightNavigationBars(true);
        ViewCompat.requestApplyInsets(root);
        updaterStatus = new TextView(this);
        updaterStatus.setPadding(dp(20), dp(12), dp(20), dp(16));
        updaterStatus.setText("Check for Android updates");
        updater = new HelperUpdater(this, updaterStatus);
        SpotifyMediaListener.attach(this);
        ShelfShortcuts.install(this);
        web.loadUrl(trustedOrigin + (ACTION_NOW_PLAYING.equals(getIntent().getAction()) ? "/#shelf" : "/#shelf"));
    }

    static boolean isTrustedOrigin(String origin) {
        if (origin == null || origin.length() > 2048) return false;
        Uri uri=Uri.parse(origin);
        return "https".equals(uri.getScheme()) && uri.getHost()!=null && uri.getUserInfo()==null &&
            (uri.getPath()==null || uri.getPath().isEmpty()) && uri.getQuery()==null &&
            uri.getFragment()==null;
    }
    private boolean sameOrigin(Uri uri) {
        if (uri==null || !"https".equals(uri.getScheme()) || uri.getHost()==null) return false;
        Uri base=Uri.parse(trustedOrigin);
        return uri.getHost().equalsIgnoreCase(base.getHost()) && uri.getPort()==base.getPort() &&
            uri.getUserInfo()==null;
    }
    private int dp(int size) {return Math.round(getResources().getDisplayMetrics().density*size);}

    /**
     * The native status inset is a loading fallback, not permanent empty
     * space. After verifying a real scrollable spacer inside the page,
     * content can pass behind the status icons as the user scrolls.
     */
    private void updateNativeSafeArea() {
        if (shelfRoot == null) return;
        shelfRoot.setPadding(leftSafePx, scrollSafeHeaderReady ? 0 : statusBarPx,
            rightSafePx, bottomSafePx);
        if (options != null) {
            FrameLayout.LayoutParams params =
                (FrameLayout.LayoutParams) options.getLayoutParams();
            int top = dp(3) + (scrollSafeHeaderReady ? statusBarPx : 0);
            if (params.topMargin != top) {
                params.topMargin = top;
                options.setLayoutParams(params);
            }
        }
    }

    /**
     * The top spacer belongs to the scrollable document body. The masthead
     * is NOT sticky: logo, Settings and shelf can scroll underneath the
     * Android status icons. Measure BOTH controls before dropping the
     * temporary native inset; never trust a mere 'ready' response.
     */
    private void applyScrollableHeader() {
        if (!loaded || web == null ||
            !sameOrigin(Uri.parse(web.getUrl() == null ? "" : web.getUrl())))
            return;
        int cssTop = Math.max(0, (int) Math.ceil(
            statusBarPx / getResources().getDisplayMetrics().density));
        String script = "(function(){" +
            "var b=document.body,h=document.querySelector('.masthead');" +
            "if(!b||!h)return 'missing';" +
            "b.style.setProperty('padding-top','" + cssTop + "px','important');" +
            "h.style.setProperty('position','relative','important');" +
            "h.style.setProperty('top','auto','important');" +
            "h.style.setProperty('padding-top','8px','important');" +
            "h.style.setProperty('padding-bottom','12px','important');" +
            "var logo=h.querySelector('.brand');" +
            "var settings=h.querySelector('.settings-link');" +
            "if(!logo||!settings)return 'missing-controls';" +
            "var a=logo.getBoundingClientRect(),s=settings.getBoundingClientRect();" +
            "return (a.top>=" + cssTop + "&&s.top>=" + cssTop + ")?" +
                "'safe':'unsafe';})()";
        final WebView target = web;
        web.evaluateJavascript(script, result -> {
            if (!loaded || web != target || shelfRoot == null ||
                !"\"safe\"".equals(result)) return;
            if (!scrollSafeHeaderReady) {
                scrollSafeHeaderReady = true;
                updateNativeSafeArea();
            }
        });
    }

    private void startWake(JSONObject cmd) {
        String clientId=cmd.optString("client_id","");
        String account=cmd.optString("account_id","");
        String job=cmd.optString("job_id","");
        String bearer=cmd.optString("helper_token","");
        String registered=getSharedPreferences(PREFS,MODE_PRIVATE).getString("trusted_client","");
        if (!clientId.matches("[a-fA-F0-9]{32}") || !clientId.equals(registered) ||
            !(account.equals("owner")||account.matches("[a-f0-9]{32}")) ||
            !job.matches("[A-Za-z0-9_-]{20,128}") || !bearer.matches("[A-Za-z0-9_-]{32,128}")) {
            // Do not record any untrusted values from a rejected web message.
            return;
        }
        stopWake("Previous request replaced by a new playback attempt.");
        integratedWakeLog = new WakeLog();
        integratedWakeStartedAt = SystemClock.elapsedRealtime();
        integratedSessionId = WakeDiagnostics.sessionId();
        lastWakePhase = null;
        lastWakeState = null;
        lastWakeError = null;
        lastAcceptedPlay = false;
        wakePollCount = 0;
        wakeSdkState = "connecting";
        jobId=job; accountId=account; token=bearer;
        wakeDeadline=integratedWakeStartedAt+20000;
        final int ticket=sequence;
        wakeConnecting=true;
        recordIntegrated("Wake session " + integratedSessionId + " · integrated Android app "
            + BuildConfig.VERSION_NAME + " (build " + BuildConfig.VERSION_CODE + ")"
            + "; Android API " + Build.VERSION.SDK_INT + ".");
        recordIntegrated("Time zone " + java.time.ZoneId.systemDefault().getId()
            + "; absolute timestamps and monotonic elapsed seconds enabled.");
        recordIntegrated("Trusted wake request validated; credentials, account and full job ID omitted.");
        recordIntegrated("Spotify App Remote connects without sending Play; server owns device/track selection and verification.");
        handler.postDelayed(() -> {
            if(ticket==sequence)stopWake("20-second deadline; server phase="
                + (lastWakePhase==null?"not yet received":lastWakePhase)
                + "; Spotify SDK=" + wakeSdkState
                + "; Play accepted=" + lastAcceptedPlay
                + ". Verified playback was not reported before this deadline.");
        },20000);
        pollWake();
        if (!SpotifyAppRemote.isSpotifyInstalled(this)) {
            recordIntegrated("Spotify package is not installed; cannot wake App Remote.");
            Toast.makeText(this,"Spotify is not installed.",Toast.LENGTH_SHORT).show();
            stopWake("Spotify missing; no local SDK wake possible.");
            return;
        }
        try {
            recordIntegrated("Spotify installed: " + getPackageManager()
                .getPackageInfo("com.spotify.music",0).versionName + ".");
        } catch(PackageManager.NameNotFoundException ignored) {
            recordIntegrated("Spotify version unavailable.");
        }
        recordIntegrated("Starting foreground-visible App Remote service wake; authorisation UI disabled.");
        handler.postDelayed(() -> {
            if(ticket==sequence && wakeConnecting && remote==null) {
                recordIntegrated("Spotify SDK watchdog: no success/failure callback after 10 seconds."
                    + " This is an App Remote connection issue even if Spotify Connect sees the phone.");
            }
        },10000);
        ConnectionParams params = new ConnectionParams.Builder(clientId)
            .setRedirectUri(SpotifyRemoteAuthPolicy.REDIRECT_URI).showAuthView(false).build();
        try {
            SpotifyAppRemote.connect(this,params,new Connector.ConnectionListener() {
                @Override public void onConnected(SpotifyAppRemote appRemote) {
                    handler.post(() -> {
                        if(ticket!=sequence) {SpotifyAppRemote.disconnect(appRemote);return;}
                        remote=appRemote;
                        wakeConnecting=false;
                        wakeSdkState="connected";
                        recordIntegrated("Spotify App Remote SDK connected; local service awake. Playback NOT yet confirmed.");
                    });
                }
                @Override public void onFailure(Throwable error) {
                    handler.post(() -> {
                        if(ticket!=sequence)return;
                        wakeConnecting=false;
                        wakeSdkState="failed: "+WakeDiagnostics.failure(error);
                        recordIntegrated("Spotify SDK connection failed: "+WakeDiagnostics.failure(error)
                            + ". This alone does not prove the server playback job failed.");
                    });
                }
            });
        } catch (RuntimeException error) {
            wakeConnecting=false;
            wakeSdkState="exception: "+WakeDiagnostics.failure(error);
            recordIntegrated("Spotify SDK connection threw: "+WakeDiagnostics.failure(error));
        }
    }

    private void recordIntegrated(String event) {
        if (integratedSessionId == null) return;
        integratedWakeLog.append(WakeDiagnostics.line(
            SystemClock.elapsedRealtime()-integratedWakeStartedAt,event));
        getSharedPreferences(PREFS,MODE_PRIVATE).edit()
            .putString("last_integrated_wake_log",integratedWakeLog.toString()).apply();
    }

    private void pollWake() {
        if(jobId==null)return;
        if(SystemClock.elapsedRealtime()>=wakeDeadline) {
            stopWake("20-second polling deadline reached without a verified terminal status.");
            return;
        }
        final int ticket=sequence;
        final int attempt=++wakePollCount;
        final String job=jobId, account=accountId, bearer=token, origin=trustedOrigin;
        if(attempt==1)recordIntegrated("Server playback verification polling started (1 request at a time).");
        executor.execute(() -> {
            PlaybackStatusClient.Result state=null;
            IOException failure=null;
            try { state=PlaybackStatusClient.poll(origin,account,job,bearer,stage -> {}); }
            catch(IOException error) {failure=error;}
            final PlaybackStatusClient.Result result=state;
            final IOException failed=failure;
            handler.post(() -> {
                if(ticket!=sequence)return;
                if(failed!=null) {
                    String description=PlaybackStatusClient.describe(failed);
                    if(!description.equals(lastWakeError)) {
                        lastWakeError=description;
                        recordIntegrated("Server check "+attempt+" failed: "+description
                            + "; retrying while wake window remains.");
                    }
                } else if(result!=null) {
                    if(lastWakeError!=null) {
                        recordIntegrated("Server connection restored at check "+attempt+".");
                        lastWakeError=null;
                    }
                    boolean phaseChanged=!result.phase.equals(lastWakePhase)
                        || !result.state.equals(lastWakeState)
                        || lastAcceptedPlay!=result.playAccepted;
                    if(phaseChanged || integratedProgressLog.shouldRecord(result.state,
                        result.phase,result.playAccepted,result.deviceSeenMs,
                        SystemClock.elapsedRealtime()-integratedWakeStartedAt)) {
                        lastWakePhase=result.phase;
                        lastWakeState=result.state;
                        lastAcceptedPlay=result.playAccepted;
                        recordIntegrated("Server check "+attempt+": state="+result.state
                            + ", phase="+result.phase+", Play accepted="+result.playAccepted
                            + ", HTTP "+result.httpStatus+". "+result.progress()+".");
                    }
                    if(!"waiting".equals(result.state)) {
                        // HTTP Play acknowledgement or device discovery alone is
                        // never proof of the exact requested track playing.
                        stopWake("Server terminal="+result.state
                            + ("started".equals(result.state)
                                ? ": correct phone and first track CONFIRMED playing."
                                : ": requested playback NOT confirmed.")
                            + " Spotify SDK="+wakeSdkState+".");
                        return;
                    }
                }
                handler.postDelayed(poll,500);
            });
        });
    }

    private void stopWake() {
        stopWake("Wake cancelled or Android activity closed.");
    }
    private void stopWake(String reason) {
        if(jobId!=null) {
            recordIntegrated(reason);
            recordIntegrated("Disconnecting Spotify SDK="
                + (remote!=null?"connected":wakeSdkState)
                + "; AudioShelf server may continue its independent verification.");
        }
        sequence++;jobId=null;accountId=null;token=null;wakeConnecting=false;
        handler.removeCallbacks(poll);
        if(remote!=null){SpotifyAppRemote.disconnect(remote);remote=null;}
    }

    void onSpotifyMediaChanged(String title,String artist,boolean playing) {
        if(web==null || !loaded) return;
        // A signal to refresh authoritative server state, not a source of track truth.
        web.evaluateJavascript("window.dispatchEvent(new CustomEvent('audioshelf-native-playback'));",null);
    }

    private void openMediaPermission() {
        new AlertDialog.Builder(this).setTitle("Spotify playback awareness")
            .setMessage("Optionally allow AudioShelf to observe Spotify's Android media session so it can refresh Now Playing quickly. This permission grants notification access. AudioShelf reads only Spotify media-session status.")
            .setPositiveButton("Open Android settings",(d,w)->{
                Intent intent=new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS);
                try {startActivity(intent);}catch(ActivityNotFoundException ignored){}
            }).setNegativeButton("Cancel",null).show();
    }

    private void showNativeMenu() {
        PopupMenu menu=new PopupMenu(this,options);
        boolean enabled=getSharedPreferences(PREFS,MODE_PRIVATE).getBoolean("playback_notifications",false);
        menu.getMenu().add("Refresh AudioShelf");
        menu.getMenu().add("Check for Android updates");
        menu.getMenu().add("Playback diagnostics / Copy log");
        menu.getMenu().add("Pair or reconnect Spotify");
        menu.getMenu().add("Enable Spotify playback awareness…");
        menu.getMenu().add(enabled?"Disable playback notifications":"Enable playback notifications");
        menu.getMenu().add("Use companion mode");
        menu.setOnMenuItemClickListener(item -> {
            String choice=String.valueOf(item.getTitle());
            if(choice.equals("Refresh AudioShelf")) web.reload();
            else if(choice.equals("Check for Android updates")) showUpdater();
            else if(choice.equals("Playback diagnostics / Copy log")) showPlaybackDiagnostics();
            else if(choice.equals("Pair or reconnect Spotify"))
                startActivity(new Intent(this, MainActivity.class).putExtra("force_companion", true));
            else if(choice.startsWith("Enable Spotify playback awareness")) openMediaPermission();
            else if(choice.contains("playback notifications")) changeNotifications(!enabled);
            else if(choice.equals("Use companion mode")) {
                getSharedPreferences(PREFS,MODE_PRIVATE).edit().putBoolean("integrated_mode",false).apply();
                stopWake();startActivity(new Intent(this,MainActivity.class));finish();
            }
            return true;
        });
        menu.show();
    }
    /** Accessible directly in integrated mode, including after the wake ends.
     * The companion's previous trace remains readable without mode switching. */
    private void showPlaybackDiagnostics() {
        String recentIntegrated = getSharedPreferences(PREFS,MODE_PRIVATE)
            .getString("last_integrated_wake_log","No integrated-app wake recorded yet.");
        String recentCompanion = getSharedPreferences(PREFS,MODE_PRIVATE)
            .getString("last_wake_log","No companion wake recorded yet.");
        String recentPairing = getSharedPreferences(PREFS,MODE_PRIVATE)
            .getString("last_pairing_log","No companion pairing recorded yet.");
        String body = "Installed Android app: "+BuildConfig.VERSION_NAME
            +" (build "+BuildConfig.VERSION_CODE+")"
            +"\nAll times are local and include UTC offset; +seconds are elapsed within an attempt."
            +"\nServer Play acceptance is NOT verified playback."
            +"\nNo tokens, OAuth URLs or full playback job identifiers are saved."
            +"\n\nINTEGRATED APP WAKE\n"+recentIntegrated
            +"\nCOMPANION WAKE\n"+recentCompanion
            +"\nPAIRING\n"+recentPairing;
        TextView content=new TextView(this);
        content.setText(body);
        content.setTextSize(12);
        content.setTextIsSelectable(true);
        content.setPadding(dp(14),dp(12),dp(14),dp(12));
        ScrollView scroll=new ScrollView(this);
        scroll.addView(content);
        new AlertDialog.Builder(this)
            .setTitle("Spotify playback diagnostics")
            .setView(scroll)
            .setPositiveButton("Copy log",(dialog,which)->{
                ClipboardManager clipboard=(ClipboardManager)getSystemService(CLIPBOARD_SERVICE);
                clipboard.setPrimaryClip(ClipData.newPlainText("AudioShelf playback diagnostics",body));
                Toast.makeText(this,"Playback diagnostics copied.",Toast.LENGTH_SHORT).show();
            })
            .setNeutralButton("Refresh",(dialog,which)->showPlaybackDiagnostics())
            .setNegativeButton("Close",null)
            .show();
    }

    private void showUpdater() {
        if (updater == null || updaterStatus == null) return;
        if (updaterStatus.getParent() instanceof ViewGroup)
            ((ViewGroup) updaterStatus.getParent()).removeView(updaterStatus);
        // Use one focused dialog throughout the check. The older implementation
        // opened a status dialog, then tried to open a second confirmation only
        // if Activity.hasWindowFocus() was true. Dialog focus made that false.
        AlertDialog dialog = new AlertDialog.Builder(this)
            .setTitle("AudioShelf Android update")
            .setView(updaterStatus)
            .setPositiveButton("Download", null)
            .setNegativeButton("Close", null)
            .create();
        dialog.show();
        android.widget.Button download = dialog.getButton(AlertDialog.BUTTON_POSITIVE);
        download.setEnabled(false);
        updater.check((version, action) -> {
            // A check may finish after the user has closed this dialog.
            if (!dialog.isShowing() || isFinishing() || isDestroyed()) return;
            download.setText("Download " + version);
            download.setEnabled(true);
            download.setOnClickListener(button -> {
                // Dismiss before downloading so the Activity regains focus
                // and the existing signed-APK installer can open normally.
                dialog.dismiss();
                action.run();
            });
        });
    }

    private void changeNotifications(boolean enabled) {
        if(enabled && Build.VERSION.SDK_INT>=33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},19);
            return;
        }
        getSharedPreferences(PREFS,MODE_PRIVATE).edit().putBoolean("playback_notifications",enabled).apply();
        if(!enabled) SpotifyMediaListener.dismiss(this);
        Toast.makeText(this,enabled?"Playback notifications enabled":"Playback notifications disabled",Toast.LENGTH_SHORT).show();
    }
    @Override public void onRequestPermissionsResult(int request,String[] names,int[] results){
        super.onRequestPermissionsResult(request,names,results);
        if(request==19 && results.length>0 && results[0]==PackageManager.PERMISSION_GRANTED) changeNotifications(true);
    }
    @Override public void onWindowFocusChanged(boolean focused) {
        super.onWindowFocusChanged(focused);
        if (focused && updater != null) updater.onVisible();
    }
    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (updater != null) updater.activityResult(requestCode);
    }
    @Override public void onBackPressed() {
        if(web!=null && web.canGoBack())web.goBack();
        else if(web!=null)web.evaluateJavascript("location.hash='#shelf'",null);
        else super.onBackPressed();
    }
    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);setIntent(intent);
        if(web!=null && ACTION_SHELF.equals(intent.getAction()))web.evaluateJavascript("location.hash='#shelf'",null);
        if(web!=null && ACTION_NOW_PLAYING.equals(intent.getAction()))
            web.evaluateJavascript("location.hash='#shelf';window.dispatchEvent(new CustomEvent('audioshelf-native-playback'));",null);
    }
    @Override protected void onDestroy() {
        stopWake();SpotifyMediaListener.detach(this);
        if(updater != null) updater.close();
        executor.shutdownNow();
        if(web!=null){web.destroy();web=null;}
        super.onDestroy();
    }
}
