package uk.co.justcop.audioshelf.helper;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.widget.TextView;
import androidx.core.content.FileProvider;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** User-approved, same-package, same-signature updates. Never runs during waking. */
final class HelperUpdater {
    private final Activity activity;
    private final TextView status;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private boolean busy, closed, pendingInstall, checkingPermission;
    private File readyApk;
    private static final int INSTALL_PERMISSION = 7301;

    HelperUpdater(Activity activity, TextView status) { this.activity = activity; this.status = status; }

    void check() {
        if (busy || closed) return;
        busy = true;
        status.setText("Checking for helper updates…");
        worker.execute(() -> {
            try {
                PackageInfo installed = activity.getPackageManager().getPackageInfo(activity.getPackageName(), 0);
                HttpURLConnection connection = connect(UpdateInfo.MANIFEST_URL);
                if (connection.getResponseCode() == 404) {
                    connection.disconnect();
                    post(() -> { busy = false; status.setText("Helper " + installed.versionName + ". No published update is available yet."); });
                    return;
                }
                String json;
                try (InputStream in = input(connection); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
                    copy(in, out, 32768);
                    json = out.toString(StandardCharsets.UTF_8.name());
                } finally { connection.disconnect(); }
                UpdateInfo update = UpdateInfo.parse(json, code(installed));
                post(() -> {
                    busy = false;
                    if (update == null) status.setText("Helper " + installed.versionName + " is up to date.");
                    else {
                        status.setText("Helper " + update.versionName + " is available. Tap Check for updates to install.");
                        if (activity.hasWindowFocus()) new AlertDialog.Builder(activity)
                            .setTitle("Update AudioShelf helper?")
                            .setMessage("Download version " + update.versionName + "? Android will ask you to confirm installation.")
                            .setPositiveButton("Download", (dialog, which) -> download(update))
                            .setNegativeButton("Later", null).show();
                    }
                });
            } catch (Exception error) { post(() -> { busy = false; status.setText("Could not check for updates. Try again when online."); }); }
        });
    }

    private void download(UpdateInfo update) {
        if (busy || closed) return;
        busy = true;
        status.setText("Downloading helper " + update.versionName + "…");
        worker.execute(() -> {
            File temporary = null;
            try {
                File directory = new File(activity.getCacheDir(), "updates");
                if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("Update directory unavailable");
                temporary = new File(directory, "download.part");
                HttpURLConnection connection = connect(UpdateInfo.APK_URL);
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                long bytes = 0;
                try (InputStream in = input(connection); FileOutputStream out = new FileOutputStream(temporary)) {
                    byte[] buffer = new byte[16384]; int count;
                    while ((count = in.read(buffer)) != -1) {
                        if (Thread.currentThread().isInterrupted()) throw new IOException("Cancelled");
                        bytes += count;
                        if (bytes > update.size) throw new IOException("APK exceeds declared size");
                        digest.update(buffer, 0, count); out.write(buffer, 0, count);
                    }
                } finally { connection.disconnect(); }
                if (bytes != update.size || !hex(digest.digest()).equals(update.sha256)) throw new IOException("Download verification failed");
                verify(temporary, update);
                File apk = new File(directory, "AudioShelf-Helper.apk");
                if (apk.exists() && !apk.delete()) throw new IOException("Previous download unavailable");
                if (!temporary.renameTo(apk)) throw new IOException("Could not prepare APK");
                post(() -> {
                    busy = false; readyApk = apk; pendingInstall = true;
                    status.setText("Update verified. Confirm the Android installation.");
                    onVisible();
                });
            } catch (Exception error) {
                if (temporary != null) temporary.delete();
                post(() -> { busy = false; status.setText("Update could not be verified or downloaded. Your installed helper is unchanged. Try again."); });
            }
        });
    }

    private void verify(File apk, UpdateInfo update) throws Exception {
        PackageManager pm = activity.getPackageManager();
        int flags = Build.VERSION.SDK_INT >= 28 ? PackageManager.GET_SIGNING_CERTIFICATES : PackageManager.GET_SIGNATURES;
        PackageInfo candidate = pm.getPackageArchiveInfo(apk.getAbsolutePath(), flags);
        PackageInfo installed = pm.getPackageInfo(activity.getPackageName(), flags);
        if (candidate == null || !update.allowsCandidate(candidate.packageName, code(candidate), candidate.versionName,
                code(installed), signers(candidate), signers(installed))) {
            throw new IOException("APK package, version or signing key does not match");
        }
    }

    private static Set<String> signers(PackageInfo info) throws Exception {
        Signature[] signatures = Build.VERSION.SDK_INT >= 28
            ? (info.signingInfo == null ? null : info.signingInfo.getApkContentsSigners()) : info.signatures;
        if (signatures == null || signatures.length == 0) throw new IOException("Missing APK signer");
        Set<String> result = new HashSet<>();
        for (Signature signature : signatures) result.add(hex(MessageDigest.getInstance("SHA-256").digest(signature.toByteArray())));
        return result;
    }

    private static long code(PackageInfo info) { return Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode; }

    void onVisible() {
        if (closed || !pendingInstall || readyApk == null || !activity.hasWindowFocus() || checkingPermission) return;
        try {
            if (!activity.getPackageManager().canRequestPackageInstalls()) {
                checkingPermission = true;
                status.setText("Allow this helper to install updates, then return here.");
                activity.startActivityForResult(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + activity.getPackageName())), INSTALL_PERMISSION);
                return;
            }
            pendingInstall = false;
            Uri uri = FileProvider.getUriForFile(activity, activity.getPackageName() + ".updates", readyApk);
            Intent install = new Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            activity.startActivity(install);
            status.setText("Follow Android's install confirmation. If cancelled, tap Check for updates to retry.");
        } catch (ActivityNotFoundException | SecurityException error) {
            pendingInstall = false; checkingPermission = false;
            status.setText("Android could not open the installer. Try Check for updates again.");
        }
    }

    boolean activityResult(int request) {
        if (request != INSTALL_PERMISSION) return false;
        checkingPermission = false;
        if (!activity.getPackageManager().canRequestPackageInstalls()) {
            pendingInstall = false;
            status.setText("Install permission was not enabled. Tap Check for updates to retry.");
        }
        return true;
    }

    private void post(Runnable action) { main.post(() -> { if (!closed && !activity.isFinishing() && !activity.isDestroyed()) action.run(); }); }
    void close() { closed = true; main.removeCallbacksAndMessages(null); worker.shutdownNow(); }

    private static HttpURLConnection connect(String address) throws IOException {
        URL url = new URL(address);
        for (int hop = 0; hop < 6; hop++) {
            String host = url.getHost();
            if (!"https".equals(url.getProtocol()) || url.getUserInfo() != null || url.getPort() != -1
                    || !(host.equals("github.com") || host.equals("release-assets.githubusercontent.com")
                    || host.equals("objects.githubusercontent.com") || host.equals("github-releases.githubusercontent.com"))) {
                throw new IOException("Untrusted update redirect");
            }
            HttpURLConnection connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(15000); connection.setReadTimeout(20000);
            connection.setInstanceFollowRedirects(false);
            connection.setRequestProperty("User-Agent", "AudioShelf-Android-Helper");
            connection.setRequestProperty("Cache-Control", "no-cache");
            int status = connection.getResponseCode();
            if (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
                String location = connection.getHeaderField("Location"); connection.disconnect();
                if (location == null) throw new IOException("Missing update redirect");
                url = new URL(url, location);
            } else return connection;
        }
        throw new IOException("Too many update redirects");
    }

    private static InputStream input(HttpURLConnection connection) throws IOException {
        if (connection.getResponseCode() != 200) throw new IOException("Update download unavailable");
        return connection.getInputStream();
    }

    private static void copy(InputStream in, ByteArrayOutputStream out, int limit) throws IOException {
        byte[] buffer = new byte[4096]; int count;
        while ((count = in.read(buffer)) != -1) {
            if (out.size() + count > limit) throw new IOException("Update metadata too large");
            out.write(buffer, 0, count);
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) result.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
        return result.toString();
    }
}
